import type {
  ConfigResponse,
  DirectoryListing,
  FileContentResponse,
  FileSortField,
  ResultResponse,
  SessionListResponse,
  SessionResponse,
  SortOrder,
  SSEEvent,
  TaskStartedResponse,
  TokenResponse,
  User,
} from './types';

async function apiRequest<T>(
  baseUrl: string,
  path: string,
  options: RequestInit = {},
  token?: string
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function fetchToken(baseUrl: string): Promise<TokenResponse> {
  return apiRequest<TokenResponse>(baseUrl, '/api/v1/auth/token', { method: 'POST' });
}

export async function getConfig(baseUrl: string): Promise<ConfigResponse> {
  return apiRequest<ConfigResponse>(baseUrl, '/api/v1/config', {});
}

export async function login(
  baseUrl: string,
  email: string,
  password: string
): Promise<TokenResponse> {
  return apiRequest<TokenResponse>(baseUrl, '/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(baseUrl: string, token: string): Promise<void> {
  await apiRequest<{ status: string }>(
    baseUrl,
    '/api/v1/auth/logout',
    { method: 'POST' },
    token
  );
}

export async function getCurrentUser(
  baseUrl: string,
  token: string
): Promise<User> {
  return apiRequest<User>(baseUrl, '/api/v1/auth/me', {}, token);
}

export async function listSessions(
  baseUrl: string,
  token: string
): Promise<SessionListResponse> {
  return apiRequest<SessionListResponse>(baseUrl, '/api/v1/sessions', {}, token);
}

export async function getSession(
  baseUrl: string,
  token: string,
  sessionId: string
): Promise<SessionResponse> {
  return apiRequest<SessionResponse>(baseUrl, `/api/v1/sessions/${sessionId}`, {}, token);
}

export async function runTask(
  baseUrl: string,
  token: string,
  task: string,
  model?: string
): Promise<TaskStartedResponse> {
  return apiRequest<TaskStartedResponse>(
    baseUrl,
    '/api/v1/sessions/run',
    {
      method: 'POST',
      body: JSON.stringify({
        task,
        config: model ? { model } : {},
      }),
    },
    token
  );
}

export async function cancelSession(
  baseUrl: string,
  token: string,
  sessionId: string
): Promise<void> {
  await apiRequest<{ status: string }>(
    baseUrl,
    `/api/v1/sessions/${sessionId}/cancel`,
    { method: 'POST' },
    token
  );
}

export async function getResult(
  baseUrl: string,
  token: string,
  sessionId: string
): Promise<ResultResponse> {
  return apiRequest<ResultResponse>(baseUrl, `/api/v1/sessions/${sessionId}/result`, {}, token);
}

export async function getSessionEvents(
  baseUrl: string,
  token: string,
  sessionId: string,
  after?: number
): Promise<SSEEvent[]> {
  const params = new URLSearchParams();
  if (after !== undefined) {
    params.set('after', String(after));
  }
  const query = params.toString();
  const path = `/api/v1/sessions/${sessionId}/events/history${query ? `?${query}` : ''}`;
  return apiRequest<SSEEvent[]>(baseUrl, path, {}, token);
}

export async function continueTask(
  baseUrl: string,
  token: string,
  sessionId: string,
  task: string,
  model?: string
): Promise<TaskStartedResponse> {
  return apiRequest<TaskStartedResponse>(
    baseUrl,
    `/api/v1/sessions/${sessionId}/task`,
    {
      method: 'POST',
      body: JSON.stringify({
        task,
        config: model ? { model } : {},
      }),
    },
    token
  );
}

// =============================================================================
// File Explorer API Functions
// =============================================================================

export async function browseFiles(
  baseUrl: string,
  token: string,
  sessionId: string,
  path: string = '',
  options: {
    includeHidden?: boolean;
    sortBy?: FileSortField;
    sortOrder?: SortOrder;
    limit?: number;
  } = {}
): Promise<DirectoryListing> {
  const params = new URLSearchParams();
  if (path) params.set('path', path);
  if (options.includeHidden) params.set('include_hidden', 'true');
  if (options.sortBy) params.set('sort_by', options.sortBy);
  if (options.sortOrder) params.set('sort_order', options.sortOrder);
  if (options.limit) params.set('limit', String(options.limit));

  const query = params.toString();
  const apiPath = `/api/v1/files/${sessionId}/browse${query ? `?${query}` : ''}`;
  return apiRequest<DirectoryListing>(baseUrl, apiPath, {}, token);
}

export async function getFileContent(
  baseUrl: string,
  token: string,
  sessionId: string,
  filePath: string
): Promise<FileContentResponse> {
  const params = new URLSearchParams({ path: filePath });
  const apiPath = `/api/v1/files/${sessionId}/content?${params.toString()}`;
  return apiRequest<FileContentResponse>(baseUrl, apiPath, {}, token);
}

export function getFileDownloadUrl(
  baseUrl: string,
  token: string,
  sessionId: string,
  filePath: string
): string {
  const params = new URLSearchParams({ path: filePath });
  return `${baseUrl}/api/v1/files/${sessionId}/download?${params.toString()}`;
}

export async function deleteFile(
  baseUrl: string,
  token: string,
  sessionId: string,
  filePath: string
): Promise<{ status: string; path: string }> {
  const params = new URLSearchParams({ path: filePath });
  const apiPath = `/api/v1/files/${sessionId}?${params.toString()}`;
  return apiRequest<{ status: string; path: string }>(
    baseUrl,
    apiPath,
    { method: 'DELETE' },
    token
  );
}
