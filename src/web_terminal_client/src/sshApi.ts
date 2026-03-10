/**
 * API client functions for user SSH profile endpoints.
 */
import type {
  CreateSSHProfileRequest,
  SSHProfile,
  SSHProfileList,
  TestSSHConnectionRequest,
  TestSSHConnectionResponse,
  UpdateSSHProfileRequest,
} from './types/ssh';
import { authenticatedRequest } from './utils/apiClient';

// ---------------------------------------------------------------------------
// SSH Profiles — User
// ---------------------------------------------------------------------------

export async function listSSHProfiles(
  baseUrl: string,
  token: string,
): Promise<SSHProfileList> {
  return authenticatedRequest(baseUrl, '/api/v1/ssh-profiles', token);
}

export async function createSSHProfile(
  baseUrl: string,
  token: string,
  data: CreateSSHProfileRequest,
): Promise<SSHProfile> {
  return authenticatedRequest(baseUrl, '/api/v1/ssh-profiles', token, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function getSSHProfile(
  baseUrl: string,
  token: string,
  profileId: string,
): Promise<SSHProfile> {
  return authenticatedRequest(baseUrl, `/api/v1/ssh-profiles/${profileId}`, token);
}

export async function updateSSHProfile(
  baseUrl: string,
  token: string,
  profileId: string,
  data: UpdateSSHProfileRequest,
): Promise<SSHProfile> {
  return authenticatedRequest(baseUrl, `/api/v1/ssh-profiles/${profileId}`, token, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteSSHProfile(
  baseUrl: string,
  token: string,
  profileId: string,
): Promise<void> {
  return authenticatedRequest(baseUrl, `/api/v1/ssh-profiles/${profileId}`, token, {
    method: 'DELETE',
  });
}

export async function testSSHConnection(
  baseUrl: string,
  token: string,
  data: TestSSHConnectionRequest,
): Promise<TestSSHConnectionResponse> {
  return authenticatedRequest(baseUrl, '/api/v1/ssh-profiles/test', token, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function testSavedSSHConnection(
  baseUrl: string,
  token: string,
  profileId: string,
): Promise<TestSSHConnectionResponse> {
  return authenticatedRequest(baseUrl, `/api/v1/ssh-profiles/${profileId}/test`, token, {
    method: 'POST',
  });
}
