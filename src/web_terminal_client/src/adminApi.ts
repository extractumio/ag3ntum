/**
 * API client functions for admin and reseller dashboard endpoints.
 *
 * All functions follow the same pattern as api.ts — accept baseUrl + token,
 * return typed responses. Errors are thrown as strings with the server message.
 */
import type {
  AdminUserListResponse,
  ApiKey,
  AuditLogResponse,
  ConnectionTestResult,
  DeleteResellerResponse,
  DeleteUserResponse,
  PlatformConfig,
  PlatformStats,
  Reseller,
  ResellerListResponse,
  ResellerUser,
  ResellerUserListResponse,
  RetentionConfig,
  RetentionRunResult,
  SecurityConfig,
  SettingsMode,
  SkillListResponse,
  SpendingStatus,
  SSHFilters,
  SuspendResponse,
  UsageResponse,
  UserConfig,
  UserUsageResponse,
  WebhookDelivery,
  WebhookEndpoint,
  WhmcsMetrics,
} from './types/admin';
import { authenticatedRequest } from './utils/apiClient';

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

async function adminRequestText(baseUrl: string, path: string, token: string, options: RequestInit = {}): Promise<string> {
  const headers = new Headers(options.headers);
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

  return response.text();
}

// ---------------------------------------------------------------------------
// Admin — Platform
// ---------------------------------------------------------------------------

export async function getPlatformStats(baseUrl: string, token: string): Promise<PlatformStats> {
  return authenticatedRequest(baseUrl, '/api/v1/admin/stats', token);
}

export async function getPlatformConfig(baseUrl: string, token: string): Promise<PlatformConfig> {
  return authenticatedRequest(baseUrl, '/api/v1/admin/config', token);
}

export async function updatePlatformConfig(
  baseUrl: string, token: string,
  body: Partial<{ features: Record<string, unknown>; quotas: Record<string, unknown>; spending: Record<string, unknown> }>,
): Promise<PlatformConfig> {
  return authenticatedRequest(baseUrl, '/api/v1/admin/config', token, {
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
  return authenticatedRequest(baseUrl, `/api/v1/admin/resellers${toQS(params)}`, token);
}

export async function getReseller(baseUrl: string, token: string, id: string): Promise<Reseller> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/resellers/${id}`, token);
}

export async function createReseller(
  baseUrl: string, token: string, body: Record<string, unknown>,
): Promise<Reseller> {
  return authenticatedRequest(baseUrl, '/api/v1/admin/resellers', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function updateReseller(
  baseUrl: string, token: string, id: string, body: Record<string, unknown>,
): Promise<Reseller> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/resellers/${id}`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function suspendReseller(baseUrl: string, token: string, id: string, reason?: string): Promise<SuspendResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/resellers/${id}/suspend`, token, {
    method: 'POST', body: JSON.stringify(reason ? { reason } : {}),
  });
}

export async function unsuspendReseller(baseUrl: string, token: string, id: string): Promise<SuspendResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/resellers/${id}/unsuspend`, token, { method: 'POST' });
}

export async function deleteReseller(baseUrl: string, token: string, id: string): Promise<DeleteResellerResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/resellers/${id}?confirm=true`, token, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Admin — Users
// ---------------------------------------------------------------------------

export async function listAllUsers(
  baseUrl: string, token: string,
  params: { page?: number; per_page?: number; reseller_id?: string; status?: string; role?: string; search?: string } = {},
): Promise<AdminUserListResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users${toQS(params)}`, token);
}

export async function createAdminUser(
  baseUrl: string, token: string,
  body: { username: string; email: string; password: string; role?: string },
): Promise<Record<string, unknown>> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users${toQS({ role: body.role })}`, token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function suspendUser(baseUrl: string, token: string, id: string, reason?: string): Promise<SuspendResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users/${id}/suspend`, token, {
    method: 'POST', body: JSON.stringify(reason ? { reason } : {}),
  });
}

export async function unsuspendUser(baseUrl: string, token: string, id: string): Promise<SuspendResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users/${id}/unsuspend`, token, { method: 'POST' });
}

export async function changeAdminUserPassword(
  baseUrl: string, token: string, userId: string, newPassword: string,
): Promise<{ status: string }> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users/${userId}/change-password`, token, {
    method: 'POST', body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function deleteAdminUser(baseUrl: string, token: string, userId: string): Promise<DeleteUserResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users/${userId}?confirm=true`, token, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Admin — Usage + Audit
// ---------------------------------------------------------------------------

export async function getPlatformUsage(
  baseUrl: string, token: string,
  params: { period?: string; reseller_id?: string; group_by?: string; start?: string; end?: string } = {},
): Promise<UsageResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/usage${toQS(params)}`, token);
}

export async function getAuditLog(
  baseUrl: string, token: string,
  params: { page?: number; per_page?: number; reseller_id?: string; action?: string; start?: string; end?: string } = {},
): Promise<AuditLogResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/audit${toQS(params)}`, token);
}

// ---------------------------------------------------------------------------
// Admin — Retention
// ---------------------------------------------------------------------------

export async function getRetentionConfig(baseUrl: string, token: string): Promise<RetentionConfig> {
  return authenticatedRequest(baseUrl, '/api/v1/admin/retention', token);
}

export async function updateRetentionConfig(
  baseUrl: string, token: string, body: Partial<RetentionConfig>,
): Promise<RetentionConfig> {
  return authenticatedRequest(baseUrl, '/api/v1/admin/retention', token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function runRetention(baseUrl: string, token: string): Promise<RetentionRunResult> {
  return authenticatedRequest(baseUrl, '/api/v1/admin/retention/run', token, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Reseller — Profile & Connection
// ---------------------------------------------------------------------------

export async function getResellerProfile(baseUrl: string, token: string): Promise<Reseller> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/profile', token);
}

export async function testConnection(baseUrl: string, token: string): Promise<ConnectionTestResult> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/test-connection', token);
}

export async function getResellerSpending(baseUrl: string, token: string): Promise<SpendingStatus> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/spending', token);
}

// ---------------------------------------------------------------------------
// Reseller — Users
// ---------------------------------------------------------------------------

export async function listResellerUsers(
  baseUrl: string, token: string,
  params: { page?: number; per_page?: number; status?: string; search?: string } = {},
): Promise<ResellerUserListResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users${toQS(params)}`, token);
}

export async function getResellerUser(baseUrl: string, token: string, userId: string): Promise<ResellerUser> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}`, token);
}

export async function createResellerUser(
  baseUrl: string, token: string, body: Record<string, unknown>,
): Promise<ResellerUser> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/users', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function updateResellerUser(
  baseUrl: string, token: string, userId: string, body: Record<string, unknown>,
): Promise<ResellerUser> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function suspendResellerUser(baseUrl: string, token: string, userId: string, reason?: string): Promise<SuspendResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/suspend`, token, {
    method: 'POST', body: JSON.stringify(reason ? { reason } : {}),
  });
}

export async function unsuspendResellerUser(baseUrl: string, token: string, userId: string): Promise<SuspendResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/unsuspend`, token, { method: 'POST' });
}

export async function changeResellerUserPassword(
  baseUrl: string, token: string, userId: string, newPassword: string,
): Promise<{ status: string }> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/change-password`, token, {
    method: 'POST', body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function deleteResellerUser(baseUrl: string, token: string, userId: string): Promise<DeleteUserResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}`, token, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Reseller — User Config
// ---------------------------------------------------------------------------

export async function getUserConfig(baseUrl: string, token: string, userId: string): Promise<UserConfig> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/config`, token);
}

export async function updateUserConfig(
  baseUrl: string, token: string, userId: string, body: Record<string, unknown>,
): Promise<UserConfig> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/config`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function getUserSecurity(baseUrl: string, token: string, userId: string): Promise<SecurityConfig> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/security`, token);
}

export async function updateUserSecurity(
  baseUrl: string, token: string, userId: string, body: Partial<SecurityConfig>,
): Promise<SecurityConfig> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/security`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function getUserSSHFilters(baseUrl: string, token: string, userId: string): Promise<SSHFilters> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/ssh-filters`, token);
}

export async function updateUserSSHFilters(
  baseUrl: string, token: string, userId: string, body: Partial<SSHFilters>,
): Promise<SSHFilters> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/ssh-filters`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function getUserEnvVars(baseUrl: string, token: string, userId: string): Promise<{ env_vars: Record<string, string> }> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/env-vars`, token);
}

export async function setUserEnvVars(
  baseUrl: string, token: string, userId: string, envVars: Record<string, string>,
): Promise<{ env_vars: Record<string, string> }> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/env-vars`, token, {
    method: 'PUT', body: JSON.stringify({ env_vars: envVars }),
  });
}

export async function deleteUserEnvVar(baseUrl: string, token: string, userId: string, name: string): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/env-vars/${encodeURIComponent(name)}`, token, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Reseller — User Spending
// ---------------------------------------------------------------------------

export async function getUserSpending(baseUrl: string, token: string, userId: string): Promise<SpendingStatus> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/spending`, token);
}

export async function setUserSpendingLimits(
  baseUrl: string, token: string, userId: string,
  body: { max_monthly_usd?: number | null; max_daily_usd?: number | null; max_per_session_usd?: number | null },
): Promise<SpendingStatus> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/spending-limits`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function setUserSettingsMode(
  baseUrl: string, token: string, userId: string,
  body: { mode: SettingsMode; allowed_overrides?: string[] },
): Promise<Record<string, unknown>> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/settings-mode`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Reseller — Skills
// ---------------------------------------------------------------------------

export async function getUserSkills(baseUrl: string, token: string, userId: string): Promise<SkillListResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/skills`, token);
}

export async function assignUserSkill(
  baseUrl: string, token: string, userId: string,
  body: { name: string; source?: string },
): Promise<Record<string, unknown>> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/skills`, token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function removeUserSkill(baseUrl: string, token: string, userId: string, skillName: string): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/skills/${encodeURIComponent(skillName)}`, token, {
    method: 'DELETE',
  });
}

export async function enableUserSkill(baseUrl: string, token: string, userId: string, skillName: string): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/skills/${encodeURIComponent(skillName)}/enable`, token, {
    method: 'POST',
  });
}

export async function disableUserSkill(baseUrl: string, token: string, userId: string, skillName: string): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/skills/${encodeURIComponent(skillName)}/disable`, token, {
    method: 'POST',
  });
}

export async function getSkillLibrary(baseUrl: string, token: string): Promise<SkillListResponse> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/skill-library', token);
}

export async function uploadSkill(
  baseUrl: string, token: string,
  body: { name: string; description?: string; content: string },
): Promise<Record<string, unknown>> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/skill-library', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function deleteLibrarySkill(baseUrl: string, token: string, skillName: string): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/skill-library/${encodeURIComponent(skillName)}`, token, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Reseller — API Keys
// ---------------------------------------------------------------------------

export async function listApiKeys(baseUrl: string, token: string): Promise<{ api_keys: ApiKey[] }> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/api-keys', token);
}

export async function createApiKey(
  baseUrl: string, token: string,
  body: { name: string; scopes: string[]; ip_allowlist?: string[] | null; rate_limit_per_minute?: number; expires_at?: string },
): Promise<ApiKey & { key: string }> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/api-keys', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function revokeApiKey(baseUrl: string, token: string, id: string): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/api-keys/${id}/revoke`, token, { method: 'POST' });
}

export async function rotateApiKey(baseUrl: string, token: string, id: string): Promise<{ key: string }> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/api-keys/${id}/rotate`, token, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Reseller — Usage
// ---------------------------------------------------------------------------

export async function getResellerUsage(
  baseUrl: string, token: string,
  params: { period?: string } = {},
): Promise<UsageResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/usage${toQS(params)}`, token);
}

export async function getUserUsage(
  baseUrl: string, token: string, userId: string,
  params: { period?: string } = {},
): Promise<UserUsageResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/users/${userId}/usage${toQS(params)}`, token);
}

export async function getResellerUsageMetrics(baseUrl: string, token: string): Promise<WhmcsMetrics> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/usage/metrics', token);
}

export async function exportUsage(
  baseUrl: string, token: string,
  params: { format?: string; period?: string } = {},
): Promise<string> {
  return adminRequestText(baseUrl, `/api/v1/reseller/usage/export${toQS(params)}`, token);
}

// ---------------------------------------------------------------------------
// Reseller — Webhooks
// ---------------------------------------------------------------------------

export async function listWebhooks(baseUrl: string, token: string): Promise<{ webhooks: WebhookEndpoint[] }> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/webhooks', token);
}

export async function createWebhook(
  baseUrl: string, token: string, body: { url: string; events: string[]; description?: string },
): Promise<WebhookEndpoint & { secret: string }> {
  return authenticatedRequest(baseUrl, '/api/v1/reseller/webhooks', token, {
    method: 'POST', body: JSON.stringify(body),
  });
}

export async function updateWebhook(
  baseUrl: string, token: string, id: string,
  body: { url?: string; events?: string[]; is_active?: boolean; description?: string },
): Promise<WebhookEndpoint> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/webhooks/${id}`, token, {
    method: 'PUT', body: JSON.stringify(body),
  });
}

export async function deleteWebhook(baseUrl: string, token: string, id: string): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/webhooks/${id}`, token, { method: 'DELETE' });
}

export async function testWebhook(baseUrl: string, token: string, id: string): Promise<{ status: string }> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/webhooks/${id}/test`, token, { method: 'POST' });
}

export async function getWebhookDeliveries(
  baseUrl: string, token: string, id: string,
): Promise<{ deliveries: WebhookDelivery[] }> {
  return authenticatedRequest(baseUrl, `/api/v1/reseller/webhooks/${id}/deliveries`, token);
}

// ---------------------------------------------------------------------------
// Admin — User Features
// ---------------------------------------------------------------------------

export interface UserFeaturesResponse {
  effective: Record<string, unknown>;
  user_overrides: Record<string, unknown>;
  toggleable: string[];
}

export async function getUserFeatures(
  baseUrl: string, token: string, userId: string,
): Promise<UserFeaturesResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users/${userId}/features`, token);
}

export async function updateUserFeatures(
  baseUrl: string, token: string, userId: string, features: Record<string, unknown>,
): Promise<UserFeaturesResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users/${userId}/features`, token, {
    method: 'PUT',
    body: JSON.stringify(features),
  });
}

// ---------------------------------------------------------------------------
// Admin — User SSH Profiles
// ---------------------------------------------------------------------------

import type { SSHProfileList } from './types/ssh';

export async function adminListUserSSHProfiles(
  baseUrl: string, token: string, userId: string,
): Promise<SSHProfileList> {
  return authenticatedRequest(baseUrl, `/api/v1/admin/users/${userId}/ssh-profiles`, token);
}

export async function adminDeleteUserSSHProfile(
  baseUrl: string, token: string, userId: string, profileId: string,
): Promise<void> {
  return authenticatedRequest(
    baseUrl,
    `/api/v1/admin/users/${userId}/ssh-profiles/${profileId}?confirm=true`,
    token,
    { method: 'DELETE' },
  );
}
