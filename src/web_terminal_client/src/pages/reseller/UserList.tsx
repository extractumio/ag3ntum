import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import { listResellerUsers } from '../../adminApi';
import { DataTable, StatusBadge } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';

interface ResellerUser {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

interface UserListData {
  users: ResellerUser[];
  pagination: { page: number; total_pages: number };
}

export function UserList() {
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<UserListData | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(() => {
    if (!token || !baseUrl) return;
    listResellerUsers(baseUrl, token, {
      page,
      status: statusFilter,
      search: search || undefined,
    })
      .then((d) => setData(d as UserListData))
      .catch((e) => setError(e.message));
  }, [token, baseUrl, page, statusFilter, search]);

  useEffect(() => { fetchData(); }, [fetchData]);

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
  ];

  if (error) return <div className="dash-error">{error}</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Users</h2>
      </div>
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
