/**
 * API client functions for admin and reseller dashboard endpoints.
 *
 * All functions follow the same pattern as api.ts — accept baseUrl + token,
 * return typed responses. Errors are thrown as strings with the server message.
 */
import type {
  AdminUserListResponse,
  AuditLogResponse,
  PlatformConfig,
  PlatformStats,
  Reseller,
  ResellerListResponse,
  RetentionConfig,
  RetentionRunResult,
  UsageResponse,
  WebhookDelivery,
  WebhookEndpoint,
  WhmcsMetrics,
} from './types/admin';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toQS(params: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, val] of Object.entries(params)) {
    if (val !== undefined && val !== '') qs.set(key, String(val));
  }
  const s = qs.toString();
  return s ? `?${s}` : '';
}

async function adminRequest<T>(
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

// ---------------------------------------------------------------------------
// Admin — Platform
// ---------------------------------------------------------------------------

export async function getPlatformStats(baseUrl: string, token: string): Promise<PlatformStats> {
  return adminRequest(baseUrl, '/api/v1/admin/stats', token);
}

export async function getPlatformConfig(baseUrl: string, token: string): Promise<PlatformConfig> {
  return adminRequest(baseUrl, '/api/v1/admin/config', token);
}

export async function updatePlatformConfig(
  baseUrl: string, token: string,
  body: Partial<{ features: Record<string, unknown>; quotas: Record<string, unknown>; spending: Record<string, unknown> }>,
): Promise<PlatformConfig> {
  return adminRequest(baseUrl, '/api/v1/admin/config', token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Admin — Resellers
// ---------------------------------------------------------------------------

export async function listResellers(
  baseUrl: string, token: string,
  params: { page?: number; per_page?: number; status?: string; search?: string } = {},
): Promise<ResellerListResponse> {
  return adminRequest(baseUrl, `/api/v1/admin/resellers${toQS(params)}`, token);
}

export async function getReseller(baseUrl: string, token: string, id: string): Promise<Reseller> {
  return adminRequest(baseUrl, `/api/v1/admin/resellers/${id}`, token);
}

export async function createReseller(
  baseUrl: string, token: string, body: Record<string, unknown>,
): Promise<Reseller> {
  return adminRequest(baseUrl, '/api/v1/admin/resellers', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function updateReseller(
  baseUrl: string, token: string, id: string, body: Record<string, unknown>,
): Promise<Reseller> {
  return adminRequest(baseUrl, `/api/v1/admin/resellers/${id}`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function suspendReseller(baseUrl: string, token: string, id: string, reason?: string) {
  return adminRequest(baseUrl, `/api/v1/admin/resellers/${id}/suspend`, token, {
    method: 'POST', body: JSON.stringify(reason ? { reason } : {}),
  });
}

export async function unsuspendReseller(baseUrl: string, token: string, id: string) {
  return adminRequest(baseUrl, `/api/v1/admin/resellers/${id}/unsuspend`, token, { method: 'POST' });
}

export async function deleteReseller(baseUrl: string, token: string, id: string) {
  return adminRequest(baseUrl, `/api/v1/admin/resellers/${id}?confirm=true`, token, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Admin — Users
// ---------------------------------------------------------------------------

export async function listAllUsers(
  baseUrl: string, token: string,
  params: { page?: number; per_page?: number; reseller_id?: string; status?: string; role?: string; search?: string } = {},
): Promise<AdminUserListResponse> {
  return adminRequest(baseUrl, `/api/v1/admin/users${toQS(params)}`, token);
}

export async function suspendUser(baseUrl: string, token: string, id: string) {
  return adminRequest(baseUrl, `/api/v1/admin/users/${id}/suspend`, token, { method: 'POST' });
}

export async function unsuspendUser(baseUrl: string, token: string, id: string) {
  return adminRequest(baseUrl, `/api/v1/admin/users/${id}/unsuspend`, token, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Admin — Usage + Audit
// ---------------------------------------------------------------------------

export async function getPlatformUsage(
  baseUrl: string, token: string,
  params: { period?: string; reseller_id?: string } = {},
): Promise<UsageResponse> {
  return adminRequest(baseUrl, `/api/v1/admin/usage${toQS(params)}`, token);
}

export async function getAuditLog(
  baseUrl: string, token: string,
  params: { page?: number; per_page?: number; reseller_id?: string; action?: string } = {},
): Promise<AuditLogResponse> {
  return adminRequest(baseUrl, `/api/v1/admin/audit${toQS(params)}`, token);
}

// ---------------------------------------------------------------------------
// Admin — Retention
// ---------------------------------------------------------------------------

export async function getRetentionConfig(baseUrl: string, token: string): Promise<RetentionConfig> {
  return adminRequest(baseUrl, '/api/v1/admin/retention', token);
}

export async function updateRetentionConfig(
  baseUrl: string, token: string, body: Partial<RetentionConfig>,
): Promise<RetentionConfig> {
  return adminRequest(baseUrl, '/api/v1/admin/retention', token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function runRetention(baseUrl: string, token: string): Promise<RetentionRunResult> {
  return adminRequest(baseUrl, '/api/v1/admin/retention/run', token, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Reseller — Profile & Users
// ---------------------------------------------------------------------------

export async function getResellerProfile(baseUrl: string, token: string) {
  return adminRequest(baseUrl, '/api/v1/reseller/profile', token);
}

export async function listResellerUsers(
  baseUrl: string, token: string,
  params: { page?: number; per_page?: number; status?: string; search?: string } = {},
) {
  return adminRequest(baseUrl, `/api/v1/reseller/users${toQS(params)}`, token);
}

export async function getResellerUser(
  baseUrl: string, token: string, userId: string,
) {
  return adminRequest(baseUrl, `/api/v1/reseller/users/${userId}`, token);
}

export async function createResellerUser(
  baseUrl: string, token: string, body: Record<string, unknown>,
) {
  return adminRequest(baseUrl, '/api/v1/reseller/users', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Reseller — API Keys
// ---------------------------------------------------------------------------

export async function listApiKeys(baseUrl: string, token: string) {
  return adminRequest(baseUrl, '/api/v1/reseller/api-keys', token);
}

export async function createApiKey(baseUrl: string, token: string, body: Record<string, unknown>) {
  return adminRequest(baseUrl, '/api/v1/reseller/api-keys', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function revokeApiKey(baseUrl: string, token: string, id: string) {
  return adminRequest(baseUrl, `/api/v1/reseller/api-keys/${id}`, token, { method: 'DELETE' });
}

export async function rotateApiKey(baseUrl: string, token: string, id: string) {
  return adminRequest(baseUrl, `/api/v1/reseller/api-keys/${id}/rotate`, token, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Reseller — Usage
// ---------------------------------------------------------------------------

export async function getResellerUsageMetrics(baseUrl: string, token: string): Promise<WhmcsMetrics> {
  return adminRequest(baseUrl, '/api/v1/reseller/usage/metrics', token);
}

// ---------------------------------------------------------------------------
// Reseller — Webhooks
// ---------------------------------------------------------------------------

export async function listWebhooks(baseUrl: string, token: string): Promise<{ webhooks: WebhookEndpoint[] }> {
  return adminRequest(baseUrl, '/api/v1/reseller/webhooks', token);
}

export async function createWebhook(
  baseUrl: string, token: string, body: { url: string; events: string[]; description?: string },
): Promise<WebhookEndpoint & { secret: string }> {
  return adminRequest(baseUrl, '/api/v1/reseller/webhooks', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function deleteWebhook(baseUrl: string, token: string, id: string) {
  return adminRequest(baseUrl, `/api/v1/reseller/webhooks/${id}`, token, { method: 'DELETE' });
}

export async function getWebhookDeliveries(
  baseUrl: string, token: string, id: string,
): Promise<{ deliveries: WebhookDelivery[] }> {
  return adminRequest(baseUrl, `/api/v1/reseller/webhooks/${id}/deliveries`, token);
}
