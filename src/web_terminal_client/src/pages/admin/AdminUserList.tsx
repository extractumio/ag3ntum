import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import { listAllUsers, createAdminUser } from '../../adminApi';
import { DataTable, StatusBadge, FormField } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { AdminUser, AdminUserListResponse } from '../../types/admin';

interface CreateForm {
  username: string;
  email: string;
  password: string;
  role: string;
}

const EMPTY_FORM: CreateForm = { username: '', email: '', password: '', role: 'user' };

function validateForm(form: CreateForm): Partial<Record<keyof CreateForm, string>> {
  const errors: Partial<Record<keyof CreateForm, string>> = {};
  if (!form.username.trim()) errors.username = 'Username is required';
  else if (form.username.length < 3 || form.username.length > 32) errors.username = 'Must be 3–32 characters';
  else if (!/^[a-z0-9_-]+$/.test(form.username)) errors.username = 'Lowercase letters, numbers, _ or - only';
  if (!form.email.trim()) errors.email = 'Email is required';
  else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = 'Invalid email';
  if (!form.password) errors.password = 'Password is required';
  else if (form.password.length < 8) errors.password = 'Min 8 characters';
  return errors;
}

export function AdminUserList() {
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<AdminUserListResponse | null>(null);
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof CreateForm, string>>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const fetchList = useCallback(() => {
    if (!token || !baseUrl) return;
    const params: Record<string, string | number | undefined> = { page };
    if (roleFilter !== 'all') params.role = roleFilter;
    if (statusFilter !== 'all') params.status = statusFilter;
    if (search) params.search = search;
    listAllUsers(baseUrl, token, params)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, baseUrl, page, roleFilter, statusFilter, search]);

  useEffect(() => { fetchList(); }, [fetchList]);

  const setField = (key: keyof CreateForm, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    if (formErrors[key]) setFormErrors((prev) => ({ ...prev, [key]: undefined }));
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const errors = validateForm(form);
    if (Object.keys(errors).length > 0) { setFormErrors(errors); return; }
    setSubmitting(true);
    setSubmitError(null);
    try {
      await createAdminUser(baseUrl!, token!, {
        username: form.username.trim(),
        email: form.email.trim(),
        password: form.password,
        role: form.role,
      });
      setForm(EMPTY_FORM);
      setShowCreate(false);
      fetchList();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const columns: Column<Record<string, unknown>>[] = [
    { key: 'username', header: 'Username', sortable: true },
    { key: 'email', header: 'Email', sortable: true },
    {
      key: 'role', header: 'Role', sortable: true,
      render: (r) => <span className={`dash-badge dash-badge-${r.role === 'admin' ? 'blue' : r.role === 'reseller' ? 'yellow' : 'default'}`}>{String(r.role)}</span>,
    },
    {
      key: 'reseller_name', header: 'Reseller',
      render: (r) => r.reseller_name ? String(r.reseller_name) : '—',
    },
    {
      key: 'is_active', header: 'Status', sortable: true,
      render: (r) => <StatusBadge status={r.is_active ? 'active' : 'suspended'} />,
    },
    {
      key: 'created_at', header: 'Created', sortable: true,
      render: (r) => new Date(r.created_at as string).toLocaleDateString(),
    },
  ];

  if (error) return <div className="dash-error">{error}</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Users</h2>
        <button
          className="dash-btn dash-btn-primary"
          onClick={() => { setShowCreate(!showCreate); setSubmitError(null); }}
        >
          {showCreate ? 'Cancel' : '+ Create User'}
        </button>
      </div>

      {showCreate && (
        <div className="dash-section">
          <h3 className="dash-section-title">New User</h3>
          <form onSubmit={handleCreate} noValidate>
            <div className="dash-form-grid">
              <FormField label="Username" required error={formErrors.username} hint="Lowercase, 3–32 chars">
                <input
                  className="dash-form-input"
                  value={form.username}
                  onChange={(e) => setField('username', e.target.value.toLowerCase())}
                  placeholder="username"
                />
              </FormField>
              <FormField label="Email" required error={formErrors.email}>
                <input
                  className="dash-form-input"
                  type="email"
                  value={form.email}
                  onChange={(e) => setField('email', e.target.value)}
                  placeholder="user@example.com"
                />
              </FormField>
              <FormField label="Password" required error={formErrors.password}>
                <input
                  className="dash-form-input"
                  type="password"
                  value={form.password}
                  onChange={(e) => setField('password', e.target.value)}
                  placeholder="Min 8 characters"
                />
              </FormField>
              <FormField label="Role">
                <select
                  className="dash-form-input dash-select"
                  value={form.role}
                  onChange={(e) => setField('role', e.target.value)}
                >
                  <option value="user">User</option>
                  <option value="admin">Admin</option>
                </select>
              </FormField>
            </div>
            {submitError && <div className="dash-form-error">{submitError}</div>}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
              <button type="submit" className="dash-btn dash-btn-primary" disabled={submitting}>
                {submitting ? 'Creating...' : 'Create User'}
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={() => { setShowCreate(false); setForm(EMPTY_FORM); setFormErrors({}); }}
              >
                Cancel
              </button>
            </div>
          </form>
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
          value={roleFilter}
          onChange={(e) => { setRoleFilter(e.target.value); setPage(1); }}
        >
          <option value="all">All Roles</option>
          <option value="admin">Admin</option>
          <option value="reseller">Reseller</option>
          <option value="user">User</option>
        </select>
        <select
          className="dash-select"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
        >
          <option value="all">All Status</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
        </select>
      </div>

      {data && (
        <DataTable
          columns={columns}
          data={data.users as unknown as Record<string, unknown>[]}
          keyField="id"
          onRowClick={(r) => navigate(`/admin/users/${r.id}`, { state: { user: r as unknown as AdminUser } })}
          emptyMessage="No users found"
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
