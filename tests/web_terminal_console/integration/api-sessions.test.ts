import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import {
  cancelSession,
  continueTask,
  getResult,
  getSession,
  getSessionEvents,
  listSessions,
  runTask,
} from '../../../src/web_terminal_client/src/api';
import { createMockSession, createMockSessionList, VALID_SESSION_IDS, INVALID_SESSION_IDS } from '../mocks/data';
import { server } from '../mocks/server';

const BASE_URL = 'http://localhost:40080';
const TOKEN = 'valid-token';

describe('Sessions API', () => {
  describe('listSessions', () => {
    it('returns list of sessions', async () => {
      const result = await listSessions(BASE_URL, TOKEN);

      expect(result.sessions).toHaveLength(5);
      expect(result.total).toBe(5);
      expect(result.sessions[0]).toHaveProperty('id');
      expect(result.sessions[0]).toHaveProperty('status');
      expect(result.sessions[0]).toHaveProperty('task');
    });

    it('returns empty list when no sessions', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/sessions`, () => {
          return HttpResponse.json({ sessions: [], total: 0 });
        })
      );

      const result = await listSessions(BASE_URL, TOKEN);

      expect(result.sessions).toHaveLength(0);
      expect(result.total).toBe(0);
    });

    it('throws error on unauthorized', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/sessions`, () => {
          return HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 });
        })
      );

      await expect(listSessions(BASE_URL, TOKEN)).rejects.toThrow();
    });

    it('throws error on server error', async () => {
      server.use(
        http.get(`${BASE_URL}/api/v1/sessions`, () => {
          return HttpResponse.json({ detail: 'Internal error' }, { status: 500 });
        })
      );

      await expect(listSessions(BASE_URL, TOKEN)).rejects.toThrow();
    });
  });

  describe('getSession', () => {
    it('returns session by ID', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      const result = await getSession(BASE_URL, TOKEN, sessionId);

      expect(result.id).toBe(sessionId);
      expect(result).toHaveProperty('status');
      expect(result).toHaveProperty('task');
      expect(result).toHaveProperty('created_at');
    });

    it('returns session with all fields', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      server.use(
        http.get(`${BASE_URL}/api/v1/sessions/${sessionId}`, () => {
          return HttpResponse.json(createMockSession({
            id: sessionId,
            status: 'running',
            task: 'Test task',
            model: 'claude-3-sonnet',
            num_turns: 10,
            duration_ms: 5000,
            total_cost_usd: 0.05,
          }));
        })
      );

      const result = await getSession(BASE_URL, TOKEN, sessionId);

      expect(result.id).toBe(sessionId);
      expect(result.status).toBe('running');
      expect(result.task).toBe('Test task');
      expect(result.model).toBe('claude-3-sonnet');
      expect(result.num_turns).toBe(10);
    });

    it('throws error for invalid session ID format', async () => {
      await expect(getSession(BASE_URL, TOKEN, 'invalid-id')).rejects.toThrow();
    });

    it('throws error for non-existent session', async () => {
      const sessionId = '20240115_143052_00000000';
      server.use(
        http.get(`${BASE_URL}/api/v1/sessions/${sessionId}`, () => {
          return HttpResponse.json({ detail: 'Session not found' }, { status: 404 });
        })
      );

      await expect(getSession(BASE_URL, TOKEN, sessionId)).rejects.toThrow();
    });
  });

  describe('runTask', () => {
    it('starts a new task', async () => {
      const result = await runTask(BASE_URL, TOKEN, 'Create a hello world script');

      expect(result.session_id).toBeTruthy();
      expect(result.status).toBe('running');
      expect(result.message).toBeTruthy();
    });

    it('starts task with specific model', async () => {
      const result = await runTask(BASE_URL, TOKEN, 'Test task', 'claude-3-opus');

      expect(result.session_id).toBeTruthy();
      expect(result.status).toBe('running');
    });

    it('throws error for empty task', async () => {
      server.use(
        http.post(`${BASE_URL}/api/v1/sessions/run`, async ({ request }) => {
          const body = await request.json() as { task: string };
          if (!body.task) {
            return HttpResponse.json({ detail: 'Task is required' }, { status: 400 });
          }
          return HttpResponse.json({ session_id: 'test', status: 'running', message: 'ok' });
        })
      );

      await expect(runTask(BASE_URL, TOKEN, '')).rejects.toThrow();
    });

    it('handles rate limiting', async () => {
      server.use(
        http.post(`${BASE_URL}/api/v1/sessions/run`, () => {
          return HttpResponse.json(
            { detail: 'Rate limit exceeded' },
            { status: 429 }
          );
        })
      );

      await expect(runTask(BASE_URL, TOKEN, 'Test task')).rejects.toThrow();
    });
  });

  describe('continueTask', () => {
    it('continues an existing session', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      const result = await continueTask(BASE_URL, TOKEN, sessionId, 'Continue with step 2');

      expect(result.session_id).toBe(sessionId);
      expect(result.resumed_from).toBe(sessionId);
    });

    it('continues with specific model', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      const result = await continueTask(BASE_URL, TOKEN, sessionId, 'Continue', 'claude-3-haiku');

      expect(result.session_id).toBe(sessionId);
    });

    it('throws error for non-resumable session', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      server.use(
        http.post(`${BASE_URL}/api/v1/sessions/${sessionId}/task`, () => {
          return HttpResponse.json(
            { detail: 'Session is not resumable' },
            { status: 400 }
          );
        })
      );

      await expect(continueTask(BASE_URL, TOKEN, sessionId, 'Continue')).rejects.toThrow();
    });
  });

  describe('cancelSession', () => {
    it('cancels a running session', async () => {
      const sessionId = VALID_SESSION_IDS[0];

      // Should not throw
      await expect(cancelSession(BASE_URL, TOKEN, sessionId)).resolves.not.toThrow();
    });

    it('handles cancellation of already cancelled session', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      server.use(
        http.post(`${BASE_URL}/api/v1/sessions/${sessionId}/cancel`, () => {
          return HttpResponse.json(
            { detail: 'Session already cancelled' },
            { status: 400 }
          );
        })
      );

      await expect(cancelSession(BASE_URL, TOKEN, sessionId)).rejects.toThrow();
    });

    it('handles cancellation of completed session', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      server.use(
        http.post(`${BASE_URL}/api/v1/sessions/${sessionId}/cancel`, () => {
          return HttpResponse.json(
            { detail: 'Session already completed' },
            { status: 400 }
          );
        })
      );

      await expect(cancelSession(BASE_URL, TOKEN, sessionId)).rejects.toThrow();
    });
  });

  describe('getResult', () => {
    it('returns session result', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      const result = await getResult(BASE_URL, TOKEN, sessionId);

      expect(result.session_id).toBe(sessionId);
      expect(result).toHaveProperty('status');
      expect(result).toHaveProperty('output');
      expect(result).toHaveProperty('error');
      expect(result).toHaveProperty('comments');
      expect(result).toHaveProperty('result_files');
    });

    it('returns result with metrics', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      const result = await getResult(BASE_URL, TOKEN, sessionId);

      expect(result.metrics).toBeTruthy();
      expect(result.metrics).toHaveProperty('duration_ms');
      expect(result.metrics).toHaveProperty('num_turns');
      expect(result.metrics).toHaveProperty('total_cost_usd');
    });

    it('throws error for running session', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      server.use(
        http.get(`${BASE_URL}/api/v1/sessions/${sessionId}/result`, () => {
          return HttpResponse.json(
            { detail: 'Session still running' },
            { status: 400 }
          );
        })
      );

      await expect(getResult(BASE_URL, TOKEN, sessionId)).rejects.toThrow();
    });
  });

  describe('getSessionEvents', () => {
    it('returns event history', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      const events = await getSessionEvents(BASE_URL, TOKEN, sessionId);

      expect(Array.isArray(events)).toBe(true);
      expect(events.length).toBeGreaterThan(0);
      expect(events[0]).toHaveProperty('type');
      expect(events[0]).toHaveProperty('data');
      expect(events[0]).toHaveProperty('timestamp');
      expect(events[0]).toHaveProperty('sequence');
    });

    it('returns events after specific sequence', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      const events = await getSessionEvents(BASE_URL, TOKEN, sessionId, 10);

      // Handler returns empty array for after >= 10
      expect(Array.isArray(events)).toBe(true);
      expect(events.length).toBe(0);
    });

    it('handles no events', async () => {
      const sessionId = VALID_SESSION_IDS[0];
      server.use(
        http.get(`${BASE_URL}/api/v1/sessions/${sessionId}/events/history`, () => {
          return HttpResponse.json([]);
        })
      );

      const events = await getSessionEvents(BASE_URL, TOKEN, sessionId);

      expect(events).toHaveLength(0);
    });
  });
});
