/**
 * Tests for AdminSSHProfilesTab and the AdminUserDetail tabbed layout.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const BASE = 'http://localhost:40080';
const USER_ID = 'u-test-1';

vi.mock('../../../src/web_terminal_client/src/AuthContext', () => ({
  useAuth: () => ({
    user: { id: '1', username: 'admin', email: 'a@b.com', role: 'admin', created_at: '2024-01-01' },
    token: 'test-token',
    baseUrl: BASE,
    isAuthenticated: true,
    isAdmin: true,
    isReseller: false,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    error: null,
  }),
}));

import { AdminSSHProfilesTab } from '../../../src/web_terminal_client/src/pages/admin/AdminSSHProfilesTab';
import { AdminUserDetail } from '../../../src/web_terminal_client/src/pages/admin/AdminUserDetail';

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const mockProfile = {
  id: 'p1',
  name: 'prod-server',
  host: '10.0.0.1',
  port: 22,
  username: 'deploy',
  mode: 'sftp',
  privilege_level: 1,
  host_key_pinned: true,
  host_key_fingerprint: 'SHA256:abcdef',
  key_preview: 'ssh-rsa AAAA...',
  key_fingerprint: 'SHA256:xyzxyz',
  key_type: 'RSA',
  is_active: true,
  last_connected_at: '2024-06-01T12:00:00Z',
  last_connection_error: null,
  description: null,
  created_by: 'admin',
  created_at: '2024-01-01T00:00:00Z',
};

const mockProfileError = {
  ...mockProfile,
  id: 'p2',
  name: 'broken-server',
  host: '10.0.0.2',
  port: 2222,
  last_connected_at: null,
  last_connection_error: 'Connection refused',
};

const mockProfileNever = {
  ...mockProfile,
  id: 'p3',
  name: 'new-server',
  host: '10.0.0.3',
  port: 22,
  last_connected_at: null,
  last_connection_error: null,
};

const mockUser = {
  id: USER_ID,
  username: 'testuser',
  email: 'test@test.com',
  role: 'user',
  is_active: true,
  reseller_id: null,
  reseller_name: null,
  created_at: '2024-01-01T00:00:00Z',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderTab(userId = USER_ID) {
  return render(
    <MemoryRouter>
      <AdminSSHProfilesTab userId={userId} />
    </MemoryRouter>,
  );
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={[`/admin/users/${USER_ID}`]}>
      <Routes>
        <Route path="/admin/users/:userId" element={<AdminUserDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  server.use(
    http.get(`${BASE}/api/v1/admin/users/${USER_ID}/ssh-profiles`, () =>
      HttpResponse.json({ profiles: [mockProfile, mockProfileError, mockProfileNever], count: 3 }),
    ),
    http.get(`${BASE}/api/v1/admin/users`, () =>
      HttpResponse.json({
        users: [mockUser],
        pagination: { page: 1, per_page: 50, total: 1, total_pages: 1 },
      }),
    ),
  );
});

// ---------------------------------------------------------------------------
// AdminSSHProfilesTab — rendering
// ---------------------------------------------------------------------------

describe('AdminSSHProfilesTab', () => {
  it('shows loading state initially', () => {
    renderTab();
    expect(screen.getByText('Loading SSH profiles...')).toBeInTheDocument();
  });

  it('renders profile rows after fetch', async () => {
    renderTab();
    await waitFor(() => {
      expect(screen.getByText('prod-server')).toBeInTheDocument();
    });
    expect(screen.getByText('broken-server')).toBeInTheDocument();
    expect(screen.getByText('new-server')).toBeInTheDocument();
  });

  it('displays host:port for prod-server', async () => {
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    // prod-server has unique host 10.0.0.1:22
    expect(screen.getByText('10.0.0.1:22')).toBeInTheDocument();
  });

  it('displays host:port for broken-server with non-standard port', async () => {
    renderTab();
    await waitFor(() => screen.getByText('broken-server'));
    // broken-server has unique host:port 10.0.0.2:2222
    expect(screen.getByText('10.0.0.2:2222')).toBeInTheDocument();
  });

  it('shows Connected status (green) when last_connected_at is set and no error', async () => {
    renderTab();
    await waitFor(() => screen.getAllByText('Connected'));
    expect(screen.getAllByText('Connected').length).toBeGreaterThan(0);
  });

  it('shows Error status when last_connection_error is set', async () => {
    renderTab();
    await waitFor(() => screen.getByText('Error'));
    expect(screen.getByText('Error')).toBeInTheDocument();
  });

  it('shows Never tested status when no connection history', async () => {
    renderTab();
    await waitFor(() => screen.getByText('Never tested'));
    expect(screen.getByText('Never tested')).toBeInTheDocument();
  });

  it('shows key type', async () => {
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    // All profiles have key_type: 'RSA' — there should be multiple
    expect(screen.getAllByText('RSA').length).toBeGreaterThan(0);
  });

  it('shows key fingerprint in code element', async () => {
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    const fp = screen.getAllByText('SHA256:xyzxyz');
    expect(fp.length).toBeGreaterThan(0);
    expect(fp[0].tagName.toLowerCase()).toBe('code');
  });

  it('renders Delete button for each profile', async () => {
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' });
    expect(deleteButtons).toHaveLength(3);
  });

  it('shows empty state when no profiles', async () => {
    server.use(
      http.get(`${BASE}/api/v1/admin/users/${USER_ID}/ssh-profiles`, () =>
        HttpResponse.json({ profiles: [], count: 0 }),
      ),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByText('No SSH profiles for this user.')).toBeInTheDocument();
    });
  });

  it('shows error message when API fails', async () => {
    server.use(
      http.get(`${BASE}/api/v1/admin/users/${USER_ID}/ssh-profiles`, () =>
        HttpResponse.json({ detail: 'Forbidden' }, { status: 403 }),
      ),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByText('Forbidden')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// AdminSSHProfilesTab — delete flow
// ---------------------------------------------------------------------------

describe('AdminSSHProfilesTab — delete', () => {
  it('opens confirm dialog when Delete is clicked', async () => {
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' });
    fireEvent.click(deleteButtons[0]);
    await waitFor(() => {
      expect(screen.getByText('Delete SSH Profile')).toBeInTheDocument();
    });
    // Dialog message contains the profile name
    expect(screen.getByText(/Delete profile "prod-server"/)).toBeInTheDocument();
  });

  it('closes dialog on Cancel', async () => {
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    await waitFor(() => screen.getByText('Delete SSH Profile'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => {
      expect(screen.queryByText('Delete SSH Profile')).not.toBeInTheDocument();
    });
  });

  it('calls delete API on confirm', async () => {
    let deleteCalled = false;
    server.use(
      http.delete(`${BASE}/api/v1/admin/users/${USER_ID}/ssh-profiles/:profileId`, () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    // Click delete for prod-server (first row)
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    await waitFor(() => screen.getByText('Delete SSH Profile'));
    // Confirm button is the last button in the dialog
    const allDeleteBtns = screen.getAllByRole('button', { name: 'Delete' });
    // The dialog confirm button is the last Delete button
    fireEvent.click(allDeleteBtns[allDeleteBtns.length - 1]);
    await waitFor(() => {
      expect(deleteCalled).toBe(true);
    });
  });

  it('shows error message when delete fails', async () => {
    server.use(
      http.delete(`${BASE}/api/v1/admin/users/${USER_ID}/ssh-profiles/:profileId`, () =>
        HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
      ),
    );
    renderTab();
    await waitFor(() => screen.getByText('prod-server'));
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    await waitFor(() => screen.getByText('Delete SSH Profile'));
    const allDeleteBtns = screen.getAllByRole('button', { name: 'Delete' });
    fireEvent.click(allDeleteBtns[allDeleteBtns.length - 1]);
    await waitFor(() => {
      expect(screen.getByText('Not found')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// AdminUserDetail — tabbed layout
// ---------------------------------------------------------------------------

describe('AdminUserDetail — tabs', () => {
  it('defaults to Details tab showing user info', async () => {
    renderDetail();
    await waitFor(() => screen.getAllByText('testuser'));
    expect(screen.getByText('Details')).toBeInTheDocument();
    expect(screen.getByText('SSH Profiles')).toBeInTheDocument();
    // Email appears in header subtitle and in the Details ReadonlyField
    expect(screen.getAllByText('test@test.com').length).toBeGreaterThan(0);
  });

  it('switches to SSH Profiles tab and loads profiles', async () => {
    renderDetail();
    await waitFor(() => screen.getAllByText('testuser'));
    fireEvent.click(screen.getByText('SSH Profiles'));
    await waitFor(() => {
      expect(screen.getByText('prod-server')).toBeInTheDocument();
    });
  });

  it('switches back to Details tab after viewing SSH Profiles', async () => {
    renderDetail();
    await waitFor(() => screen.getAllByText('testuser'));
    fireEvent.click(screen.getByText('SSH Profiles'));
    await waitFor(() => screen.getByText('prod-server'));
    fireEvent.click(screen.getByText('Details'));
    await waitFor(() => {
      // Email appears in header and Details tab ReadonlyField
      expect(screen.getAllByText('test@test.com').length).toBeGreaterThan(0);
    });
    // SSH tab content is no longer shown
    expect(screen.queryByText('prod-server')).not.toBeInTheDocument();
  });
});
