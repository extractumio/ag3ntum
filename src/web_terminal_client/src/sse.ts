import type { SSEEvent } from './types';

const MAX_RECONNECT_ATTEMPTS = 5;
const INITIAL_RECONNECT_DELAY_MS = 1000;
const POLL_INTERVAL_MS = 4000;

export function connectSSE(
  baseUrl: string,
  sessionId: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onReconnecting?: (attempt: number) => void,
  initialLastEventId?: string | number | null
): () => void {
  let source: EventSource | null = null;
  let reconnectAttempts = 0;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let pollInterval: ReturnType<typeof setInterval> | null = null;
  let isClosed = false;
  let lastEventId: string | null = initialLastEventId ? String(initialLastEventId) : null;

  function buildUrl(): string {
    const params = new URLSearchParams({ token });
    if (lastEventId) {
      params.set('after', lastEventId);
    }
    return `${baseUrl}/api/v1/sessions/${sessionId}/events?${params.toString()}`;
  }

  async function pollEvents(): Promise<void> {
    if (isClosed) return;

    const params = new URLSearchParams({ token });
    if (lastEventId) {
      params.set('after', lastEventId);
    }
    const url = `${baseUrl}/api/v1/sessions/${sessionId}/events/history?${params.toString()}`;

    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Polling failed (${response.status})`);
      }
      const events = (await response.json()) as SSEEvent[];
      events.forEach((event) => {
        lastEventId = String(event.sequence);
        onEvent(event);
        if (event.type === 'agent_complete' || event.type === 'error' || event.type === 'cancelled') {
          isClosed = true;
        }
      });
    } catch (error) {
      onError(error instanceof Error ? error : new Error('Polling failed'));
    }
  }

  function startPolling() {
    if (pollInterval) return;
    pollInterval = setInterval(() => {
      void pollEvents();
    }, POLL_INTERVAL_MS);
    void pollEvents();
  }

  function connect() {
    if (isClosed) return;

    const url = buildUrl();
    source = new EventSource(url);

    source.onopen = () => {
      reconnectAttempts = 0;
    };

    source.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as SSEEvent;
        lastEventId = event.lastEventId || String(parsed.sequence ?? lastEventId ?? '');
        onEvent(parsed);
        
        // Stop reconnecting on terminal events
        if (
          parsed.type === 'agent_complete' ||
          parsed.type === 'error' ||
          parsed.type === 'cancelled'
        ) {
          isClosed = true;
          source?.close();
          if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
          }
        }
      } catch (error) {
        onError(new Error('Failed to parse SSE payload'));
      }
    };

    source.onerror = () => {
      source?.close();
      
      // If already marked as closed (terminal event received), this is expected
      if (isClosed) {
        return;
      }

      reconnectAttempts++;

      if (reconnectAttempts <= MAX_RECONNECT_ATTEMPTS) {
        const delay = INITIAL_RECONNECT_DELAY_MS * Math.pow(2, reconnectAttempts - 1);
        onReconnecting?.(reconnectAttempts);
        reconnectTimeout = setTimeout(connect, delay);
      } else {
        onError(new Error('SSE connection failed after multiple attempts; falling back to polling.'));
        startPolling();
      }
    };
  }

  connect();

  return () => {
    isClosed = true;
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
    }
    if (pollInterval) {
      clearInterval(pollInterval);
    }
    source?.close();
  };
}
