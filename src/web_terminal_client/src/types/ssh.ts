/** TypeScript interfaces for SSH Profile API responses. */

/** Valid SSH access modes — matches backend VALID_SSH_MODES in ssh_profile_models.py */
export type SSHMode = 'readonly' | 'operations' | 'filtered_shell';

/** SSH privilege level definition — single source of truth for form and list. */
export interface SSHAccessLevel {
  value: number;
  label: string;
  shortLabel: string;
  mode: SSHMode;
  recommended?: boolean;
}

/**
 * All privilege profiles. P0–P1 use distinct modes; P2/P3 share 'filtered_shell'
 * and are differentiated by privilege_level number in the backend filter.
 */
export const SSH_ACCESS_LEVELS: SSHAccessLevel[] = [
  { value: 0, label: 'Observer', shortLabel: 'Observer', mode: 'readonly' },
  { value: 1, label: 'Site Manager', shortLabel: 'Site Mgr', mode: 'operations', recommended: true },
  { value: 2, label: 'Server Admin', shortLabel: 'Srv Admin', mode: 'filtered_shell' },
  { value: 3, label: 'Full Access', shortLabel: 'Full', mode: 'filtered_shell' },
];

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
