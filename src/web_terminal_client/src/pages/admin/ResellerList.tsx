import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import { listResellers } from '../../adminApi';
import { DataTable, StatusBadge } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { ResellerListResponse } from '../../types/admin';

export function ResellerList() {
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<ResellerListResponse | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(() => {
    if (!token || !baseUrl) return;
    listResellers(baseUrl, token, { page, status: statusFilter, search: search || undefined })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [token, baseUrl, page, statusFilter, search]);

  useEffect(() => { fetch(); }, [fetch]);

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
      </div>
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
