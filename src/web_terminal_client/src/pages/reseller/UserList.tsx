import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import {
  listResellerUsers,
  createResellerUser,
  suspendResellerUser,
  unsuspendResellerUser,
} from '../../adminApi';
import { DataTable, StatusBadge, FormField } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { ResellerUser, ResellerUserListResponse } from '../../types/admin';

interface CreateForm {
  username: string;
  email: string;
  password: string;
}

const EMPTY_FORM: CreateForm = { username: '', email: '', password: '' };

function validateCreateForm(form: CreateForm): Partial<CreateForm> {
  const errors: Partial<CreateForm> = {};
  if (!form.username) {
    errors.username = 'Required';
  } else if (form.username.length < 3 || form.username.length > 32) {
    errors.username = 'Must be 3-32 characters';
  } else if (!/^[a-z][a-z0-9_]*$/.test(form.username)) {
    errors.username = 'Lowercase letters, digits, underscore; must start with a letter';
  }
  if (!form.email) {
    errors.email = 'Required';
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
    errors.email = 'Invalid email address';
  }
  if (!form.password) {
    errors.password = 'Required';
  } else if (form.password.length < 8) {
    errors.password = 'Minimum 8 characters';
  }
  return errors;
}

export function UserList() {
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<ResellerUserListResponse | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [formErrors, setFormErrors] = useState<Partial<CreateForm>>({});
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const fetchData = useCallback(() => {
    if (!token || !baseUrl) return;
    listResellerUsers(baseUrl, token, {
      page,
      status: statusFilter,
      search: search || undefined,
    })
      .then((d) => setData(d as ResellerUserListResponse))
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl, page, statusFilter, search]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleCreate = async () => {
    const errors = validateCreateForm(form);
    if (Object.keys(errors).length > 0) {
      setFormErrors(errors);
      return;
    }
    if (!token || !baseUrl) return;
    setCreating(true);
    setCreateError(null);
    try {
      await createResellerUser(baseUrl, token, {
        username: form.username,
        email: form.email,
        password: form.password,
      });
      setShowCreate(false);
      setForm(EMPTY_FORM);
      setFormErrors({});
      fetchData();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const handleSuspendToggle = async (e: React.MouseEvent, user: ResellerUser) => {
    e.stopPropagation();
    if (!token || !baseUrl) return;
    try {
      if (user.is_active) {
        await suspendResellerUser(baseUrl, token, user.id);
      } else {
        await unsuspendResellerUser(baseUrl, token, user.id);
      }
      fetchData();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const columns: Column<Record<string, unknown>>[] = [
    { key: 'username', header: 'Username', sortable: true },
    { key: 'email', header: 'Email' },
    {
      key: 'is_active', header: 'Status', sortable: true,
      render: (r) => <StatusBadge status={r.is_active ? 'active' : 'suspended'} />,
    },
    {
      key: 'created_at', header: 'Created', sortable: true,
      render: (r) => new Date(r.created_at as string).toLocaleDateString(),
    },
    {
      key: 'actions', header: '',
      render: (r) => (
        <button
          className={`dash-btn dash-btn-sm ${r.is_active ? 'dash-btn-secondary' : 'dash-btn-primary'}`}
          onClick={(e) => handleSuspendToggle(e, r as unknown as ResellerUser)}
        >
          {r.is_active ? 'Suspend' : 'Unsuspend'}
        </button>
      ),
    },
  ];

  if (error) return <div className="dash-error">{error}</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Users</h2>
        <button
          className="dash-btn dash-btn-primary"
          onClick={() => { setShowCreate(!showCreate); setForm(EMPTY_FORM); setFormErrors({}); setCreateError(null); }}
        >
          {showCreate ? 'Cancel' : 'Create User'}
        </button>
      </div>

      {showCreate && (
        <div className="dash-section" style={{ marginBottom: '1rem' }}>
          <h3 className="dash-section-title">New User</h3>
          {createError && <div className="dash-error">{createError}</div>}
          <FormField label="Username" required error={formErrors.username}>
            <input
              className="dash-form-input"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              placeholder="e.g. john_doe"
            />
          </FormField>
          <FormField label="Email" required error={formErrors.email}>
            <input
              className="dash-form-input"
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="user@example.com"
            />
          </FormField>
          <FormField label="Password" required error={formErrors.password}>
            <input
              className="dash-form-input"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="Min. 8 characters"
            />
          </FormField>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className="dash-btn dash-btn-primary"
              onClick={handleCreate}
              disabled={creating}
            >
              {creating ? 'Creating...' : 'Create'}
            </button>
            <button
              className="dash-btn dash-btn-secondary"
              onClick={() => { setShowCreate(false); setForm(EMPTY_FORM); setFormErrors({}); }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="dash-toolbar">
        <input
          className="dash-search"
          placeholder="Search users..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
        <select
          className="dash-select"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
        >
          <option value="all">All</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
        </select>
      </div>

      {data && (
        <DataTable
          columns={columns}
          data={data.users as unknown as Record<string, unknown>[]}
          keyField="id"
          onRowClick={(r) => navigate(`/reseller/users/${r.id}`)}
          pagination={{
            page: data.pagination.page,
            totalPages: data.pagination.total_pages,
            onPageChange: setPage,
          }}
        />
      )}
    </div>
  );
}
