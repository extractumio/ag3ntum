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
  | 'infrastructure_error';

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
