import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import { listResellers, createReseller } from '../../adminApi';
import { DataTable, StatusBadge } from '../../components/dashboard';
import { FormField } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { ResellerListResponse } from '../../types/admin';

interface CreateForm {
  name: string;
  contact_email: string;
  password: string;
  company: string;
  max_users: string;
  max_concurrent_tasks: string;
  max_daily_tasks: string;
  max_monthly_spending_usd: string;
  max_daily_spending_usd: string;
  notes: string;
}

const EMPTY_FORM: CreateForm = {
  name: '',
  contact_email: '',
  password: '',
  company: '',
  max_users: '50',
  max_concurrent_tasks: '10',
  max_daily_tasks: '500',
  max_monthly_spending_usd: '',
  max_daily_spending_usd: '',
  notes: '',
};

function validateForm(form: CreateForm): Partial<Record<keyof CreateForm, string>> {
  const errors: Partial<Record<keyof CreateForm, string>> = {};
  if (!form.name.trim()) errors.name = 'Name is required';
  if (!form.contact_email.trim()) errors.contact_email = 'Email is required';
  else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.contact_email)) errors.contact_email = 'Invalid email';
  if (!form.password) errors.password = 'Password is required';
  else if (form.password.length < 8) errors.password = 'Min 8 characters';
  return errors;
}

export function ResellerList() {
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<ResellerListResponse | null>(null);
  const [search, setSearch] = useState('');
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
    listResellers(baseUrl, token, { page, status: statusFilter, search: search || undefined })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, baseUrl, page, statusFilter, search]);

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
      const body: Record<string, unknown> = {
        name: form.name.trim(),
        contact_email: form.contact_email.trim(),
        password: form.password,
        max_users: parseInt(form.max_users, 10) || 50,
        max_concurrent_tasks: parseInt(form.max_concurrent_tasks, 10) || 10,
        max_daily_tasks: parseInt(form.max_daily_tasks, 10) || 500,
      };
      if (form.company.trim()) body.company = form.company.trim();
      if (form.max_monthly_spending_usd) body.max_monthly_spending_usd = parseFloat(form.max_monthly_spending_usd);
      if (form.max_daily_spending_usd) body.max_daily_spending_usd = parseFloat(form.max_daily_spending_usd);
      if (form.notes.trim()) body.notes = form.notes.trim();

      await createReseller(baseUrl!, token!, body);
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
    { key: 'name', header: 'Name', sortable: true },
    { key: 'company', header: 'Company', sortable: true },
    { key: 'contact_email', header: 'Email' },
    {
      key: 'is_active', header: 'Status', sortable: true,
      render: (r) => <StatusBadge status={r.is_active ? 'active' : 'suspended'} />,
    },
    {
      key: 'limits', header: 'Users',
      render: (r) => {
        const lim = r.limits as { current_users: number; max_users: number };
        return `${lim.current_users}/${lim.max_users}`;
      },
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
        <h2 className="dash-page-title">Resellers</h2>
        <button
          className="dash-btn dash-btn-primary"
          onClick={() => { setShowCreate(!showCreate); setSubmitError(null); }}
        >
          {showCreate ? 'Cancel' : '+ Create Reseller'}
        </button>
      </div>

      {showCreate && (
        <div className="dash-section">
          <h3 className="dash-section-title">New Reseller</h3>
          <form onSubmit={handleCreate} noValidate>
            <div className="dash-form-grid">
              <FormField label="Name" required error={formErrors.name}>
                <input
                  className="dash-form-input"
                  value={form.name}
                  onChange={(e) => setField('name', e.target.value)}
                  placeholder="Reseller name"
                />
              </FormField>
              <FormField label="Contact Email" required error={formErrors.contact_email}>
                <input
                  className="dash-form-input"
                  type="email"
                  value={form.contact_email}
                  onChange={(e) => setField('contact_email', e.target.value)}
                  placeholder="admin@example.com"
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
              <FormField label="Company" error={formErrors.company}>
                <input
                  className="dash-form-input"
                  value={form.company}
                  onChange={(e) => setField('company', e.target.value)}
                  placeholder="Optional"
                />
              </FormField>
              <FormField label="Max Users">
                <input
                  className="dash-form-input"
                  type="number"
                  min={1}
                  value={form.max_users}
                  onChange={(e) => setField('max_users', e.target.value)}
                />
              </FormField>
              <FormField label="Max Concurrent Tasks">
                <input
                  className="dash-form-input"
                  type="number"
                  min={1}
                  value={form.max_concurrent_tasks}
                  onChange={(e) => setField('max_concurrent_tasks', e.target.value)}
                />
              </FormField>
              <FormField label="Max Daily Tasks">
                <input
                  className="dash-form-input"
                  type="number"
                  min={1}
                  value={form.max_daily_tasks}
                  onChange={(e) => setField('max_daily_tasks', e.target.value)}
                />
              </FormField>
              <FormField label="Max Monthly Spending (USD)" hint="Leave blank for no limit">
                <input
                  className="dash-form-input"
                  type="number"
                  min={0}
                  step={0.01}
                  value={form.max_monthly_spending_usd}
                  onChange={(e) => setField('max_monthly_spending_usd', e.target.value)}
                  placeholder="Optional"
                />
              </FormField>
              <FormField label="Max Daily Spending (USD)" hint="Leave blank for no limit">
                <input
                  className="dash-form-input"
                  type="number"
                  min={0}
                  step={0.01}
                  value={form.max_daily_spending_usd}
                  onChange={(e) => setField('max_daily_spending_usd', e.target.value)}
                  placeholder="Optional"
                />
              </FormField>
            </div>
            <FormField label="Notes">
              <textarea
                className="dash-form-input"
                rows={3}
                value={form.notes}
                onChange={(e) => setField('notes', e.target.value)}
                placeholder="Optional notes"
              />
            </FormField>
            {submitError && <div className="dash-form-error">{submitError}</div>}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
              <button
                type="submit"
                className="dash-btn dash-btn-primary"
                disabled={submitting}
              >
                {submitting ? 'Creating...' : 'Create Reseller'}
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
          placeholder="Search resellers..."
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
          data={data.resellers as unknown as Record<string, unknown>[]}
          keyField="id"
          onRowClick={(r) => navigate(`/admin/resellers/${r.id}`)}
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
