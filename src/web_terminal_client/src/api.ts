import type {
  ConfigResponse,
  DirectoryListing,
  FileContentResponse,
  FileSortField,
  ResultResponse,
  SessionListResponse,
  SessionResponse,
  SkillsListResponse,
  SortOrder,
  SSEEvent,
  TaskStartedResponse,
  TokenResponse,
  UploadResponse,
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

/**
 * Download a file with authentication.
 * Fetches the file as a blob with auth headers and triggers a browser download.
 */
export async function downloadFile(
  baseUrl: string,
  token: string,
  sessionId: string,
  filePath: string,
  fileName?: string
): Promise<void> {
  const params = new URLSearchParams({ path: filePath });
  const url = `${baseUrl}/api/v1/files/${sessionId}/download?${params.toString()}`;

  const response = await fetch(url, {
    headers: {
      'Authorization': `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error(`Download failed: ${response.statusText}`);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fileName || filePath.split('/').pop() || 'download';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  // Clean up the object URL after a short delay
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
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

/**
 * Upload files to a session's workspace.
 * Uses multipart/form-data for file upload.
 *
 * @param baseUrl - API base URL
 * @param token - JWT token for authentication
 * @param sessionId - Session ID to upload to
 * @param files - Array of File objects to upload
 * @param path - Target directory path (relative to workspace root, default: root)
 * @param overwrite - Whether to overwrite existing files (default: false)
 * @returns Upload response with list of uploaded files and any errors
 */
export async function uploadFiles(
  baseUrl: string,
  token: string,
  sessionId: string,
  files: File[],
  path: string = '',
  overwrite: boolean = false
): Promise<UploadResponse> {
  const formData = new FormData();

  // Append all files
  files.forEach((file) => {
    formData.append('files', file);
  });

  // Append path and overwrite options
  if (path) {
    formData.append('path', path);
  }
  if (overwrite) {
    formData.append('overwrite', 'true');
  }

  const response = await fetch(
    `${baseUrl}/api/v1/files/${sessionId}/upload`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        // Note: Don't set Content-Type for FormData - browser sets it with boundary
      },
      body: formData,
    }
  );

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Upload failed: ${response.status}`);
  }

  return response.json() as Promise<UploadResponse>;
}

// =============================================================================
// Skills API Functions
// =============================================================================

export async function getSkills(
  baseUrl: string,
  token: string
): Promise<SkillsListResponse> {
  return apiRequest<SkillsListResponse>(baseUrl, '/api/v1/skills', {}, token);
}

// =============================================================================
// Cached API Functions
// =============================================================================

import { apiCache } from './apiCache';

/**
 * Get sessions with caching (TTL: 1 minute, stale-while-revalidate).
 * Use invalidateSessionsCache() when you know sessions have changed.
 */
export async function listSessionsCached(
  baseUrl: string,
  token: string
): Promise<SessionListResponse> {
  return apiCache.get('sessions', () => listSessions(baseUrl, token));
}

/**
 * Invalidate the sessions cache (call after creating, updating, or completing sessions).
 */
export function invalidateSessionsCache(): void {
  apiCache.invalidate('sessions');
}

/**
 * Get skills with caching (TTL: 5 minutes).
 * Skills rarely change during a session.
 */
export async function getSkillsCached(
  baseUrl: string,
  token: string
): Promise<SkillsListResponse> {
  return apiCache.get('skills', () => getSkills(baseUrl, token));
}

/**
 * Invalidate the skills cache (call if skills are added/removed).
 */
export function invalidateSkillsCache(): void {
  apiCache.invalidate('skills');
}

/**
 * Invalidate all API caches (call on logout or user change).
 */
export function invalidateAllCaches(): void {
  apiCache.invalidateAll();
}
