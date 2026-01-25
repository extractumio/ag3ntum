/**
 * useSSEConnection Hook
 *
 * Manages Server-Sent Events connection including:
 * - SSE connection lifecycle
 * - Event streaming and handling
 * - Reconnection logic
 * - Connection state management
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { invalidateSessionsCache } from '../api';
import { connectSSE } from '../sse';
import type { AppConfig, TerminalEvent } from '../types';
import { EMPTY_EVENTS } from '../constants';

export type ConnectionState = 'connected' | 'reconnecting' | 'polling' | 'degraded';

export interface UseSSEConnectionResult {
  events: TerminalEvent[];
  setEvents: React.Dispatch<React.SetStateAction<TerminalEvent[]>>;
  connectionState: ConnectionState;
  reconnecting: boolean;
  error: string | null;
  setError: React.Dispatch<React.SetStateAction<string | null>>;
  startSSE: (sessionId: string, lastSequence?: number | null) => void;
  stopSSE: () => void;
  appendEvent: (event: TerminalEvent) => void;
}

export function useSSEConnection(
  config: AppConfig | null,
  token: string | null,
  onEvent: (event: TerminalEvent) => void,
  onSessionComplete?: () => void
): UseSSEConnectionResult {
  const [events, setEvents] = useState<TerminalEvent[]>(EMPTY_EVENTS);
  const [connectionState, setConnectionState] = useState<ConnectionState>('connected');
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

  // Append event with deduplication and max lines limit
  const appendEvent = useCallback(
    (event: TerminalEvent) => {
      setEvents((prev) => {
        if (event.type === 'user_message') {
          const last = prev[prev.length - 1];
          const lastText = (last?.data as { text?: unknown } | undefined)?.text;
          const nextText = (event.data as { text?: unknown } | undefined)?.text;
          if (last?.type === 'user_message' && lastText === nextText) {
            return prev;
          }
        }
        const next = [...prev, event];
        const maxLines = config?.ui.max_output_lines ?? 1000;
        if (next.length > maxLines) {
          return next.slice(-maxLines);
        }
        return next;
      });
    },
    [config]
  );

  // Start SSE connection
  const startSSE = useCallback(
    (sessionId: string, lastSequence?: number | null) => {
      if (!config || !token) {
        return;
      }

      // Clean up existing connection
      if (cleanupRef.current) {
        cleanupRef.current();
      }

      cleanupRef.current = connectSSE(
        config.api.base_url,
        sessionId,
        token,
        (event) => {
          setReconnecting(false);
          appendEvent(event);
          onEvent(event);
        },
        (err) => {
          setReconnecting(false);
          setError(err.message);
        },
        (attempt) => {
          setReconnecting(true);
          if (attempt > 3) {
            setError(`Reconnecting (attempt ${attempt})...`);
          }
        },
        lastSequence ?? null,
        // Heartbeat callback
        (heartbeatData) => {
          if (
            heartbeatData.session_status &&
            ['completed', 'failed', 'cancelled'].includes(heartbeatData.session_status)
          ) {
            invalidateSessionsCache();
            onSessionComplete?.();
          }
        },
        // Connection state change callback
        (state) => {
          setConnectionState(state);
          if (state === 'connected') {
            setReconnecting(false);
            setError(null);
          } else if (state === 'polling') {
            setError(null);
          } else if (state === 'degraded') {
            setError('Connection unstable');
          } else if (state === 'reconnecting') {
            setError(null);
          }
        }
      );
    },
    [config, token, appendEvent, onEvent, onSessionComplete]
  );

  // Stop SSE connection
  const stopSSE = useCallback(() => {
    if (cleanupRef.current) {
      cleanupRef.current();
      cleanupRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (cleanupRef.current) {
        cleanupRef.current();
      }
    };
  }, []);

  return {
    events,
    setEvents,
    connectionState,
    reconnecting,
    error,
    setError,
    startSSE,
    stopSSE,
    appendEvent,
  };
}
