/** TypeScript interfaces for admin and reseller API responses. */

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export interface PaginationInfo {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
}

// ---------------------------------------------------------------------------
// Platform Stats (GET /admin/stats)
// ---------------------------------------------------------------------------

export interface PlatformStats {
  platform: { version: string; uptime_seconds: number };
  resellers: { total: number; active: number; suspended: number };
  users: { total: number; active: number; suspended: number; by_role: Record<string, number> };
  sessions: { total: number; today: number; active_now: number; queued: number };
  usage_this_month: {
    total_sessions: number;
    cost_usd: number;
    input_tokens: number;
    output_tokens: number;
    avg_cost_usd: number;
    top_models: { model: string; sessions: number }[];
  };
  capacity: { global_max_concurrent: number; active: number; redis_memory_mb: number; disk_usage_gb: number };
}

// ---------------------------------------------------------------------------
// Reseller
// ---------------------------------------------------------------------------

export interface SpendingLimits {
  monthly_usd: number | null;
  daily_usd: number | null;
  per_session_usd?: number | null;
}

export interface SpendingCurrent {
  monthly_usd: number;
  daily_usd: number;
}

export interface ResellerSpending {
  limits: SpendingLimits;
  current: SpendingCurrent;
  alert_threshold_pct: number | null;
}

export interface ResellerLimits {
  max_users: number;
  current_users: number;
  max_concurrent_tasks: number;
  max_daily_tasks: number;
}

export interface ResellerStats {
  user_count: number;
  active_users_30d: number;
  total_sessions: number;
  total_cost_usd: number;
  api_keys_active: number;
  sessions_this_month: number;
  cost_this_month_usd: number;
}

export interface Reseller {
  id: string;
  name: string;
  company: string | null;
  contact_email: string;
  owner_user_id: string;
  owner_username: string | null;
  is_active: boolean;
  suspended_at: string | null;
  limits: ResellerLimits;
  llm_provider: string | null;
  features: Record<string, unknown>;
  spending: ResellerSpending;
  notes: string | null;
  stats?: ResellerStats;
  created_at: string;
  updated_at: string;
}

export interface ResellerListResponse {
  resellers: Reseller[];
  pagination: PaginationInfo;
}

// ---------------------------------------------------------------------------
// User (admin view)
// ---------------------------------------------------------------------------

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  reseller_id: string | null;
  reseller_name: string | null;
  created_at: string;
}

export interface AdminUserListResponse {
  users: AdminUser[];
  pagination: PaginationInfo;
}

// ---------------------------------------------------------------------------
// Reseller User (reseller view)
// ---------------------------------------------------------------------------

export interface ResellerUserQuota {
  max_concurrent_tasks: number;
  max_daily_tasks: number;
  tasks_today: number;
}

export interface ResellerUserUsageSummary {
  total_sessions: number;
  total_cost_usd: number;
}

export interface ResellerUser {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  created_at: string;
  updated_at?: string;
  last_session_at?: string;
  sessions_total: number;
  quota?: ResellerUserQuota;
  features?: Record<string, unknown>;
  usage_summary?: ResellerUserUsageSummary;
  metadata?: Record<string, unknown>;
  settings_mode?: SettingsMode;
  spending_limits?: SpendingLimits;
}

export interface ResellerUserListResponse {
  users: ResellerUser[];
  pagination: PaginationInfo;
}

// ---------------------------------------------------------------------------
// Settings mode
// ---------------------------------------------------------------------------

export type SettingsMode = 'readonly' | 'configurable';

// ---------------------------------------------------------------------------
// Spending
// ---------------------------------------------------------------------------

export interface SpendingStatus {
  limits: SpendingLimits;
  current: SpendingCurrent;
  alert_threshold_pct: number;
  status: string;
}

// ---------------------------------------------------------------------------
// User Config
// ---------------------------------------------------------------------------

export interface UserConfig {
  user_id: string;
  settings_mode: SettingsMode;
  allowed_overrides: string[];
  features: Record<string, unknown>;
  security: Record<string, unknown>;
  spending: SpendingStatus;
  skills: Record<string, unknown>;
  ssh_filters: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Security Config
// ---------------------------------------------------------------------------

export interface SecurityConfig {
  allowed_tools?: string[];
  disabled_tools?: string[];
  command_block_patterns?: string[];
  network_allowed_domains?: string[];
  network_blocked_domains?: string[];
  path_blocklist_additions?: string[];
}

// ---------------------------------------------------------------------------
// SSH Filters
// ---------------------------------------------------------------------------

export interface SSHFilters {
  blocked_hosts?: string[];
  allowed_hosts?: string[];
  command_block_patterns?: string[];
  max_connections?: number;
  session_timeout_seconds?: number;
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export interface SkillInfo {
  name: string;
  source: string;
  is_enabled: boolean;
  content_hash: string;
  created_at?: string;
  description?: string;
}

export interface SkillListResponse {
  skills: SkillInfo[];
  limits: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Platform Config
// ---------------------------------------------------------------------------

export interface PlatformConfig {
  default_features: Record<string, unknown>;
  default_quotas: Record<string, unknown>;
  default_spending_limits: Record<string, unknown>;
  default_settings_mode?: string;
  default_allowed_overrides?: string[];
}

// ---------------------------------------------------------------------------
// Retention
// ---------------------------------------------------------------------------

export interface RetentionConfig {
  usage_records: number;
  events: number;
  webhook_delivery_log: number;
  api_key_audit_log: number;
}

export interface RetentionRunResult {
  total_purged: number;
  tables: Record<string, { purged: number; retention_days: number }>;
}

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------

export interface AuditLogEntry {
  id: number;
  timestamp: string;
  api_key_name: string | null;
  reseller_name: string | null;
  action: string;
  target_user: string | null;
  ip_address: string;
  status_code: number;
  error: string | null;
}

export interface AuditLogResponse {
  entries: AuditLogEntry[];
  pagination: PaginationInfo;
}

// ---------------------------------------------------------------------------
// API Key
// ---------------------------------------------------------------------------

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  ip_allowlist: string[] | null;
  rate_limit_per_minute: number;
  is_active: boolean;
  last_used_at: string | null;
  last_used_ip?: string | null;
  expires_at: string | null;
  created_at: string;
}

export const VALID_API_KEY_SCOPES = [
  'users:create', 'users:read', 'users:update', 'users:suspend',
  'users:delete', 'users:password', 'sessions:read', 'usage:read',
  'keys:manage', 'config:read', 'config:update', 'skills:manage',
  'security:manage',
] as const;

// ---------------------------------------------------------------------------
// Webhook
// ---------------------------------------------------------------------------

export interface WebhookEndpoint {
  id: string;
  url: string;
  events: string[];
  is_active: boolean;
  description: string | null;
  created_at: string;
  updated_at?: string;
}

export interface WebhookDelivery {
  id: number;
  event_type: string;
  status: string;
  attempts: number;
  max_attempts?: number;
  response_status: number | null;
  error: string | null;
  last_attempt_at?: string;
  next_retry_at?: string;
  created_at: string;
}

export const WEBHOOK_EVENT_TYPES = [
  'session.started', 'session.completed', 'session.failed',
  'user.created', 'user.suspended', 'user.deleted',
  'spending.warning', 'spending.exceeded',
  '*',
] as const;

// ---------------------------------------------------------------------------
// Usage / Metrics
// ---------------------------------------------------------------------------

export interface UsageTotals {
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  active_users: number;
  ssh_commands?: number;
}

export interface UsageResponse {
  period: { start: string; end: string };
  totals: UsageTotals;
  by_user?: { user_id: string; username: string; sessions: number; cost_usd: number }[];
  by_day?: { date: string; sessions: number; cost_usd: number }[];
}

export interface UserUsageSession {
  id: string;
  status: string;
  created_at: string;
  cost_usd: number;
  num_turns: number;
}

export interface UserUsageResponse {
  user_id: string;
  username: string;
  period: { start: string; end: string };
  totals: UsageTotals;
  sessions: UserUsageSession[];
}

export interface WhmcsMetrics {
  metrics: Record<string, { type: string; display: string }>;
  usage: Record<string, Record<string, number>>;
}

// ---------------------------------------------------------------------------
// Connection Test
// ---------------------------------------------------------------------------

export interface ConnectionTestResult {
  status: string;
  version?: string;
  reseller?: string;
}

// ---------------------------------------------------------------------------
// Suspend / Delete Responses
// ---------------------------------------------------------------------------

export interface SuspendResponse {
  id: string;
  username?: string;
  name?: string;
  is_active: boolean;
  active_sessions_cancelled?: number;
  users_suspended?: number;
  sessions_cancelled?: number;
  api_keys_deactivated?: number;
}

export interface DeleteUserResponse {
  status: string;
  id: string;
  username: string;
  sessions_deleted: number;
  files_cleaned: boolean;
}

export interface DeleteResellerResponse {
  status: string;
  name: string;
  users_deleted: number;
  sessions_deleted: number;
}
