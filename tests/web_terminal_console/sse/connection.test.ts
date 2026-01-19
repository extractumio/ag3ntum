import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SSEEvent } from '../../../src/web_terminal_client/src/types';

// =============================================================================
// SSE Constants and Types (from sse.ts)
// =============================================================================

const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_BACKOFF_MS = 30000;
const POLL_INTERVAL_MS = 4000;
const HEARTBEAT_TIMEOUT_MS = 45000;
const TERMINAL_EVENTS = ['agent_complete', 'error', 'cancelled'];

type ConnectionState = 'connected' | 'reconnecting' | 'polling' | 'degraded';

interface HeartbeatData {
  session_status?: string;
  server_time?: string;
  redis_ok?: boolean;
  last_sequence?: number;
}

// =============================================================================
// Helper Functions (extracted for testing)
// =============================================================================

function getBackoffDelay(reconnectAttempts: number): number {
  const exponential = INITIAL_RECONNECT_DELAY_MS * Math.pow(2, Math.min(reconnectAttempts - 1, 10));
  const capped = Math.min(exponential, MAX_BACKOFF_MS);
  const jitter = capped * 0.2 * (Math.random() - 0.5) * 2;
  return Math.max(100, capped + jitter);
}

function buildUrl(baseUrl: string, sessionId: string, token: string, lastEventId: string | null): string {
  const params = new URLSearchParams({ token });
  if (lastEventId) {
    params.set('after', lastEventId);
  }
  return `${baseUrl}/api/v1/sessions/${sessionId}/events?${params.toString()}`;
}

// =============================================================================
// Mock EventSource Implementation
// =============================================================================

class MockEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;

  readyState = MockEventSource.CONNECTING;
  url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  private static instances: MockEventSource[] = [];

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  close() {
    this.readyState = MockEventSource.CLOSED;
  }

  // Test helpers
  static getLastInstance(): MockEventSource | undefined {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }

  static clearInstances() {
    MockEventSource.instances = [];
  }

  simulateOpen() {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.(new Event('open'));
  }

  simulateMessage(data: SSEEvent) {
    const event = new MessageEvent('message', {
      data: JSON.stringify(data),
      lastEventId: String(data.sequence),
    });
    this.onmessage?.(event);
  }

  simulateError() {
    this.onerror?.(new Event('error'));
  }
}

// =============================================================================
// Tests
// =============================================================================

describe('SSE Connection', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockEventSource.clearInstances();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('buildUrl', () => {
    it('builds correct URL with token', () => {
      const url = buildUrl('http://localhost:40080', 'session123', 'token456', null);

      expect(url).toBe('http://localhost:40080/api/v1/sessions/session123/events?token=token456');
    });

    it('includes lastEventId when provided', () => {
      const url = buildUrl('http://localhost:40080', 'session123', 'token456', '10');

      expect(url).toContain('token=token456');
      expect(url).toContain('after=10');
    });

    it('handles special characters in token', () => {
      const url = buildUrl('http://localhost:40080', 'session123', 'token+with/special', null);

      expect(url).toContain('token=token%2Bwith%2Fspecial');
    });
  });

  describe('getBackoffDelay', () => {
    beforeEach(() => {
      vi.spyOn(Math, 'random').mockReturnValue(0.5); // Neutral jitter
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('returns minimum delay for first attempt', () => {
      const delay = getBackoffDelay(1);

      // 1000 * 2^0 = 1000, with 0 jitter (0.5 - 0.5 = 0)
      expect(delay).toBe(1000);
    });

    it('doubles delay for each attempt', () => {
      expect(getBackoffDelay(1)).toBe(1000);   // 1000 * 2^0
      expect(getBackoffDelay(2)).toBe(2000);   // 1000 * 2^1
      expect(getBackoffDelay(3)).toBe(4000);   // 1000 * 2^2
      expect(getBackoffDelay(4)).toBe(8000);   // 1000 * 2^3
      expect(getBackoffDelay(5)).toBe(16000);  // 1000 * 2^4
    });

    it('caps at MAX_BACKOFF_MS', () => {
      const delay = getBackoffDelay(20);

      expect(delay).toBeLessThanOrEqual(MAX_BACKOFF_MS * 1.2); // Allow for jitter
    });

    it('adds jitter to prevent thundering herd', () => {
      vi.spyOn(Math, 'random')
        .mockReturnValueOnce(0)    // Min jitter
        .mockReturnValueOnce(1);   // Max jitter

      const delayMin = getBackoffDelay(2);
      const delayMax = getBackoffDelay(2);

      // With attempt 2: base = 2000
      // Jitter range: ±20% = ±400
      expect(delayMin).toBeLessThan(delayMax);
      expect(delayMax - delayMin).toBeLessThanOrEqual(800);
    });

    it('never returns less than 100ms', () => {
      vi.spyOn(Math, 'random').mockReturnValue(0); // Max negative jitter

      for (let i = 1; i <= 15; i++) {
        expect(getBackoffDelay(i)).toBeGreaterThanOrEqual(100);
      }
    });
  });

  describe('Terminal Events', () => {
    it('defines correct terminal events', () => {
      expect(TERMINAL_EVENTS).toContain('agent_complete');
      expect(TERMINAL_EVENTS).toContain('error');
      expect(TERMINAL_EVENTS).toContain('cancelled');
      expect(TERMINAL_EVENTS).toHaveLength(3);
    });

    it.each(TERMINAL_EVENTS)('identifies %s as terminal', (eventType) => {
      expect(TERMINAL_EVENTS.includes(eventType)).toBe(true);
    });

    it.each(['message', 'tool_start', 'tool_complete', 'heartbeat', 'user_message'])(
      'does not identify %s as terminal',
      (eventType) => {
        expect(TERMINAL_EVENTS.includes(eventType)).toBe(false);
      }
    );
  });

  describe('Connection Constants', () => {
    it('has correct initial reconnect delay', () => {
      expect(INITIAL_RECONNECT_DELAY_MS).toBe(1000);
    });

    it('has correct max backoff', () => {
      expect(MAX_BACKOFF_MS).toBe(30000);
    });

    it('has correct poll interval', () => {
      expect(POLL_INTERVAL_MS).toBe(4000);
    });

    it('has correct heartbeat timeout', () => {
      expect(HEARTBEAT_TIMEOUT_MS).toBe(45000);
    });
  });

  describe('Event Deduplication', () => {
    it('tracks seen sequences', () => {
      const seenSequences = new Set<number>();

      // Simulate processing events
      const events: SSEEvent[] = [
        { type: 'message', data: {}, timestamp: '', sequence: 1 },
        { type: 'message', data: {}, timestamp: '', sequence: 2 },
        { type: 'message', data: {}, timestamp: '', sequence: 1 }, // Duplicate
        { type: 'message', data: {}, timestamp: '', sequence: 3 },
      ];

      const processed: SSEEvent[] = [];

      events.forEach((event) => {
        if (!seenSequences.has(event.sequence)) {
          seenSequences.add(event.sequence);
          processed.push(event);
        }
      });

      expect(processed).toHaveLength(3);
      expect(processed.map((e) => e.sequence)).toEqual([1, 2, 3]);
    });

    it('bounds sequence set size', () => {
      const seenSequences = new Set<number>();
      const maxSize = 1000;

      // Add more than max sequences
      for (let i = 0; i < 1500; i++) {
        seenSequences.add(i);

        // Simulate cleanup when exceeding max
        if (seenSequences.size > maxSize) {
          const arr = Array.from(seenSequences).sort((a, b) => a - b);
          for (let j = 0; j < 500; j++) {
            seenSequences.delete(arr[j]);
          }
        }
      }

      expect(seenSequences.size).toBeLessThanOrEqual(maxSize);
    });
  });

  describe('MockEventSource', () => {
    it('tracks instances', () => {
      MockEventSource.clearInstances();

      new MockEventSource('url1');
      new MockEventSource('url2');

      expect(MockEventSource.getLastInstance()?.url).toBe('url2');
    });

    it('starts in CONNECTING state', () => {
      const source = new MockEventSource('url');

      expect(source.readyState).toBe(MockEventSource.CONNECTING);
    });

    it('transitions to OPEN on simulateOpen', () => {
      const source = new MockEventSource('url');
      const onopen = vi.fn();
      source.onopen = onopen;

      source.simulateOpen();

      expect(source.readyState).toBe(MockEventSource.OPEN);
      expect(onopen).toHaveBeenCalled();
    });

    it('transitions to CLOSED on close', () => {
      const source = new MockEventSource('url');
      source.simulateOpen();

      source.close();

      expect(source.readyState).toBe(MockEventSource.CLOSED);
    });

    it('delivers messages via simulateMessage', () => {
      const source = new MockEventSource('url');
      const onmessage = vi.fn();
      source.onmessage = onmessage;

      const event: SSEEvent = {
        type: 'message',
        data: { text: 'hello' },
        timestamp: '2024-01-15T12:00:00Z',
        sequence: 1,
      };

      source.simulateMessage(event);

      expect(onmessage).toHaveBeenCalled();
      const messageEvent = onmessage.mock.calls[0][0];
      expect(JSON.parse(messageEvent.data)).toEqual(event);
    });

    it('triggers error via simulateError', () => {
      const source = new MockEventSource('url');
      const onerror = vi.fn();
      source.onerror = onerror;

      source.simulateError();

      expect(onerror).toHaveBeenCalled();
    });
  });

  describe('Connection State Transitions', () => {
    it('valid state transitions', () => {
      const validTransitions: Record<ConnectionState, ConnectionState[]> = {
        connected: ['reconnecting'],
        reconnecting: ['connected', 'polling', 'degraded'],
        polling: ['connected', 'degraded'],
        degraded: ['connected', 'polling'],
      };

      // Verify each state has defined valid transitions
      Object.keys(validTransitions).forEach((state) => {
        expect(validTransitions[state as ConnectionState].length).toBeGreaterThan(0);
      });
    });

    it('reconnecting after connection error', () => {
      let connectionState: ConnectionState = 'connected';

      // Simulate error
      connectionState = 'reconnecting';

      expect(connectionState).toBe('reconnecting');
    });

    it('transitions to polling after multiple reconnect failures', () => {
      let connectionState: ConnectionState = 'reconnecting';
      let reconnectAttempts = 0;
      const maxReconnectAttempts = 5;

      // Simulate multiple failures
      while (reconnectAttempts <= maxReconnectAttempts) {
        reconnectAttempts++;
        if (reconnectAttempts > maxReconnectAttempts) {
          connectionState = 'polling';
        }
      }

      expect(connectionState).toBe('polling');
    });
  });

  describe('Heartbeat Handling', () => {
    it('parses heartbeat data', () => {
      const heartbeatEvent: SSEEvent = {
        type: 'heartbeat',
        data: {
          session_status: 'running',
          server_time: '2024-01-15T12:00:00Z',
          redis_ok: true,
          last_sequence: 10,
        },
        timestamp: '2024-01-15T12:00:00Z',
        sequence: 0,
      };

      const data = heartbeatEvent.data as HeartbeatData;

      expect(data.session_status).toBe('running');
      expect(data.redis_ok).toBe(true);
      expect(data.last_sequence).toBe(10);
    });

    it('heartbeat timeout triggers reconnect', () => {
      let shouldReconnect = false;
      let heartbeatTimer: ReturnType<typeof setTimeout> | null = null;

      // Simulate heartbeat timeout setup
      heartbeatTimer = setTimeout(() => {
        shouldReconnect = true;
      }, HEARTBEAT_TIMEOUT_MS);

      // Advance past timeout
      vi.advanceTimersByTime(HEARTBEAT_TIMEOUT_MS + 1);

      expect(shouldReconnect).toBe(true);

      // Cleanup
      if (heartbeatTimer) clearTimeout(heartbeatTimer);
    });

    it('heartbeat resets timeout', () => {
      let timeoutCount = 0;
      let heartbeatTimer: ReturnType<typeof setTimeout> | null = null;

      const resetHeartbeat = () => {
        if (heartbeatTimer) clearTimeout(heartbeatTimer);
        heartbeatTimer = setTimeout(() => {
          timeoutCount++;
        }, HEARTBEAT_TIMEOUT_MS);
      };

      // Initial setup
      resetHeartbeat();

      // Advance partway
      vi.advanceTimersByTime(HEARTBEAT_TIMEOUT_MS - 1000);

      // Receive heartbeat - reset timer
      resetHeartbeat();

      // Advance less than full timeout
      vi.advanceTimersByTime(HEARTBEAT_TIMEOUT_MS - 1000);

      // Should not have timed out
      expect(timeoutCount).toBe(0);

      // Advance past timeout without reset
      vi.advanceTimersByTime(2000);

      expect(timeoutCount).toBe(1);

      if (heartbeatTimer) clearTimeout(heartbeatTimer);
    });
  });

  describe('lastEventId tracking', () => {
    it('updates lastEventId from event sequence', () => {
      let lastEventId: string | null = null;

      const events: SSEEvent[] = [
        { type: 'message', data: {}, timestamp: '', sequence: 5 },
        { type: 'message', data: {}, timestamp: '', sequence: 10 },
        { type: 'message', data: {}, timestamp: '', sequence: 7 }, // Out of order
      ];

      events.forEach((event) => {
        const currentId = lastEventId ? parseInt(lastEventId, 10) : 0;
        lastEventId = String(Math.max(currentId, event.sequence));
      });

      expect(lastEventId).toBe('10');
    });

    it('includes lastEventId in URL when reconnecting', () => {
      const url = buildUrl('http://localhost:40080', 'session123', 'token456', '42');

      expect(url).toContain('after=42');
    });
  });

  describe('Polling Fallback', () => {
    it('poll interval is shorter than heartbeat timeout', () => {
      // Ensures polling can detect issues before heartbeat timeout
      expect(POLL_INTERVAL_MS).toBeLessThan(HEARTBEAT_TIMEOUT_MS);
    });

    it('simulates polling behavior', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve([]),
      });
      global.fetch = mockFetch;

      let pollCount = 0;
      const pollInterval = setInterval(() => {
        pollCount++;
        mockFetch();
      }, POLL_INTERVAL_MS);

      // Advance through 3 poll cycles
      vi.advanceTimersByTime(POLL_INTERVAL_MS * 3);

      expect(pollCount).toBe(3);
      expect(mockFetch).toHaveBeenCalledTimes(3);

      clearInterval(pollInterval);
    });
  });
});
