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
// Platform Config
// ---------------------------------------------------------------------------

export interface PlatformConfig {
  default_features: Record<string, unknown>;
  default_quotas: Record<string, unknown>;
  default_spending_limits: Record<string, unknown>;
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
  ip_allowlist: string[];
  rate_limit_rpm: number | null;
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

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
}

export interface WebhookDelivery {
  id: number;
  event_type: string;
  status: string;
  attempts: number;
  response_status: number | null;
  error: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Usage / Metrics
// ---------------------------------------------------------------------------

export interface UsageResponse {
  period: { start: string; end: string };
  totals: {
    sessions: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    active_users: number;
  };
}

export interface WhmcsMetrics {
  metrics: Record<string, { type: string; display: string }>;
  usage: Record<string, Record<string, number>>;
}
