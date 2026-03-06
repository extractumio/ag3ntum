/**
 * Tests for admin and reseller dashboard pages.
 * Uses MSW to mock API responses and vi.mock for auth.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const BASE = 'http://localhost:40080';

// Mock useAuth
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

// Import pages after mock
import { AdminDashboard } from '../../../src/web_terminal_client/src/pages/admin/AdminDashboard';
import { ResellerList } from '../../../src/web_terminal_client/src/pages/admin/ResellerList';
import { AdminUserList } from '../../../src/web_terminal_client/src/pages/admin/AdminUserList';
import { AdminUsage } from '../../../src/web_terminal_client/src/pages/admin/AdminUsage';
import { AuditLog } from '../../../src/web_terminal_client/src/pages/admin/AuditLog';
import { PlatformConfig } from '../../../src/web_terminal_client/src/pages/admin/PlatformConfig';

function renderInRouter(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="*" element={ui} />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Mock API data
// ---------------------------------------------------------------------------

const mockPlatformStats = {
  platform: { version: '1.0.0', uptime_seconds: 3600 },
  resellers: { total: 5, active: 4, suspended: 1 },
  users: { total: 50, active: 45, suspended: 5, by_role: { admin: 2, reseller: 5, user: 43 } },
  sessions: { total: 200, today: 10, active_now: 3, queued: 1 },
  usage_this_month: {
    total_sessions: 150, cost_usd: 42.50, input_tokens: 100000, output_tokens: 50000,
    avg_cost_usd: 0.28, top_models: [],
  },
  capacity: { global_max_concurrent: 20, active: 3, redis_memory_mb: 64, disk_usage_gb: 10 },
};

const mockResellerList = {
  resellers: [
    {
      id: 'r1', name: 'Acme Corp', company: 'Acme', contact_email: 'a@acme.com',
      owner_user_id: 'u1', owner_username: 'ag3_res_acme', is_active: true, suspended_at: null,
      limits: { max_users: 50, current_users: 10, max_concurrent_tasks: 10, max_daily_tasks: 500 },
      llm_provider: null, features: {},
      spending: { limits: { monthly_usd: 100, daily_usd: null }, current: { monthly_usd: 25, daily_usd: 5 }, alert_threshold_pct: 80 },
      notes: null, created_at: '2024-01-01', updated_at: '2024-01-01',
    },
  ],
  pagination: { page: 1, per_page: 50, total: 1, total_pages: 1 },
};

const mockUserList = {
  users: [
    { id: 'u1', username: 'testuser', email: 'test@test.com', role: 'user', is_active: true, reseller_id: 'r1', reseller_name: 'Acme', created_at: '2024-01-01' },
    { id: 'u2', username: 'admin1', email: 'admin@test.com', role: 'admin', is_active: true, reseller_id: null, reseller_name: null, created_at: '2024-01-01' },
  ],
  pagination: { page: 1, per_page: 50, total: 2, total_pages: 1 },
};

const mockUsage = {
  period: { start: '2024-01-01', end: '2024-01-31' },
  totals: { sessions: 100, input_tokens: 50000, output_tokens: 25000, cost_usd: 15.5, active_users: 8 },
  by_user: [{ user_id: 'u1', username: 'testuser', sessions: 50, cost_usd: 8.5 }],
  by_day: [{ date: '2024-01-15', sessions: 10, cost_usd: 2.0 }],
};

const mockAuditLog = {
  entries: [
    { id: 1, timestamp: '2024-01-01T12:00:00Z', api_key_name: 'key1', reseller_name: 'Acme', action: 'user.create', target_user: 'testuser', ip_address: '192.168.1.1', status_code: 201, error: null },
  ],
  pagination: { page: 1, per_page: 50, total: 1, total_pages: 1 },
};

const mockPlatformConfig = {
  default_features: { ssh_enabled: true },
  default_quotas: { max_concurrent_tasks: 4 },
  default_spending_limits: { monthly_usd: 500 },
};

const mockRetention = { usage_records: 90, events: 30, webhook_delivery_log: 30, api_key_audit_log: 90 };

// ---------------------------------------------------------------------------
// Setup handlers
// ---------------------------------------------------------------------------

beforeEach(() => {
  server.use(
    http.get(`${BASE}/api/v1/admin/stats`, () => HttpResponse.json(mockPlatformStats)),
    http.get(`${BASE}/api/v1/admin/resellers`, () => HttpResponse.json(mockResellerList)),
    http.get(`${BASE}/api/v1/admin/users`, () => HttpResponse.json(mockUserList)),
    http.get(`${BASE}/api/v1/admin/usage`, () => HttpResponse.json(mockUsage)),
    http.get(`${BASE}/api/v1/admin/audit`, () => HttpResponse.json(mockAuditLog)),
    http.get(`${BASE}/api/v1/admin/config`, () => HttpResponse.json(mockPlatformConfig)),
    http.get(`${BASE}/api/v1/admin/retention`, () => HttpResponse.json(mockRetention)),
    http.post(`${BASE}/api/v1/admin/resellers`, () => HttpResponse.json(mockResellerList.resellers[0], { status: 201 })),
    http.post(`${BASE}/api/v1/admin/users`, () => HttpResponse.json({ id: 'new', username: 'newuser' }, { status: 201 })),
  );
});

// ---------------------------------------------------------------------------
// AdminDashboard
// ---------------------------------------------------------------------------

describe('AdminDashboard', () => {
  it('renders platform stats', async () => {
    renderInRouter(<AdminDashboard />);
    await waitFor(() => {
      expect(screen.getByText('1.0.0')).toBeInTheDocument();
    });
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('45')).toBeInTheDocument();
  });

  it('shows loading initially', () => {
    renderInRouter(<AdminDashboard />);
    expect(screen.getByText('Loading stats...')).toBeInTheDocument();
  });

  it('shows error on API failure', async () => {
    server.use(
      http.get(`${BASE}/api/v1/admin/stats`, () => HttpResponse.json({ detail: 'Forbidden' }, { status: 403 })),
    );
    renderInRouter(<AdminDashboard />);
    await waitFor(() => {
      expect(screen.getByText('Forbidden')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// ResellerList
// ---------------------------------------------------------------------------

describe('ResellerList', () => {
  it('renders reseller table', async () => {
    renderInRouter(<ResellerList />);
    await waitFor(() => {
      expect(screen.getByText('Acme Corp')).toBeInTheDocument();
    });
  });

  it('shows Create Reseller button', async () => {
    renderInRouter(<ResellerList />);
    await waitFor(() => {
      expect(screen.getByText('+ Create Reseller')).toBeInTheDocument();
    });
  });

  it('has search and filter controls', async () => {
    renderInRouter(<ResellerList />);
    await waitFor(() => screen.getByText('Acme Corp'));
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AdminUserList
// ---------------------------------------------------------------------------

describe('AdminUserList', () => {
  it('renders user table', async () => {
    renderInRouter(<AdminUserList />);
    await waitFor(() => {
      expect(screen.getByText('testuser')).toBeInTheDocument();
      expect(screen.getByText('admin1')).toBeInTheDocument();
    });
  });

  it('shows Create User button', async () => {
    renderInRouter(<AdminUserList />);
    await waitFor(() => {
      expect(screen.getByText('+ Create User')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// AdminUsage
// ---------------------------------------------------------------------------

describe('AdminUsage', () => {
  it('renders usage stats', async () => {
    renderInRouter(<AdminUsage />);
    await waitFor(() => {
      expect(screen.getByText('100')).toBeInTheDocument();
    });
  });

  it('has period selector', () => {
    renderInRouter(<AdminUsage />);
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('shows by-user breakdown', async () => {
    renderInRouter(<AdminUsage />);
    await waitFor(() => {
      expect(screen.getByText('testuser')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// AuditLog
// ---------------------------------------------------------------------------

describe('AuditLog', () => {
  it('renders audit entries', async () => {
    renderInRouter(<AuditLog />);
    await waitFor(() => {
      expect(screen.getByText('user.create')).toBeInTheDocument();
    });
  });

  it('shows IP address', async () => {
    renderInRouter(<AuditLog />);
    await waitFor(() => {
      expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// PlatformConfig
// ---------------------------------------------------------------------------

describe('PlatformConfig', () => {
  it('renders config and retention sections', async () => {
    renderInRouter(<PlatformConfig />);
    await waitFor(() => {
      expect(screen.getByText(/configuration/i)).toBeInTheDocument();
    });
  });
});
