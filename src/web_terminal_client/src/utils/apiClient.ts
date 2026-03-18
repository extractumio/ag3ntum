/**
 * Shared authenticated HTTP request helper.
 *
 * Used by sshApi.ts and adminApi.ts — handles auth headers,
 * JSON error parsing, and 204 (No Content) responses.
 */
export async function authenticatedRequest<T>(
  baseUrl: string,
  path: string,
  token: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(`${baseUrl}${path}`, { ...options, headers });

  if (!response.ok) {
    const text = await response.text();
    let message = `HTTP ${response.status}`;
    try {
      const json = JSON.parse(text);
      message = json.detail || message;
    } catch { /* use default */ }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}
