/**
 * Tests for sshApi — unit tests with MSW handlers.
 */
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import {
  createSSHProfile,
  deleteSSHProfile,
  getSSHProfile,
  listSSHProfiles,
  testSSHConnection,
  testSavedSSHConnection,
  updateSSHProfile,
} from '../../../src/web_terminal_client/src/sshApi';
import { server } from '../mocks/server';

const BASE = 'http://localhost:40080';
const TOKEN = 'test-token';

// ---------------------------------------------------------------------------
// Shared mock profile
// ---------------------------------------------------------------------------

const mockProfile = {
  id: 'prof-abc',
  name: 'prod-server',
  host: '10.0.0.1',
  port: 22,
  username: 'root',
  mode: 'manage',
  privilege_level: 1,
  host_key_pinned: false,
  host_key_fingerprint: null,
  key_preview: '-----BEGIN RSA...**...KEY-----',
  key_fingerprint: 'SHA256:abc123',
  key_type: 'RSA',
  is_active: true,
  last_connected_at: null,
  last_connection_error: null,
  description: null,
  created_by: 'user-1',
  created_at: '2026-01-01T00:00:00Z',
};

// ---------------------------------------------------------------------------
// listSSHProfiles
// ---------------------------------------------------------------------------

describe('listSSHProfiles', () => {
  it('returns a list of profiles', async () => {
    server.use(
      http.get(`${BASE}/api/v1/ssh-profiles`, () =>
        HttpResponse.json({ profiles: [mockProfile], count: 1 }),
      ),
    );
    const res = await listSSHProfiles(BASE, TOKEN);
    expect(res.count).toBe(1);
    expect(res.profiles[0].name).toBe('prod-server');
  });

  it('throws on 401', async () => {
    server.use(
      http.get(`${BASE}/api/v1/ssh-profiles`, () =>
        HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
      ),
    );
    await expect(listSSHProfiles(BASE, TOKEN)).rejects.toThrow('Unauthorized');
  });
});

// ---------------------------------------------------------------------------
// createSSHProfile
// ---------------------------------------------------------------------------

describe('createSSHProfile', () => {
  it('creates and returns a new profile', async () => {
    server.use(
      http.post(`${BASE}/api/v1/ssh-profiles`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ ...mockProfile, name: String(body.name) });
      }),
    );
    const created = await createSSHProfile(BASE, TOKEN, {
      name: 'prod-server',
      host: '10.0.0.1',
      username: 'root',
      private_key: '-----BEGIN RSA PRIVATE KEY-----\n...',
    });
    expect(created.name).toBe('prod-server');
  });

  it('throws with server message on error', async () => {
    server.use(
      http.post(`${BASE}/api/v1/ssh-profiles`, () =>
        HttpResponse.json({ detail: 'Name already exists' }, { status: 409 }),
      ),
    );
    await expect(
      createSSHProfile(BASE, TOKEN, {
        name: 'dup',
        host: '10.0.0.1',
        username: 'root',
        private_key: 'key',
      }),
    ).rejects.toThrow('Name already exists');
  });
});

// ---------------------------------------------------------------------------
// getSSHProfile
// ---------------------------------------------------------------------------

describe('getSSHProfile', () => {
  it('returns the profile by ID', async () => {
    server.use(
      http.get(`${BASE}/api/v1/ssh-profiles/prof-abc`, () =>
        HttpResponse.json(mockProfile),
      ),
    );
    const profile = await getSSHProfile(BASE, TOKEN, 'prof-abc');
    expect(profile.id).toBe('prof-abc');
  });

  it('throws on 404', async () => {
    server.use(
      http.get(`${BASE}/api/v1/ssh-profiles/missing`, () =>
        HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
      ),
    );
    await expect(getSSHProfile(BASE, TOKEN, 'missing')).rejects.toThrow('Not found');
  });
});

// ---------------------------------------------------------------------------
// updateSSHProfile
// ---------------------------------------------------------------------------

describe('updateSSHProfile', () => {
  it('sends update and returns updated profile', async () => {
    server.use(
      http.put(`${BASE}/api/v1/ssh-profiles/prof-abc`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>;
        return HttpResponse.json({ ...mockProfile, host: String(body.host ?? mockProfile.host) });
      }),
    );
    const updated = await updateSSHProfile(BASE, TOKEN, 'prof-abc', { host: '10.0.0.2' });
    expect(updated.host).toBe('10.0.0.2');
  });
});

// ---------------------------------------------------------------------------
// deleteSSHProfile
// ---------------------------------------------------------------------------

describe('deleteSSHProfile', () => {
  it('deletes profile and returns void on 204', async () => {
    server.use(
      http.delete(`${BASE}/api/v1/ssh-profiles/prof-abc`, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    const result = await deleteSSHProfile(BASE, TOKEN, 'prof-abc');
    expect(result).toBeUndefined();
  });

  it('throws on 404', async () => {
    server.use(
      http.delete(`${BASE}/api/v1/ssh-profiles/missing`, () =>
        HttpResponse.json({ detail: 'Profile not found' }, { status: 404 }),
      ),
    );
    await expect(deleteSSHProfile(BASE, TOKEN, 'missing')).rejects.toThrow('Profile not found');
  });
});

// ---------------------------------------------------------------------------
// testSSHConnection
// ---------------------------------------------------------------------------

describe('testSSHConnection', () => {
  it('returns success result', async () => {
    server.use(
      http.post(`${BASE}/api/v1/ssh-profiles/test`, () =>
        HttpResponse.json({
          status: 'success',
          message: 'Connected',
          latency_ms: 35,
          host_key_fingerprint: 'SHA256:xyz',
        }),
      ),
    );
    const result = await testSSHConnection(BASE, TOKEN, {
      host: '10.0.0.1',
      username: 'root',
      private_key: 'key',
    });
    expect(result.status).toBe('success');
    expect(result.latency_ms).toBe(35);
  });

  it('returns failed result', async () => {
    server.use(
      http.post(`${BASE}/api/v1/ssh-profiles/test`, () =>
        HttpResponse.json({
          status: 'failed',
          message: 'Connection refused',
          error_code: 'CONN_REFUSED',
        }),
      ),
    );
    const result = await testSSHConnection(BASE, TOKEN, {
      host: '10.0.0.1',
      username: 'root',
      private_key: 'key',
    });
    expect(result.status).toBe('failed');
    expect(result.error_code).toBe('CONN_REFUSED');
  });
});

// ---------------------------------------------------------------------------
// testSavedSSHConnection
// ---------------------------------------------------------------------------

describe('testSavedSSHConnection', () => {
  it('tests saved profile', async () => {
    server.use(
      http.post(`${BASE}/api/v1/ssh-profiles/prof-abc/test`, () =>
        HttpResponse.json({ status: 'success', message: 'OK', latency_ms: 10 }),
      ),
    );
    const result = await testSavedSSHConnection(BASE, TOKEN, 'prof-abc');
    expect(result.status).toBe('success');
    expect(result.latency_ms).toBe(10);
  });
});
