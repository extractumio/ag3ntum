import type { SSEEvent } from './types';

// Configuration constants
const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_BACKOFF_MS = 30000;
const POLL_INTERVAL_MS = 4000;
const HEARTBEAT_TIMEOUT_MS = 45000;
const SSE_UPGRADE_INTERVAL_MS = 60000;

// Terminal events that end the session
const TERMINAL_EVENTS = ['agent_complete', 'error', 'cancelled'];

export interface HeartbeatData {
  session_status?: string;
  server_time?: string;
  redis_ok?: boolean;
  last_sequence?: number;
}

export interface ConnectSSEOptions {
  baseUrl: string;
  sessionId: string;
  token: string;
  onEvent: (event: SSEEvent) => void;
  onError: (error: Error) => void;
  onReconnecting?: (attempt: number) => void;
  onHeartbeat?: (data: HeartbeatData) => void;
  onConnectionStateChange?: (state: 'connected' | 'reconnecting' | 'polling' | 'degraded') => void;
  initialLastEventId?: string | number | null;
}

/**
 * Connect to SSE stream with resilient reconnection logic.
 *
 * Features:
 * - Exponential backoff with jitter (capped at 30s)
 * - Automatic fallback to polling after SSE failures
 * - Periodic SSE upgrade attempts from polling mode
 * - Heartbeat timeout detection (45s without data = stale)
 * - Event deduplication by sequence number
 */
export function connectSSE(
  baseUrl: string,
  sessionId: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onReconnecting?: (attempt: number) => void,
  initialLastEventId?: string | number | null,
  onHeartbeat?: (data: HeartbeatData) => void,
  onConnectionStateChange?: (state: 'connected' | 'reconnecting' | 'polling' | 'degraded') => void
): () => void {
  let source: EventSource | null = null;
  let reconnectAttempts = 0;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let heartbeatTimeout: ReturnType<typeof setTimeout> | null = null;
  let pollInterval: ReturnType<typeof setInterval> | null = null;
  let upgradeInterval: ReturnType<typeof setInterval> | null = null;
  let isClosed = false;
  let isTerminal = false;
  let lastEventId: string | null = initialLastEventId ? String(initialLastEventId) : null;
  let connectionState: 'connected' | 'reconnecting' | 'polling' | 'degraded' = 'reconnecting';
  const seenSequences = new Set<number>();

  function setConnectionState(state: 'connected' | 'reconnecting' | 'polling' | 'degraded') {
    if (connectionState !== state) {
      connectionState = state;
      onConnectionStateChange?.(state);
    }
  }

  function buildUrl(): string {
    const params = new URLSearchParams({ token });
    if (lastEventId) {
      params.set('after', lastEventId);
    }
    return `${baseUrl}/api/v1/sessions/${sessionId}/events?${params.toString()}`;
  }

  function getBackoffDelay(): number {
    const exponential = INITIAL_RECONNECT_DELAY_MS * Math.pow(2, Math.min(reconnectAttempts - 1, 10));
    const capped = Math.min(exponential, MAX_BACKOFF_MS);
    // Add ±20% jitter to prevent thundering herd
    const jitter = capped * 0.2 * (Math.random() - 0.5) * 2;
    return Math.max(100, capped + jitter);
  }

  function resetHeartbeatTimeout() {
    if (heartbeatTimeout) {
      clearTimeout(heartbeatTimeout);
    }
    if (isClosed || isTerminal) return;

    heartbeatTimeout = setTimeout(() => {
      // Connection is stale - reconnect
      console.warn('[SSE] Heartbeat timeout - connection stale, reconnecting...');
      source?.close();
      reconnectAttempts++;
      setConnectionState('reconnecting');
      onReconnecting?.(reconnectAttempts);
      scheduleReconnect();
    }, HEARTBEAT_TIMEOUT_MS);
  }

  async function pollEvents(): Promise<void> {
    if (isClosed || isTerminal) return;

    const params = new URLSearchParams({ token });
    if (lastEventId) {
      params.set('after', lastEventId);
    }
    const url = `${baseUrl}/api/v1/sessions/${sessionId}/events/history?${params.toString()}`;

    try {
      const response = await fetch(url);
      if (!response.ok) {
        if (connectionState !== 'degraded') {
          setConnectionState('degraded');
        }
        return;
      }

      // Polling succeeded
      if (connectionState === 'degraded') {
        setConnectionState('polling');
      }

      const events = (await response.json()) as SSEEvent[];
      for (const event of events) {
        const seq = event.sequence;

        // Update last event ID
        if (seq !== undefined) {
          lastEventId = String(Math.max(parseInt(lastEventId || '0', 10), seq));
        }

        // Deduplicate
        if (seq !== undefined && seenSequences.has(seq)) {
          continue;
        }
        if (seq !== undefined) {
          seenSequences.add(seq);
          // Keep set bounded
          if (seenSequences.size > 1000) {
            const arr = Array.from(seenSequences).sort((a, b) => a - b);
            for (let i = 0; i < 500; i++) {
              seenSequences.delete(arr[i]);
            }
          }
        }

        onEvent(event);

        if (TERMINAL_EVENTS.includes(event.type)) {
          isTerminal = true;
          cleanup();
          return;
        }
      }
    } catch (error) {
      if (connectionState !== 'degraded') {
        setConnectionState('degraded');
      }
      onError(error instanceof Error ? error : new Error('Polling failed'));
    }
  }

  function startPolling() {
    if (pollInterval) return;

    setConnectionState('polling');

    // Start periodic SSE upgrade attempts
    if (!upgradeInterval) {
      upgradeInterval = setInterval(() => {
        attemptSSEUpgrade();
      }, SSE_UPGRADE_INTERVAL_MS);
    }

    pollInterval = setInterval(() => {
      void pollEvents();
    }, POLL_INTERVAL_MS);
    void pollEvents();
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  function stopUpgradeTimer() {
    if (upgradeInterval) {
      clearInterval(upgradeInterval);
      upgradeInterval = null;
    }
  }

  function attemptSSEUpgrade() {
    if (isClosed || isTerminal) return;
    if (connectionState !== 'polling' && connectionState !== 'degraded') return;

    // Try SSE again (keep polling running during attempt)
    reconnectAttempts = 0;
    connect();
  }

  function scheduleReconnect() {
    if (isClosed || isTerminal) return;

    const delay = getBackoffDelay();

    // After many failed SSE attempts, switch to polling
    if (reconnectAttempts > 5) {
      startPolling();
      return;
    }

    reconnectTimeout = setTimeout(connect, delay);
  }

  function connect() {
    if (isClosed || isTerminal) return;

    // Close existing source if any
    source?.close();

    const url = buildUrl();
    source = new EventSource(url);

    source.onopen = () => {
      reconnectAttempts = 0;
      setConnectionState('connected');
      resetHeartbeatTimeout();

      // Stop polling if we were in polling mode
      stopPolling();
      stopUpgradeTimer();
    };

    source.onmessage = (event) => {
      resetHeartbeatTimeout();

      try {
        const parsed = JSON.parse(event.data);

        // Handle heartbeat events
        if (parsed.type === 'heartbeat') {
          onHeartbeat?.(parsed.data as HeartbeatData);
          return;
        }

        // Handle infrastructure error events (don't deduplicate, always show)
        if (parsed.type === 'infrastructure_error') {
          console.warn('[SSE] Infrastructure error:', parsed.data);
          // Emit as regular event so UI can show warning
          onEvent(parsed as SSEEvent);
          return;
        }

        const sseEvent = parsed as SSEEvent;
        const seq = sseEvent.sequence;

        // Update last event ID
        lastEventId = event.lastEventId || String(seq ?? lastEventId ?? '');

        // Deduplicate
        if (seq !== undefined && seenSequences.has(seq)) {
          return;
        }
        if (seq !== undefined) {
          seenSequences.add(seq);
          if (seenSequences.size > 1000) {
            const arr = Array.from(seenSequences).sort((a, b) => a - b);
            for (let i = 0; i < 500; i++) {
              seenSequences.delete(arr[i]);
            }
          }
        }

        onEvent(sseEvent);

        // Stop reconnecting on terminal events
        if (TERMINAL_EVENTS.includes(sseEvent.type)) {
          isTerminal = true;
          cleanup();
        }
      } catch (error) {
        onError(new Error('Failed to parse SSE payload'));
      }
    };

    source.onerror = () => {
      source?.close();

      // If already marked as closed (terminal event received), this is expected
      if (isClosed || isTerminal) {
        return;
      }

      reconnectAttempts++;
      setConnectionState('reconnecting');
      onReconnecting?.(reconnectAttempts);
      scheduleReconnect();
    };
  }

  function cleanup() {
    isClosed = true;
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }
    if (heartbeatTimeout) {
      clearTimeout(heartbeatTimeout);
      heartbeatTimeout = null;
    }
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
    if (upgradeInterval) {
      clearInterval(upgradeInterval);
      upgradeInterval = null;
    }
    source?.close();
    source = null;
  }

  connect();

  return cleanup;
}

/**
 * User-level events for cross-session updates.
 */
export interface UserEvent {
  type: 'session_list_update' | 'session_status_change' | 'heartbeat';
  data: Record<string, unknown>;
  timestamp: string;
}

export interface UserEventsSSEOptions {
  baseUrl: string;
  token: string;
  onEvent: (event: UserEvent) => void;
  onError: (error: Error) => void;
  onConnectionStateChange?: (state: 'connected' | 'reconnecting' | 'polling' | 'degraded') => void;
}

/**
 * Connect to user-level SSE stream for cross-session updates.
 *
 * Receives events for all user sessions:
 * - session_list_update: List of active/queued sessions changed
 * - session_status_change: A session's status changed
 * - heartbeat: Keep-alive
 *
 * Used by SessionListTab to show real-time badges and status updates.
 */
export function connectUserEventsSSE(options: UserEventsSSEOptions): () => void {
  const { baseUrl, token, onEvent, onError, onConnectionStateChange } = options;

  let source: EventSource | null = null;
  let reconnectAttempts = 0;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let heartbeatTimeout: ReturnType<typeof setTimeout> | null = null;
  let pollInterval: ReturnType<typeof setInterval> | null = null;
  let isClosed = false;
  let connectionState: 'connected' | 'reconnecting' | 'polling' | 'degraded' = 'reconnecting';

  const USER_EVENTS_POLL_INTERVAL_MS = 5000; // Poll every 5s for user events
  const USER_EVENTS_HEARTBEAT_TIMEOUT_MS = 60000; // 60s heartbeat timeout

  function setConnectionState(state: 'connected' | 'reconnecting' | 'polling' | 'degraded') {
    if (connectionState !== state) {
      connectionState = state;
      onConnectionStateChange?.(state);
    }
  }

  function buildUrl(): string {
    return `${baseUrl}/api/v1/auth/me/events?token=${encodeURIComponent(token)}`;
  }

  function getBackoffDelay(): number {
    const exponential = INITIAL_RECONNECT_DELAY_MS * Math.pow(2, Math.min(reconnectAttempts - 1, 10));
    const capped = Math.min(exponential, MAX_BACKOFF_MS);
    const jitter = capped * 0.2 * (Math.random() - 0.5) * 2;
    return Math.max(100, capped + jitter);
  }

  function resetHeartbeatTimeout() {
    if (heartbeatTimeout) {
      clearTimeout(heartbeatTimeout);
    }
    if (isClosed) return;

    heartbeatTimeout = setTimeout(() => {
      console.warn('[UserEventsSSE] Heartbeat timeout - reconnecting...');
      source?.close();
      reconnectAttempts++;
      setConnectionState('reconnecting');
      scheduleReconnect();
    }, USER_EVENTS_HEARTBEAT_TIMEOUT_MS);
  }

  function startPolling() {
    if (pollInterval) return;
    setConnectionState('polling');

    // For user events, "polling" means we just try to reconnect periodically
    pollInterval = setInterval(() => {
      if (isClosed) return;
      // Try reconnecting
      reconnectAttempts = 0;
      connect();
    }, USER_EVENTS_POLL_INTERVAL_MS * 2);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  function scheduleReconnect() {
    if (isClosed) return;

    const delay = getBackoffDelay();

    // After many failed attempts, switch to polling mode
    if (reconnectAttempts > 3) {
      startPolling();
      return;
    }

    reconnectTimeout = setTimeout(connect, delay);
  }

  function connect() {
    if (isClosed) return;

    source?.close();

    const url = buildUrl();
    source = new EventSource(url);

    source.onopen = () => {
      reconnectAttempts = 0;
      setConnectionState('connected');
      resetHeartbeatTimeout();
      stopPolling();
    };

    source.onmessage = (event) => {
      resetHeartbeatTimeout();

      try {
        const parsed = JSON.parse(event.data) as UserEvent;

        // Handle heartbeat silently
        if (parsed.type === 'heartbeat') {
          return;
        }

        onEvent(parsed);
      } catch (error) {
        onError(new Error('Failed to parse user events SSE payload'));
      }
    };

    source.onerror = () => {
      source?.close();

      if (isClosed) return;

      reconnectAttempts++;
      setConnectionState('reconnecting');
      scheduleReconnect();
    };
  }

  function cleanup() {
    isClosed = true;
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }
    if (heartbeatTimeout) {
      clearTimeout(heartbeatTimeout);
      heartbeatTimeout = null;
    }
    stopPolling();
    source?.close();
    source = null;
  }

  connect();

  return cleanup;
}
