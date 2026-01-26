export type SSEEventType =
  | 'agent_start'
  | 'user_message'
  | 'tool_start'
  | 'tool_complete'
  | 'thinking'
  | 'message'
  | 'error'
  | 'agent_complete'
  | 'metrics_update'
  | 'profile_switch'
  | 'hook_triggered'
  | 'conversation_turn'
  | 'session_connect'
  | 'session_disconnect'
  | 'cancelled'
  | 'subagent_start'
  | 'subagent_message'
  | 'subagent_stop'
  | 'heartbeat'
  | 'infrastructure_error'
  | 'security_alert'
  | 'queue_started'
  | 'queue_position_update';

export interface SSEEvent {
  type: SSEEventType;
  data: Record<string, unknown>;
  timestamp: string;
  sequence: number;
}

export interface TerminalEvent extends SSEEvent {
  meta?: {
    turn?: number;
  };
}

export interface SessionResponse {
  id: string;
  status: string;
  task?: string | null;
  model?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  num_turns: number;
  duration_ms?: number | null;
  total_cost_usd?: number | null;
  cancel_requested: boolean;
  resumable?: boolean;
  // Queue management fields
  queue_position?: number | null;
  queued_at?: string | null;
  is_auto_resume?: boolean;
}

export interface SessionListResponse {
  sessions: SessionResponse[];
  total: number;
}

export interface User {
  id: string;
  username: string;
  email: string;
  role: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  expires_in: number;
}

export interface TaskStartedResponse {
  session_id: string;
  status: string;
  message: string;
  resumed_from?: string | null;
  queue_position?: number | null;
}

export interface QueuedSessionInfo {
  session_id: string;
  queue_position?: number | null;
  queued_at?: string | null;
  is_auto_resume: boolean;
}

export interface QueueStatusResponse {
  global_queue_length: number;
  global_active_tasks: number;
  user_active_tasks: number;
  user_queued_tasks: QueuedSessionInfo[];
  max_concurrent_global: number;
  max_concurrent_user: number;
}

export interface ResultMetrics {
  duration_ms?: number | null;
  num_turns: number;
  total_cost_usd?: number | null;
  model?: string | null;
  usage?: {
    input_tokens: number;
    output_tokens: number;
    cache_creation_input_tokens: number;
    cache_read_input_tokens: number;
  };
}

export interface ResultResponse {
  session_id: string;
  status: string;
  error: string;
  comments: string;
  output: string;
  result_files: string[];
  metrics?: ResultMetrics | null;
}

export interface AppConfig {
  api: {
    base_url: string;
  };
  ui: {
    max_output_lines: number;
    auto_scroll: boolean;
  };
}

export interface ConfigResponse {
  models_available: string[];
  default_model: string;
}

// =============================================================================
// File Explorer Types
// =============================================================================

export interface FileInfo {
  name: string;
  path: string;
  is_directory: boolean;
  size: number;
  created_at: string;
  modified_at: string;
  mime_type: string | null;
  is_hidden: boolean;
  is_viewable: boolean;
  is_readonly: boolean;  // True if file/folder is in read-only area
  is_external: boolean;  // True if file is in external mount
  mount_type: 'ro' | 'rw' | 'persistent' | 'user-ro' | 'user-rw' | null;  // Type of external mount
  children?: FileInfo[] | null;
}

export interface DirectoryListing {
  path: string;
  files: FileInfo[];
  total_count: number;
  truncated: boolean;
}

export interface FileContentResponse {
  path: string;
  name: string;
  mime_type: string;
  size: number;
  content: string | null;
  is_binary: boolean;
  is_truncated: boolean;
  error: string | null;
}

export type FileSortField = 'name' | 'size' | 'created_at' | 'modified_at';
export type SortOrder = 'asc' | 'desc';

export interface UploadedFileInfo {
  name: string;
  path: string;
  size: number;
  mime_type: string;
}

export interface UploadResponse {
  uploaded: UploadedFileInfo[];
  total_count: number;
  errors: string[];
}

// =============================================================================
// Skills Types
// =============================================================================

export interface SkillInfo {
  id: string;
  name: string;
  description: string;
}

export interface SkillsListResponse {
  skills: SkillInfo[];
}

// =============================================================================
// Security Alert Types
// =============================================================================

export interface SecurityAlertFile {
  path: string;
  secrets_count: number;
  redacted: boolean;
}

export interface SecurityAlertData {
  session_id: string;
  files_scanned: number;
  files_with_secrets: number;
  total_secrets: number;
  secret_types: string[];
  type_labels: string[];
  message: string;
  files: SecurityAlertFile[];
}

// =============================================================================
// User Events Types (for cross-session SSE)
// =============================================================================

export interface UserSessionInfo {
  id: string;
  status: string;
  queue_position?: number | null;
  is_auto_resume?: boolean;
}

export interface SessionListUpdateEvent {
  type: 'session_list_update';
  data: {
    sessions: UserSessionInfo[];
  };
  timestamp: string;
}

export interface UserHeartbeatEvent {
  type: 'heartbeat';
  timestamp: string;
}

export type UserEvent = SessionListUpdateEvent | UserHeartbeatEvent | { type: string; data?: unknown; timestamp: string };
