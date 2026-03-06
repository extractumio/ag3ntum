import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { getAuditLog } from '../../adminApi';
import { DataTable } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { AuditLogResponse } from '../../types/admin';

const AUDIT_COLUMNS: Column<Record<string, unknown>>[] = [
  {
    key: 'timestamp', header: 'Timestamp', sortable: true,
    render: (r) => new Date(r.timestamp as string).toLocaleString(),
  },
  {
    key: 'api_key_name', header: 'API Key',
    render: (r) => r.api_key_name ? String(r.api_key_name) : '—',
  },
  {
    key: 'reseller_name', header: 'Reseller',
    render: (r) => r.reseller_name ? String(r.reseller_name) : '—',
  },
  { key: 'action', header: 'Action', sortable: true },
  {
    key: 'target_user', header: 'Target User',
    render: (r) => r.target_user ? String(r.target_user) : '—',
  },
  { key: 'ip_address', header: 'IP Address' },
  {
    key: 'status_code', header: 'Status', sortable: true,
    render: (r) => {
      const code = r.status_code as number;
      const color = code < 300 ? 'var(--color-success)'
        : code < 400 ? 'var(--color-warning)'
        : 'var(--color-error)';
      return <span style={{ color, fontWeight: 600 }}>{code}</span>;
    },
  },
  {
    key: 'error', header: 'Error',
    render: (r) => r.error
      ? <span className="dash-form-error">{String(r.error)}</span>
      : '—',
  },
];

export function AuditLog() {
  const { token, baseUrl } = useAuth();
  const [data, setData] = useState<AuditLogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState('');
  const [resellerFilter, setResellerFilter] = useState('');

  const fetchData = useCallback(() => {
    if (!token || !baseUrl) return;
    getAuditLog(baseUrl, token, {
      page,
      action: actionFilter || undefined,
      reseller_id: resellerFilter || undefined,
    })
      .then((d) => { setData(d); setError(null); })
      .catch((e) => { setError(e.message); });
  }, [token, baseUrl, page, actionFilter, resellerFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Audit Log</h2>
      </div>

      <div className="dash-toolbar">
        <input
          className="dash-search"
          placeholder="Filter by action..."
          value={actionFilter}
          onChange={(e) => { setActionFilter(e.target.value); setPage(1); }}
        />
        <input
          className="dash-search"
          placeholder="Filter by reseller ID..."
          value={resellerFilter}
          onChange={(e) => { setResellerFilter(e.target.value); setPage(1); }}
        />
      </div>

      {!data && !error && <div className="dash-loading">Loading...</div>}
      {error && <div className="dash-error">{error}</div>}

      {data && (
        <DataTable
          columns={AUDIT_COLUMNS}
          data={data.entries as unknown as Record<string, unknown>[]}
          keyField="id"
          emptyMessage="No audit log entries"
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
