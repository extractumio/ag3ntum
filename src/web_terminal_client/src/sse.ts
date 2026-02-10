import type { SSEEvent, TerminalEvent } from './types';
import { ConnectionManager, type HeartbeatData } from './ConnectionManager';

// Re-export HeartbeatData so existing imports from './sse' continue to work
export type { HeartbeatData } from './ConnectionManager';

// Configuration constants (used only by connectUserEventsSSE)
const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_BACKOFF_MS = 30000;

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
 * Delegates to ConnectionManager for all connection state management.
 * This function preserves the original API signature for backward compatibility.
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
  const manager = new ConnectionManager({
    baseUrl,
    sessionId,
    token,
    lastSequence: initialLastEventId != null ? Number(initialLastEventId) : null,
    onEvent: (event: TerminalEvent) => {
      onEvent(event);
    },
    onStateChange: (state, info) => {
      // Map ConnectionManager states to the simpler sse.ts state type
      // ConnectionManager has 'disconnected' and 'connecting' which sse.ts doesn't expose
      if (state === 'connected' || state === 'reconnecting' || state === 'polling' || state === 'degraded') {
        onConnectionStateChange?.(state);
      }

      // Fire onReconnecting callback when entering reconnecting state
      if (state === 'reconnecting' && info?.attempt) {
        onReconnecting?.(info.attempt);
      }
    },
    onError,
    onHeartbeat,
  });

  manager.connect();

  return () => {
    manager.disconnect();
  };
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
 *
 * Note: This uses a simpler connection model than session SSE (no terminal events,
 * no polling to /events/history, different timeouts) so it does not use ConnectionManager.
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
