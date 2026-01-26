/**
 * Tests for resilient session state management.
 *
 * Covers:
 * - Terminal status protection in User Events SSE
 * - Timestamp-based merge logic in refreshSessions
 * - currentSession/sessions[] synchronization
 * - Race condition prevention
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { SessionResponse } from '../../src/web_terminal_client/src/types';

// Terminal statuses set - matches the one in App.tsx
const TERMINAL_STATUSES = new Set(['complete', 'completed', 'partial', 'failed', 'cancelled', 'canceled']);

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

describe('Session State Synchronization', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('Terminal Status Protection', () => {
    it('prevents overwriting terminal status with non-terminal status', () => {
      // Simulates the protection logic used in User Events SSE handler
      const localSession = createMockSession({ id: 'session_1', status: 'completed' });
      const serverStatus = 'running'; // Stale status from server

      // This is the protection check
      const shouldKeepLocalStatus =
        TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverStatus);

      expect(shouldKeepLocalStatus).toBe(true);
    });

    it('allows overwriting non-terminal with terminal status', () => {
      const localSession = createMockSession({ id: 'session_1', status: 'running' });
      const serverStatus = 'completed';

      const shouldKeepLocalStatus =
        TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverStatus);

      expect(shouldKeepLocalStatus).toBe(false);
    });

    it('allows overwriting terminal with terminal status (both are final)', () => {
      const localSession = createMockSession({ id: 'session_1', status: 'completed' });
      const serverStatus = 'failed'; // Server says it failed, not completed

      const shouldKeepLocalStatus =
        TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverStatus);

      expect(shouldKeepLocalStatus).toBe(false);
    });

    it('protects partial status from being overwritten', () => {
      const localSession = createMockSession({ id: 'session_1', status: 'partial' });
      const serverStatus = 'running';

      const shouldKeepLocalStatus =
        TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverStatus);

      expect(shouldKeepLocalStatus).toBe(true);
    });

    it('protects failed status from being overwritten', () => {
      const localSession = createMockSession({ id: 'session_1', status: 'failed' });
      const serverStatus = 'running';

      const shouldKeepLocalStatus =
        TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverStatus);

      expect(shouldKeepLocalStatus).toBe(true);
    });

    it('protects cancelled status from being overwritten', () => {
      const localSession = createMockSession({ id: 'session_1', status: 'cancelled' });
      const serverStatus = 'running';

      const shouldKeepLocalStatus =
        TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverStatus);

      expect(shouldKeepLocalStatus).toBe(true);
    });
  });

  describe('Timestamp-Based Merge Logic', () => {
    /**
     * Simulates the merge logic in refreshSessions
     */
    function mergeSession(local: SessionResponse, server: SessionResponse): SessionResponse {
      // RULE 1: If local is terminal and server is non-terminal, keep local
      if (TERMINAL_STATUSES.has(local.status) && !TERMINAL_STATUSES.has(server.status)) {
        return local;
      }

      // RULE 2: If local has newer timestamp and is terminal, keep local
      const localTime = new Date(local.updated_at).getTime();
      const serverTime = new Date(server.updated_at).getTime();
      if (localTime > serverTime && TERMINAL_STATUSES.has(local.status)) {
        return local;
      }

      // Otherwise, use server data
      return server;
    }

    it('keeps local session when local is terminal and server is non-terminal', () => {
      const local = createMockSession({
        id: 'session_1',
        status: 'completed',
        updated_at: new Date('2024-01-01T10:00:00Z').toISOString(),
      });
      const server = createMockSession({
        id: 'session_1',
        status: 'running',
        updated_at: new Date('2024-01-01T10:00:00Z').toISOString(),
      });

      const result = mergeSession(local, server);
      expect(result.status).toBe('completed');
    });

    it('uses server when server timestamp is newer even if both terminal', () => {
      const local = createMockSession({
        id: 'session_1',
        status: 'completed',
        updated_at: new Date('2024-01-01T10:00:00Z').toISOString(),
      });
      const server = createMockSession({
        id: 'session_1',
        status: 'failed',
        updated_at: new Date('2024-01-01T10:00:01Z').toISOString(), // 1 second newer
      });

      const result = mergeSession(local, server);
      expect(result.status).toBe('failed');
    });

    it('keeps local when local timestamp is newer and local is terminal', () => {
      const local = createMockSession({
        id: 'session_1',
        status: 'failed',
        updated_at: new Date('2024-01-01T10:00:01Z').toISOString(), // 1 second newer
      });
      const server = createMockSession({
        id: 'session_1',
        status: 'running',
        updated_at: new Date('2024-01-01T10:00:00Z').toISOString(),
      });

      const result = mergeSession(local, server);
      expect(result.status).toBe('failed');
    });

    it('uses server when local is non-terminal even with newer timestamp', () => {
      const local = createMockSession({
        id: 'session_1',
        status: 'running',
        updated_at: new Date('2024-01-01T10:00:01Z').toISOString(),
      });
      const server = createMockSession({
        id: 'session_1',
        status: 'completed',
        updated_at: new Date('2024-01-01T10:00:00Z').toISOString(),
      });

      const result = mergeSession(local, server);
      expect(result.status).toBe('completed');
    });
  });

  describe('User Events SSE Status Change Handling', () => {
    /**
     * Simulates the session_status_change event handler
     */
    function handleStatusChange(
      prevSessions: SessionResponse[],
      change: { id: string; new_status: string }
    ): SessionResponse[] {
      return prevSessions.map((session) => {
        if (session.id !== change.id) return session;

        // CRITICAL: Don't overwrite terminal status with non-terminal status
        if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(change.new_status)) {
          return session;
        }

        return { ...session, status: change.new_status };
      });
    }

    it('applies status change for non-terminal session', () => {
      const sessions = [
        createMockSession({ id: 'session_1', status: 'running' }),
        createMockSession({ id: 'session_2', status: 'queued' }),
      ];

      const result = handleStatusChange(sessions, { id: 'session_1', new_status: 'completed' });

      expect(result[0].status).toBe('completed');
      expect(result[1].status).toBe('queued');
    });

    it('protects completed session from stale running status', () => {
      const sessions = [
        createMockSession({ id: 'session_1', status: 'completed' }),
      ];

      const result = handleStatusChange(sessions, { id: 'session_1', new_status: 'running' });

      expect(result[0].status).toBe('completed');
    });

    it('allows status change from queued to running', () => {
      const sessions = [
        createMockSession({ id: 'session_1', status: 'queued' }),
      ];

      const result = handleStatusChange(sessions, { id: 'session_1', new_status: 'running' });

      expect(result[0].status).toBe('running');
    });

    it('does not affect other sessions', () => {
      const sessions = [
        createMockSession({ id: 'session_1', status: 'running' }),
        createMockSession({ id: 'session_2', status: 'completed' }),
        createMockSession({ id: 'session_3', status: 'failed' }),
      ];

      const result = handleStatusChange(sessions, { id: 'session_1', new_status: 'completed' });

      expect(result[0].status).toBe('completed');
      expect(result[1].status).toBe('completed');
      expect(result[2].status).toBe('failed');
    });
  });

  describe('Session List Update Handling', () => {
    /**
     * Simulates the session_list_update event handler
     */
    function handleSessionListUpdate(
      prevSessions: SessionResponse[],
      updates: Array<{ id: string; status: string }>
    ): SessionResponse[] {
      return prevSessions.map((session) => {
        const updated = updates.find((s) => s.id === session.id);
        if (!updated) return session;

        // CRITICAL: Don't overwrite terminal status with non-terminal status
        if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(updated.status)) {
          return session;
        }

        return { ...session, status: updated.status };
      });
    }

    it('applies bulk updates to non-terminal sessions', () => {
      const sessions = [
        createMockSession({ id: 'session_1', status: 'running' }),
        createMockSession({ id: 'session_2', status: 'queued' }),
        createMockSession({ id: 'session_3', status: 'running' }),
      ];

      const updates = [
        { id: 'session_1', status: 'completed' },
        { id: 'session_2', status: 'running' },
      ];

      const result = handleSessionListUpdate(sessions, updates);

      expect(result[0].status).toBe('completed');
      expect(result[1].status).toBe('running');
      expect(result[2].status).toBe('running'); // Not in updates, unchanged
    });

    it('protects multiple terminal sessions from stale updates', () => {
      const sessions = [
        createMockSession({ id: 'session_1', status: 'completed' }),
        createMockSession({ id: 'session_2', status: 'failed' }),
        createMockSession({ id: 'session_3', status: 'partial' }),
      ];

      const updates = [
        { id: 'session_1', status: 'running' },
        { id: 'session_2', status: 'running' },
        { id: 'session_3', status: 'running' },
      ];

      const result = handleSessionListUpdate(sessions, updates);

      expect(result[0].status).toBe('completed');
      expect(result[1].status).toBe('failed');
      expect(result[2].status).toBe('partial');
    });
  });

  describe('currentSession and sessions[] Sync Logic', () => {
    /**
     * Simulates the sync effect between currentSession and sessions[]
     *
     * IMPORTANT: This is ONE-WAY sync only: currentSession → sessions[]
     * We never sync FROM sessions[] TO currentSession because:
     * - When user clicks "Continue" on completed session, currentSession becomes 'running'
     * - If we synced from sessions[] (still 'completed'), we'd overwrite the intentional change
     * - Terminal events always update BOTH states via handleEvent()
     */
    function syncCurrentSession(
      currentSession: SessionResponse | null,
      sessions: SessionResponse[]
    ): {
      updateSessions: boolean;
      newStatus: string | null;
    } {
      if (!currentSession) {
        return { updateSessions: false, newStatus: null };
      }

      const matchingSession = sessions.find((s) => s.id === currentSession.id);
      if (!matchingSession) {
        return { updateSessions: false, newStatus: null };
      }

      if (matchingSession.status === currentSession.status) {
        // Already in sync
        return { updateSessions: false, newStatus: null };
      }

      // ONE-WAY SYNC: Only sync sessions[] from currentSession when:
      // - currentSession has terminal status
      // - AND sessions[] has non-terminal status (stale data)
      if (TERMINAL_STATUSES.has(currentSession.status) && !TERMINAL_STATUSES.has(matchingSession.status)) {
        return { updateSessions: true, newStatus: currentSession.status };
      }

      // All other cases: no sync (let event handlers manage state)
      return { updateSessions: false, newStatus: null };
    }

    it('syncs sessions[] when currentSession has terminal and sessions[] has non-terminal', () => {
      const currentSession = createMockSession({ id: 'session_1', status: 'completed' });
      const sessions = [createMockSession({ id: 'session_1', status: 'running' })];

      const result = syncCurrentSession(currentSession, sessions);

      expect(result.updateSessions).toBe(true);
      expect(result.newStatus).toBe('completed');
    });

    it('does NOT sync currentSession from sessions[] - prevents overwriting intentional Continue', () => {
      // This is the key fix: when user clicks Continue, currentSession becomes 'running'
      // Even if sessions[] still shows 'completed', we must NOT overwrite currentSession
      const currentSession = createMockSession({ id: 'session_1', status: 'running' });
      const sessions = [createMockSession({ id: 'session_1', status: 'completed' })];

      const result = syncCurrentSession(currentSession, sessions);

      // Should NOT update anything - let the normal event flow handle it
      expect(result.updateSessions).toBe(false);
      expect(result.newStatus).toBeNull();
    });

    it('does nothing when both are in sync', () => {
      const currentSession = createMockSession({ id: 'session_1', status: 'completed' });
      const sessions = [createMockSession({ id: 'session_1', status: 'completed' })];

      const result = syncCurrentSession(currentSession, sessions);

      expect(result.updateSessions).toBe(false);
      expect(result.newStatus).toBeNull();
    });

    it('does nothing when currentSession is null', () => {
      const sessions = [createMockSession({ id: 'session_1', status: 'running' })];

      const result = syncCurrentSession(null, sessions);

      expect(result.updateSessions).toBe(false);
    });

    it('does nothing when session not found in sessions[]', () => {
      const currentSession = createMockSession({ id: 'session_1', status: 'running' });
      const sessions = [createMockSession({ id: 'session_2', status: 'running' })];

      const result = syncCurrentSession(currentSession, sessions);

      expect(result.updateSessions).toBe(false);
    });

    it('does nothing when both are non-terminal and different', () => {
      const currentSession = createMockSession({ id: 'session_1', status: 'running' });
      const sessions = [createMockSession({ id: 'session_1', status: 'queued' })];

      const result = syncCurrentSession(currentSession, sessions);

      expect(result.updateSessions).toBe(false);
    });

    it('allows Continue workflow: completed → running transition', () => {
      // Simulate the Continue workflow:
      // 1. Session was completed
      // 2. User clicks Continue
      // 3. currentSession is updated to 'running' first
      // 4. sessions[] update may lag behind
      // 5. Sync effect should NOT revert currentSession to 'completed'

      // After user clicks Continue:
      const currentSession = createMockSession({ id: 'session_1', status: 'running' });
      const sessions = [createMockSession({ id: 'session_1', status: 'completed' })]; // Lagging

      const result = syncCurrentSession(currentSession, sessions);

      // Critical: Must NOT sync from sessions[] to currentSession
      expect(result.updateSessions).toBe(false);
      expect(result.newStatus).toBeNull();
    });
  });

  describe('Connection State Resync', () => {
    it('should trigger resync when transitioning from reconnecting to connected', () => {
      const transitions = [
        { from: 'reconnecting', to: 'connected', shouldResync: true },
        { from: 'polling', to: 'connected', shouldResync: true },
        { from: 'degraded', to: 'connected', shouldResync: true },
        { from: 'connected', to: 'connected', shouldResync: false },
        { from: 'connected', to: 'reconnecting', shouldResync: false },
        { from: 'connected', to: 'polling', shouldResync: false },
      ];

      for (const { from, to, shouldResync } of transitions) {
        const previousState = from;
        const newState = to;

        const triggerResync =
          newState === 'connected' &&
          (previousState === 'reconnecting' || previousState === 'polling' || previousState === 'degraded');

        expect(triggerResync).toBe(shouldResync);
      }
    });
  });

  describe('handleSubmit Continue Flow', () => {
    it('must update sessions[] directly when continuing - cannot rely on refreshSessions', () => {
      // This test documents the bug that was found:
      // When user continues a completed session:
      // 1. handleSubmit sets currentSession.status = 'running'
      // 2. handleSubmit calls refreshSessions()
      // 3. refreshSessions has terminal protection: if local is terminal and server is non-terminal, keep local
      // 4. BUG: sessions[] still has 'completed' (terminal), server might return 'running' (non-terminal)
      // 5. Terminal protection keeps local 'completed' - WRONG!
      //
      // Fix: handleSubmit must directly update sessions[] to 'running' before/after API call

      // Simulate the bug scenario:
      const sessions = [createMockSession({ id: 'session_1', status: 'completed' })];
      const serverResponse = [createMockSession({ id: 'session_1', status: 'running' })];

      // Without the fix, refreshSessions merge would do this:
      const buggedMerge = serverResponse.map((server) => {
        const local = sessions.find((s) => s.id === server.id);
        if (local && TERMINAL_STATUSES.has(local.status) && !TERMINAL_STATUSES.has(server.status)) {
          return local; // BUG: keeps 'completed'
        }
        return server;
      });

      // This shows the bug - status would remain 'completed'
      expect(buggedMerge[0].status).toBe('completed');

      // The fix is to update sessions[] directly in handleSubmit:
      const fixedSessions = sessions.map((s) =>
        s.id === 'session_1' ? { ...s, status: 'running' } : s
      );

      // Now sessions[] is 'running' (non-terminal)
      expect(fixedSessions[0].status).toBe('running');

      // And refreshSessions won't override it because local is now non-terminal
      const correctMerge = serverResponse.map((server) => {
        const local = fixedSessions.find((s) => s.id === server.id);
        if (local && TERMINAL_STATUSES.has(local.status) && !TERMINAL_STATUSES.has(server.status)) {
          return local;
        }
        return server;
      });

      expect(correctMerge[0].status).toBe('running');
    });
  });

  describe('Race Condition Scenarios', () => {
    it('scenario: agent_complete arrives before server updates - local state protected', () => {
      // User receives agent_complete event, updates local state to 'completed'
      // Then refreshSessions() is called, server still shows 'running'
      // Local 'completed' should be preserved

      const localSessions = [createMockSession({ id: 'session_1', status: 'completed' })];
      const serverSessions = [createMockSession({ id: 'session_1', status: 'running' })];

      // Simulate merge logic
      const merged = serverSessions.map((server) => {
        const local = localSessions.find((l) => l.id === server.id);
        if (local && TERMINAL_STATUSES.has(local.status) && !TERMINAL_STATUSES.has(server.status)) {
          return local;
        }
        return server;
      });

      expect(merged[0].status).toBe('completed');
    });

    it('scenario: User Events SSE sends stale update - terminal status protected', () => {
      // Session completes via Session SSE (agent_complete)
      // User Events SSE sends delayed session_status_change with 'running'
      // Local 'completed' should be preserved

      const sessions = [createMockSession({ id: 'session_1', status: 'completed' })];
      const staleUpdate = { id: 'session_1', new_status: 'running' };

      const updated = sessions.map((session) => {
        if (session.id !== staleUpdate.id) return session;
        if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(staleUpdate.new_status)) {
          return session;
        }
        return { ...session, status: staleUpdate.new_status };
      });

      expect(updated[0].status).toBe('completed');
    });

    it('scenario: Multiple rapid status changes - final terminal state preserved', () => {
      let sessions = [createMockSession({ id: 'session_1', status: 'running' })];

      const statusChanges = [
        { id: 'session_1', new_status: 'running' },
        { id: 'session_1', new_status: 'completed' },
        { id: 'session_1', new_status: 'running' }, // Stale event arrives late
      ];

      for (const change of statusChanges) {
        sessions = sessions.map((session) => {
          if (session.id !== change.id) return session;
          if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(change.new_status)) {
            return session;
          }
          return { ...session, status: change.new_status };
        });
      }

      // Final status should be 'completed', not 'running'
      expect(sessions[0].status).toBe('completed');
    });
  });
});
