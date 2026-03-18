/** TypeScript interfaces for SSH Profile API responses. */

/** Valid SSH access modes — matches backend VALID_SSH_MODES in ssh_profile_models.py */
export type SSHMode = 'readonly' | 'operations' | 'filtered_shell';

export interface SSHProfile {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  mode: SSHMode;
  privilege_level: number;
  host_key_pinned: boolean;
  host_key_fingerprint: string | null;
  key_preview: string;
  key_fingerprint: string | null;
  key_type: string | null;
  is_active: boolean;
  last_connected_at: string | null;
  last_connection_error: string | null;
  description: string | null;
  created_by: string;
  created_at: string;
}

export interface SSHProfileList {
  profiles: SSHProfile[];
  count: number;
}

export interface CreateSSHProfileRequest {
  name: string;
  host: string;
  port?: number;
  username: string;
  private_key: string;
  passphrase?: string;
  mode?: SSHMode;
  privilege_level?: number;
  allowed_operations?: string[];
  description?: string;
}

export interface UpdateSSHProfileRequest {
  name?: string;
  host?: string;
  port?: number;
  username?: string;
  private_key?: string;
  passphrase?: string;
  mode?: SSHMode;
  privilege_level?: number;
  allowed_operations?: string[];
  description?: string;
  is_active?: boolean;
}

export interface TestSSHConnectionRequest {
  host: string;
  port?: number;
  username: string;
  private_key: string;
  passphrase?: string;
}

export interface TestSSHConnectionResponse {
  status: 'success' | 'failed';
  error_code?: string;
  message: string;
  host_key_fingerprint?: string;
  host_key_type?: string;
  server_banner?: string;
  latency_ms?: number;
}
