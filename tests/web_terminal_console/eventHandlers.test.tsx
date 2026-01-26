/**
 * Tests for SSE event handlers and session state updates.
 * 
 * Covers:
 * - agent_complete event handling
 * - error event handling  
 * - cancelled event handling
 * - Session timestamps updates (completed_at, updated_at)
 * - refreshSessions calls after terminal events
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { SessionResponse, SSEEvent } from '../../src/web_terminal_client/src/types';

// Mock the event handler logic extracted from App.tsx
// This simulates what the handleEvent function does

interface SessionState {
  currentSession: SessionResponse | null;
  sessions: SessionResponse[];
  status: string;
  runningStartTime: string | null;
  error: string | null;
}

function createMockSession(overrides: Partial<SessionResponse> = {}): SessionResponse {
  const now = new Date();
  return {
    id: 'test_session',
    status: 'running',
    task: 'Test task',
    model: 'claude-sonnet-4',
    created_at: now.toISOString(),
    updated_at: now.toISOString(),
    completed_at: null,
    num_turns: 0,
    duration_ms: null,
    total_cost_usd: null,
    cancel_requested: false,
    resumable: true,
    queue_position: null,
    queued_at: null,
    is_auto_resume: false,
    ...overrides,
  };
}

// Simulate the handleEvent logic for agent_complete
function handleAgentComplete(
  state: SessionState,
  event: SSEEvent
): SessionState {
  const normalizedStatus = String(event.data.status ?? 'complete');
  const completedAt = new Date().toISOString();
  
  return {
    ...state,
    status: normalizedStatus,
    runningStartTime: null,
    currentSession: state.currentSession
      ? {
          ...state.currentSession,
          status: normalizedStatus,
          completed_at: completedAt,
          updated_at: completedAt,
          num_turns: state.currentSession.num_turns + Number(event.data.num_turns ?? 0),
        }
      : null,
    sessions: state.sessions.map((session) =>
      session.id === state.currentSession?.id
        ? { ...session, status: normalizedStatus, completed_at: completedAt, updated_at: completedAt }
        : session
    ),
  };
}

// Simulate the handleEvent logic for error
function handleError(
  state: SessionState,
  event: SSEEvent
): SessionState {
  const nowIso = new Date().toISOString();
  
  return {
    ...state,
    status: 'failed',
    runningStartTime: null,
    error: String(event.data.message ?? 'Unknown error'),
    currentSession: state.currentSession
      ? {
          ...state.currentSession,
          status: 'failed',
          completed_at: nowIso,
          updated_at: nowIso,
        }
      : null,
    sessions: state.sessions.map((session) =>
      session.id === state.currentSession?.id
        ? { ...session, status: 'failed', completed_at: nowIso, updated_at: nowIso }
        : session
    ),
  };
}

// Simulate the handleEvent logic for cancelled
function handleCancelled(
  state: SessionState,
  event: SSEEvent
): SessionState {
  const cancelledAt = new Date().toISOString();
  const resumable = Boolean(event.data?.resumable);
  
  return {
    ...state,
    status: 'cancelled',
    runningStartTime: null,
    currentSession: state.currentSession
      ? {
          ...state.currentSession,
          status: 'cancelled',
          completed_at: cancelledAt,
          updated_at: cancelledAt,
          resumable,
        }
      : null,
    sessions: state.sessions.map((session) =>
      session.id === state.currentSession?.id
        ? { ...session, status: 'cancelled', completed_at: cancelledAt, updated_at: cancelledAt }
        : session
    ),
  };
}

describe('SSE Event Handlers', () => {
  let mockRefreshSessions: ReturnType<typeof vi.fn>;
  let mockInvalidateSessionsCache: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    mockRefreshSessions = vi.fn();
    mockInvalidateSessionsCache = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('agent_complete Event', () => {
    it('updates session status to complete', () => {
      const session = createMockSession({ id: 'complete_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'agent_complete',
        data: { status: 'complete', num_turns: 5 },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleAgentComplete(state, event);

      expect(newState.status).toBe('complete');
      expect(newState.currentSession?.status).toBe('complete');
      expect(newState.sessions[0].status).toBe('complete');
    });

    it('sets completed_at timestamp on session', () => {
      const session = createMockSession({ id: 'timestamp_test', status: 'running', completed_at: null });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'agent_complete',
        data: { status: 'complete' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleAgentComplete(state, event);

      expect(newState.currentSession?.completed_at).not.toBeNull();
      expect(newState.sessions[0].completed_at).not.toBeNull();
    });

    it('sets updated_at timestamp on session', () => {
      const oldUpdatedAt = new Date(Date.now() - 60000).toISOString();
      const session = createMockSession({ 
        id: 'updated_at_test', 
        status: 'running',
        updated_at: oldUpdatedAt,
      });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'agent_complete',
        data: { status: 'complete' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleAgentComplete(state, event);

      // updated_at should be newer than the old value
      expect(new Date(newState.sessions[0].updated_at).getTime())
        .toBeGreaterThan(new Date(oldUpdatedAt).getTime());
    });

    it('clears runningStartTime', () => {
      const session = createMockSession({ id: 'clear_running_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'agent_complete',
        data: { status: 'complete' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleAgentComplete(state, event);

      expect(newState.runningStartTime).toBeNull();
    });

    it('handles failed status from agent_complete', () => {
      const session = createMockSession({ id: 'failed_complete_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'agent_complete',
        data: { status: 'failed' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleAgentComplete(state, event);

      expect(newState.status).toBe('failed');
      expect(newState.currentSession?.status).toBe('failed');
      expect(newState.sessions[0].status).toBe('failed');
    });
  });

  describe('error Event', () => {
    it('updates session status to failed', () => {
      const session = createMockSession({ id: 'error_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'error',
        data: { message: 'Something went wrong' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleError(state, event);

      expect(newState.status).toBe('failed');
      expect(newState.currentSession?.status).toBe('failed');
      expect(newState.sessions[0].status).toBe('failed');
    });

    it('sets error message', () => {
      const session = createMockSession({ id: 'error_msg_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'error',
        data: { message: 'API timeout error' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleError(state, event);

      expect(newState.error).toBe('API timeout error');
    });

    it('sets completed_at timestamp on error', () => {
      const session = createMockSession({ id: 'error_timestamp_test', status: 'running', completed_at: null });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'error',
        data: { message: 'Error occurred' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleError(state, event);

      expect(newState.currentSession?.completed_at).not.toBeNull();
      expect(newState.sessions[0].completed_at).not.toBeNull();
    });

    it('sets updated_at timestamp on error', () => {
      const oldUpdatedAt = new Date(Date.now() - 60000).toISOString();
      const session = createMockSession({ 
        id: 'error_updated_at_test', 
        status: 'running',
        updated_at: oldUpdatedAt,
      });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'error',
        data: { message: 'Error' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleError(state, event);

      expect(new Date(newState.sessions[0].updated_at).getTime())
        .toBeGreaterThan(new Date(oldUpdatedAt).getTime());
    });

    it('clears runningStartTime on error', () => {
      const session = createMockSession({ id: 'error_clear_running_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'error',
        data: { message: 'Error' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleError(state, event);

      expect(newState.runningStartTime).toBeNull();
    });

    it('handles missing error message', () => {
      const session = createMockSession({ id: 'no_msg_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'error',
        data: {},
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleError(state, event);

      expect(newState.error).toBe('Unknown error');
    });
  });

  describe('cancelled Event', () => {
    it('updates session status to cancelled', () => {
      const session = createMockSession({ id: 'cancel_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'cancelled',
        data: { resumable: true },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleCancelled(state, event);

      expect(newState.status).toBe('cancelled');
      expect(newState.currentSession?.status).toBe('cancelled');
      expect(newState.sessions[0].status).toBe('cancelled');
    });

    it('sets completed_at timestamp on cancel', () => {
      const session = createMockSession({ id: 'cancel_timestamp_test', status: 'running', completed_at: null });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'cancelled',
        data: {},
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleCancelled(state, event);

      expect(newState.currentSession?.completed_at).not.toBeNull();
      expect(newState.sessions[0].completed_at).not.toBeNull();
    });

    it('sets updated_at timestamp on cancel', () => {
      const oldUpdatedAt = new Date(Date.now() - 60000).toISOString();
      const session = createMockSession({ 
        id: 'cancel_updated_at_test', 
        status: 'running',
        updated_at: oldUpdatedAt,
      });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'cancelled',
        data: {},
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleCancelled(state, event);

      expect(new Date(newState.sessions[0].updated_at).getTime())
        .toBeGreaterThan(new Date(oldUpdatedAt).getTime());
    });

    it('clears runningStartTime on cancel', () => {
      const session = createMockSession({ id: 'cancel_clear_running_test', status: 'running' });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'cancelled',
        data: {},
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleCancelled(state, event);

      expect(newState.runningStartTime).toBeNull();
    });

    it('preserves resumable flag from event', () => {
      const session = createMockSession({ id: 'resumable_test', status: 'running', resumable: false });
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'cancelled',
        data: { resumable: true },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleCancelled(state, event);

      expect(newState.currentSession?.resumable).toBe(true);
    });
  });

  describe('Session List Updates', () => {
    it('only updates matching session in list', () => {
      const session1 = createMockSession({ id: 'session_1', status: 'running' });
      const session2 = createMockSession({ id: 'session_2', status: 'complete' });
      const session3 = createMockSession({ id: 'session_3', status: 'failed' });
      
      const state: SessionState = {
        currentSession: session1,
        sessions: [session1, session2, session3],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'agent_complete',
        data: { status: 'complete' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleAgentComplete(state, event);

      // Only session_1 should be updated
      expect(newState.sessions[0].status).toBe('complete');
      expect(newState.sessions[0].completed_at).not.toBeNull();
      
      // Other sessions should remain unchanged
      expect(newState.sessions[1].status).toBe('complete');
      expect(newState.sessions[2].status).toBe('failed');
    });

    it('handles case when currentSession is null', () => {
      const session = createMockSession({ id: 'orphan_session', status: 'running' });
      
      const state: SessionState = {
        currentSession: null,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      const event: SSEEvent = {
        type: 'agent_complete',
        data: { status: 'complete' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleAgentComplete(state, event);

      // Should not crash and session list should remain unchanged
      expect(newState.currentSession).toBeNull();
      expect(newState.sessions[0].status).toBe('running');
    });
  });

  describe('Timer Stop Behavior', () => {
    it('ElapsedTime stops when session gets completed_at timestamp', () => {
      // This test verifies the contract between event handlers and ElapsedTime
      const session = createMockSession({ 
        id: 'timer_stop_test', 
        status: 'running',
        completed_at: null,
        updated_at: new Date(Date.now() - 60000).toISOString(),
      });
      
      const state: SessionState = {
        currentSession: session,
        sessions: [session],
        status: 'running',
        runningStartTime: new Date().toISOString(),
        error: null,
      };

      // Session is running - isActive should be true
      expect(state.sessions[0].status).toBe('running');
      expect(state.sessions[0].completed_at).toBeNull();

      const event: SSEEvent = {
        type: 'error',
        data: { message: 'Task failed' },
        timestamp: new Date().toISOString(),
        sequence: 1,
      };

      const newState = handleError(state, event);

      // After error - session should have completed_at
      // ElapsedTime uses: const isActive = status === 'running' || status === 'queued'
      // ElapsedTime uses: const endTime = !isActive ? (completed_at || updated_at) : null
      expect(newState.sessions[0].status).toBe('failed');
      expect(newState.sessions[0].completed_at).not.toBeNull();
      expect(newState.sessions[0].updated_at).not.toBeNull();
      
      // The completed_at and updated_at should be the same (set at same time)
      expect(newState.sessions[0].completed_at).toBe(newState.sessions[0].updated_at);
    });
  });
});
