import { http, HttpResponse } from 'msw';
import {
  createMockConfig,
  createMockDirectoryListing,
  createMockFileContent,
  createMockResult,
  createMockSession,
  createMockSessionList,
  createMockSkillsList,
  createMockTaskStarted,
  createMockTokenResponse,
  createMockUser,
} from './data';

const BASE_URL = 'http://localhost:40080';

export const handlers = [
  // =============================================================================
  // Authentication Endpoints
  // =============================================================================

  http.post(`${BASE_URL}/api/v1/auth/login`, async ({ request }) => {
    const body = await request.json() as { email: string; password: string };

    if (body.email === 'test@example.com' && body.password === 'password123') {
      return HttpResponse.json(createMockTokenResponse());
    }

    return HttpResponse.json(
      { detail: 'Invalid credentials' },
      { status: 401 }
    );
  }),

  http.post(`${BASE_URL}/api/v1/auth/logout`, () => {
    return HttpResponse.json({ status: 'ok' });
  }),

  http.get(`${BASE_URL}/api/v1/auth/me`, ({ request }) => {
    const authHeader = request.headers.get('Authorization');

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return HttpResponse.json(
        { detail: 'Not authenticated' },
        { status: 401 }
      );
    }

    return HttpResponse.json(createMockUser());
  }),

  http.post(`${BASE_URL}/api/v1/auth/token`, () => {
    return HttpResponse.json(createMockTokenResponse());
  }),

  // =============================================================================
  // Config Endpoints
  // =============================================================================

  http.get(`${BASE_URL}/api/v1/config`, () => {
    return HttpResponse.json(createMockConfig());
  }),

  // =============================================================================
  // Session Endpoints
  // =============================================================================

  http.get(`${BASE_URL}/api/v1/sessions`, () => {
    return HttpResponse.json(createMockSessionList(5));
  }),

  http.get(`${BASE_URL}/api/v1/sessions/:sessionId`, ({ params }) => {
    const { sessionId } = params;

    // Validate session ID format
    const sessionIdPattern = /^\d{8}_\d{6}_[a-f0-9]{8}$/;
    if (!sessionIdPattern.test(sessionId as string)) {
      return HttpResponse.json(
        { detail: 'Invalid session ID format' },
        { status: 400 }
      );
    }

    return HttpResponse.json(createMockSession({ id: sessionId as string }));
  }),

  http.post(`${BASE_URL}/api/v1/sessions/run`, async ({ request }) => {
    const body = await request.json() as { task: string; config?: { model?: string } };

    if (!body.task) {
      return HttpResponse.json(
        { detail: 'Task is required' },
        { status: 400 }
      );
    }

    return HttpResponse.json(createMockTaskStarted());
  }),

  http.post(`${BASE_URL}/api/v1/sessions/:sessionId/task`, async ({ params, request }) => {
    const { sessionId } = params;
    const body = await request.json() as { task: string; config?: { model?: string } };

    if (!body.task) {
      return HttpResponse.json(
        { detail: 'Task is required' },
        { status: 400 }
      );
    }

    return HttpResponse.json(
      createMockTaskStarted({
        session_id: sessionId as string,
        resumed_from: sessionId as string,
      })
    );
  }),

  http.post(`${BASE_URL}/api/v1/sessions/:sessionId/cancel`, ({ params }) => {
    const { sessionId } = params;
    return HttpResponse.json({ status: 'cancelled', session_id: sessionId });
  }),

  http.get(`${BASE_URL}/api/v1/sessions/:sessionId/result`, ({ params }) => {
    const { sessionId } = params;
    return HttpResponse.json(createMockResult({ session_id: sessionId as string }));
  }),

  http.get(`${BASE_URL}/api/v1/sessions/:sessionId/events/history`, ({ request }) => {
    const url = new URL(request.url);
    const after = url.searchParams.get('after');

    // Return empty array if requesting events after the last one
    if (after && parseInt(after, 10) >= 10) {
      return HttpResponse.json([]);
    }

    return HttpResponse.json([
      { type: 'agent_start', data: {}, timestamp: new Date().toISOString(), sequence: 1 },
      { type: 'user_message', data: { text: 'Test message' }, timestamp: new Date().toISOString(), sequence: 2 },
      { type: 'message', data: { text: 'Response' }, timestamp: new Date().toISOString(), sequence: 3 },
    ]);
  }),

  // =============================================================================
  // File Endpoints
  // =============================================================================

  http.get(`${BASE_URL}/api/v1/files/:sessionId/browse`, ({ request }) => {
    const url = new URL(request.url);
    const path = url.searchParams.get('path') || '';

    return HttpResponse.json(createMockDirectoryListing({ path }));
  }),

  http.get(`${BASE_URL}/api/v1/files/:sessionId/content`, ({ request }) => {
    const url = new URL(request.url);
    const path = url.searchParams.get('path') || '';

    return HttpResponse.json(createMockFileContent({ path, name: path.split('/').pop() || '' }));
  }),

  http.get(`${BASE_URL}/api/v1/files/:sessionId/download`, ({ request }) => {
    const url = new URL(request.url);
    const path = url.searchParams.get('path') || 'file.txt';

    return new HttpResponse(new Blob(['File content'], { type: 'text/plain' }), {
      headers: {
        'Content-Disposition': `attachment; filename="${path.split('/').pop()}"`,
        'Content-Type': 'text/plain',
      },
    });
  }),

  http.delete(`${BASE_URL}/api/v1/files/:sessionId`, ({ request }) => {
    const url = new URL(request.url);
    const path = url.searchParams.get('path') || '';

    return HttpResponse.json({ status: 'deleted', path });
  }),

  http.post(`${BASE_URL}/api/v1/files/:sessionId/upload`, async ({ request }) => {
    const formData = await request.formData();
    const files = formData.getAll('files');

    return HttpResponse.json({
      uploaded: files.map((file, index) => ({
        name: file instanceof File ? file.name : `file${index}.txt`,
        path: file instanceof File ? file.name : `file${index}.txt`,
        size: file instanceof File ? file.size : 100,
        mime_type: 'text/plain',
      })),
      total_count: files.length,
      errors: [],
    });
  }),

  // =============================================================================
  // Skills Endpoints
  // =============================================================================

  http.get(`${BASE_URL}/api/v1/skills`, () => {
    return HttpResponse.json(createMockSkillsList());
  }),
];

// Error handlers for testing error scenarios
export const errorHandlers = {
  networkError: http.get(`${BASE_URL}/api/v1/sessions`, () => {
    return HttpResponse.error();
  }),

  serverError: http.get(`${BASE_URL}/api/v1/sessions`, () => {
    return HttpResponse.json(
      { detail: 'Internal server error' },
      { status: 500 }
    );
  }),

  unauthorized: http.get(`${BASE_URL}/api/v1/sessions`, () => {
    return HttpResponse.json(
      { detail: 'Not authenticated' },
      { status: 401 }
    );
  }),

  notFound: http.get(`${BASE_URL}/api/v1/sessions/:sessionId`, () => {
    return HttpResponse.json(
      { detail: 'Session not found' },
      { status: 404 }
    );
  }),
};
