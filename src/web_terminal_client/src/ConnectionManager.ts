/**
 * ConnectionManager - Centralized connection state machine for resilient session management.
 *
 * States:
 *   DISCONNECTED → CONNECTING → CONNECTED ⟷ RECONNECTING → POLLING ⟷ CONNECTED
 *                                                ↓
 *                                           DEGRADED (background retry continues)
 *
 * Features:
 * - Never fully gives up - always maintains background recovery attempts
 * - Exponential backoff with jitter, capped at 30 seconds
 * - Periodic SSE upgrade attempts from polling mode (every 60s)
 * - Heartbeat timeout detection (45s without any event/heartbeat = stale)
 * - Event deduplication via sequence numbers
 */

import type { SSEEvent, TerminalEvent } from './types';

export type ConnectionState =
  | 'disconnected'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'polling'
  | 'degraded';

export interface HeartbeatData {
  session_status?: string;
  server_time?: string;
  redis_ok?: boolean;
  last_sequence?: number;
}

export interface ConnectionManagerConfig {
  baseUrl: string;
  sessionId: string;
  token: string;
  lastSequence: number | null;
  onEvent: (event: TerminalEvent) => void;
  onStateChange: (state: ConnectionState, info?: { attempt?: number; message?: string }) => void;
  onError: (error: Error) => void;
  onHeartbeat?: (data: HeartbeatData) => void;
}

export class ConnectionManager {
  private config: ConnectionManagerConfig;
  private state: ConnectionState = 'disconnected';
  private sseSource: EventSource | null = null;
  private reconnectAttempts = 0;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pollInterval: ReturnType<typeof setInterval> | null = null;
  private upgradeTimer: ReturnType<typeof setInterval> | null = null;
  private lastEventTime = Date.now();
  private seenSequences = new Set<number>();
  private lastSequence: number | null;
  private isClosed = false;
  private isTerminal = false;

  // Configuration constants
  private readonly HEARTBEAT_TIMEOUT_MS = 45000;
  private readonly MAX_BACKOFF_MS = 30000;
  private readonly INITIAL_BACKOFF_MS = 1000;
  private readonly POLL_INTERVAL_MS = 4000;
  private readonly SSE_UPGRADE_INTERVAL_MS = 60000;
  private readonly TERMINAL_EVENTS = ['agent_complete', 'error', 'cancelled'];

  constructor(config: ConnectionManagerConfig) {
    this.config = config;
    this.lastSequence = config.lastSequence;
  }

  /**
   * Start the connection - attempts SSE first.
   */
  connect(): void {
    if (this.isClosed || this.isTerminal) return;

    this.setState('connecting');
    this.connectSSE();
  }

  /**
   * Clean shutdown - stops all timers and connections.
   */
  disconnect(): void {
    this.isClosed = true;
    this.clearAllTimers();
    this.closeSSE();
    this.setState('disconnected');
  }

  /**
   * Get current connection state.
   */
  getState(): ConnectionState {
    return this.state;
  }

  /**
   * Get current reconnection attempt count.
   */
  getReconnectAttempts(): number {
    return this.reconnectAttempts;
  }

  /**
   * Force an SSE upgrade attempt (useful for manual retry).
   */
  forceSSEUpgrade(): void {
    if (this.isClosed || this.isTerminal) return;
    if (this.state === 'polling' || this.state === 'degraded') {
      this.reconnectAttempts = 0; // Reset for fresh attempt
      this.stopPolling();
      this.connectSSE();
    }
  }

  // ============================================================================
  // Private: SSE Connection
  // ============================================================================

  private connectSSE(): void {
    if (this.isClosed || this.isTerminal) return;

    this.closeSSE();
    this.setState('connecting');

    const url = this.buildSSEUrl();
    this.sseSource = new EventSource(url);

    this.sseSource.onopen = () => {
      this.reconnectAttempts = 0;
      this.setState('connected');
      this.resetHeartbeatTimer();

      // Stop polling if we were in polling mode
      this.stopPolling();
      this.stopUpgradeTimer();
    };

    this.sseSource.onmessage = (event) => {
      this.handleSSEMessage(event);
    };

    this.sseSource.onerror = () => {
      this.handleSSEError();
    };
  }

  private handleSSEMessage(event: MessageEvent): void {
    this.lastEventTime = Date.now();
    this.resetHeartbeatTimer();

    try {
      const parsed = JSON.parse(event.data);

      // Handle heartbeat events (enhanced heartbeats with session state)
      if (parsed.type === 'heartbeat') {
        this.config.onHeartbeat?.(parsed.data as HeartbeatData);
        return;
      }

      const sseEvent = parsed as SSEEvent;
      const seq = sseEvent.sequence;

      // Update last event ID from SSE header or event sequence
      if (event.lastEventId) {
        this.lastSequence = parseInt(event.lastEventId, 10);
      } else if (seq !== undefined) {
        this.lastSequence = seq;
      }

      // Deduplicate by sequence number
      if (seq !== undefined && this.seenSequences.has(seq)) {
        return;
      }
      if (seq !== undefined) {
        this.seenSequences.add(seq);

        // Keep seenSequences bounded (last 1000 sequences)
        if (this.seenSequences.size > 1000) {
          const arr = Array.from(this.seenSequences).sort((a, b) => a - b);
          for (let i = 0; i < 500; i++) {
            this.seenSequences.delete(arr[i]);
          }
        }
      }

      // Emit event to handler
      this.config.onEvent(sseEvent as TerminalEvent);

      // Check for terminal events
      if (this.TERMINAL_EVENTS.includes(sseEvent.type)) {
        this.isTerminal = true;
        this.clearAllTimers();
        this.closeSSE();
        this.setState('disconnected');
      }
    } catch (err) {
      this.config.onError(new Error('Failed to parse SSE payload'));
    }
  }

  private handleSSEError(): void {
    this.closeSSE();

    // If already terminal, this is expected
    if (this.isTerminal || this.isClosed) {
      return;
    }

    this.reconnectAttempts++;
    this.setState('reconnecting', {
      attempt: this.reconnectAttempts,
      message: `Reconnecting (attempt ${this.reconnectAttempts})...`,
    });

    // Schedule reconnection with exponential backoff
    this.scheduleReconnect();
  }

  private closeSSE(): void {
    if (this.sseSource) {
      this.sseSource.close();
      this.sseSource = null;
    }
  }

  // ============================================================================
  // Private: Reconnection Logic
  // ============================================================================

  private scheduleReconnect(): void {
    if (this.isClosed || this.isTerminal) return;

    const delay = this.getBackoffDelay();

    // After many failed SSE attempts, switch to polling with periodic SSE upgrade attempts
    if (this.reconnectAttempts > 5) {
      this.setState('polling', { message: 'Switched to polling mode' });
      this.startPolling();
      this.startUpgradeTimer();
      return;
    }

    this.reconnectTimer = setTimeout(() => {
      this.connectSSE();
    }, delay);
  }

  /**
   * Calculate backoff delay with exponential increase, cap, and jitter.
   */
  private getBackoffDelay(): number {
    const exponential = this.INITIAL_BACKOFF_MS * Math.pow(2, Math.min(this.reconnectAttempts - 1, 10));
    const capped = Math.min(exponential, this.MAX_BACKOFF_MS);
    // Add ±20% jitter to prevent thundering herd
    const jitter = capped * 0.2 * (Math.random() - 0.5) * 2;
    return Math.max(100, capped + jitter);
  }

  // ============================================================================
  // Private: Polling Fallback
  // ============================================================================

  private startPolling(): void {
    if (this.pollInterval) return;

    // Initial poll immediately
    void this.pollEvents();

    this.pollInterval = setInterval(() => {
      void this.pollEvents();
    }, this.POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  private async pollEvents(): Promise<void> {
    if (this.isClosed || this.isTerminal) return;

    const params = new URLSearchParams({ token: this.config.token });
    if (this.lastSequence !== null) {
      params.set('after', String(this.lastSequence));
    }
    const url = `${this.config.baseUrl}/api/v1/sessions/${this.config.sessionId}/events/history?${params.toString()}`;

    try {
      const response = await fetch(url);

      if (!response.ok) {
        // Polling failed - switch to degraded mode but keep trying
        if (this.state !== 'degraded') {
          this.setState('degraded', { message: `Polling failed (${response.status})` });
        }
        return;
      }

      // Polling succeeded - update state if we were degraded
      if (this.state === 'degraded') {
        this.setState('polling');
      }

      const events = (await response.json()) as SSEEvent[];
      for (const event of events) {
        const seq = event.sequence;

        // Update last sequence
        if (seq !== undefined) {
          this.lastSequence = Math.max(this.lastSequence ?? 0, seq);
        }

        // Deduplicate
        if (seq !== undefined && this.seenSequences.has(seq)) {
          continue;
        }
        if (seq !== undefined) {
          this.seenSequences.add(seq);
        }

        this.config.onEvent(event as TerminalEvent);

        // Check for terminal events
        if (this.TERMINAL_EVENTS.includes(event.type)) {
          this.isTerminal = true;
          this.clearAllTimers();
          this.setState('disconnected');
          return;
        }
      }
    } catch (err) {
      // Network error during polling
      if (this.state !== 'degraded') {
        this.setState('degraded', { message: 'Network error during polling' });
      }
      this.config.onError(err instanceof Error ? err : new Error('Polling failed'));
    }
  }

  // ============================================================================
  // Private: SSE Upgrade from Polling
  // ============================================================================

  private startUpgradeTimer(): void {
    if (this.upgradeTimer) return;

    this.upgradeTimer = setInterval(() => {
      this.attemptSSEUpgrade();
    }, this.SSE_UPGRADE_INTERVAL_MS);
  }

  private stopUpgradeTimer(): void {
    if (this.upgradeTimer) {
      clearInterval(this.upgradeTimer);
      this.upgradeTimer = null;
    }
  }

  private attemptSSEUpgrade(): void {
    if (this.isClosed || this.isTerminal) return;
    if (this.state !== 'polling' && this.state !== 'degraded') return;

    // Keep polling running during upgrade attempt
    this.reconnectAttempts = 0;
    this.connectSSE();

    // If SSE connects successfully, onopen will stop polling
    // If SSE fails, onerror will schedule reconnect (but polling continues)
  }

  // ============================================================================
  // Private: Heartbeat Timeout Detection
  // ============================================================================

  private resetHeartbeatTimer(): void {
    if (this.heartbeatTimer) {
      clearTimeout(this.heartbeatTimer);
    }

    if (this.isClosed || this.isTerminal) return;

    this.heartbeatTimer = setTimeout(() => {
      this.handleHeartbeatTimeout();
    }, this.HEARTBEAT_TIMEOUT_MS);
  }

  private handleHeartbeatTimeout(): void {
    if (this.isClosed || this.isTerminal) return;

    // Connection appears stale - trigger reconnection
    console.warn('[ConnectionManager] Heartbeat timeout - connection stale, reconnecting...');

    this.closeSSE();
    this.reconnectAttempts++;
    this.setState('reconnecting', {
      attempt: this.reconnectAttempts,
      message: 'Connection stale, reconnecting...',
    });
    this.scheduleReconnect();
  }

  // ============================================================================
  // Private: Utilities
  // ============================================================================

  private buildSSEUrl(): string {
    const params = new URLSearchParams({ token: this.config.token });
    if (this.lastSequence !== null) {
      params.set('after', String(this.lastSequence));
    }
    return `${this.config.baseUrl}/api/v1/sessions/${this.config.sessionId}/events?${params.toString()}`;
  }

  private setState(newState: ConnectionState, info?: { attempt?: number; message?: string }): void {
    if (this.state === newState) return;

    const prevState = this.state;
    this.state = newState;

    console.log(`[ConnectionManager] State: ${prevState} → ${newState}`, info || '');

    this.config.onStateChange(newState, info);
  }

  private clearAllTimers(): void {
    if (this.heartbeatTimer) {
      clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
    if (this.upgradeTimer) {
      clearInterval(this.upgradeTimer);
      this.upgradeTimer = null;
    }
  }
}
