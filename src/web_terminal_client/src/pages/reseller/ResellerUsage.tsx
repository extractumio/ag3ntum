import { useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { getResellerUsage, exportUsage } from '../../adminApi';
import { StatsCard, DataTable } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { UsageResponse } from '../../types/admin';

type Period = 'current_month' | 'last_month';

export function ResellerUsage() {
  const { token, baseUrl } = useAuth();
  const [period, setPeriod] = useState<Period>('current_month');
  const [data, setData] = useState<UsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (!token || !baseUrl) return;
    setData(null);
    setError(null);
    getResellerUsage(baseUrl, token, { period })
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl, period]);

  const handleExport = async () => {
    if (!token || !baseUrl) return;
    setExporting(true);
    try {
      const csv = await exportUsage(baseUrl, token, { format: 'csv', period });
      const blob = new Blob([csv], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `usage-${period}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const userColumns: Column<Record<string, unknown>>[] = [
    { key: 'username', header: 'User', sortable: true },
    { key: 'sessions', header: 'Sessions', sortable: true },
    {
      key: 'cost_usd', header: 'Cost (USD)', sortable: true,
      render: (r) => `$${(r.cost_usd as number).toFixed(4)}`,
    },
  ];

  const dayColumns: Column<Record<string, unknown>>[] = [
    { key: 'date', header: 'Date', sortable: true },
    { key: 'sessions', header: 'Sessions', sortable: true },
    {
      key: 'cost_usd', header: 'Cost (USD)', sortable: true,
      render: (r) => `$${(r.cost_usd as number).toFixed(4)}`,
    },
  ];

  if (error) return <div className="dash-error">{error}</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Usage</h2>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <select
            className="dash-select"
            value={period}
            onChange={(e) => setPeriod(e.target.value as Period)}
          >
            <option value="current_month">Current Month</option>
            <option value="last_month">Last Month</option>
          </select>
          <button
            className="dash-btn dash-btn-secondary"
            onClick={handleExport}
            disabled={exporting || !data}
          >
            {exporting ? 'Exporting...' : 'Export CSV'}
          </button>
        </div>
      </div>

      {!data ? (
        <div className="dash-loading">Loading usage data...</div>
      ) : (
        <>
          <div className="dash-stats-row">
            <StatsCard
              label="Sessions"
              value={data.totals.sessions}
              accent="blue"
            />
            <StatsCard
              label="Active Users"
              value={data.totals.active_users}
            />
            <StatsCard
              label="Total Cost"
              value={`$${data.totals.cost_usd.toFixed(2)}`}
              accent="yellow"
            />
            <StatsCard
              label="Input Tokens"
              value={data.totals.input_tokens.toLocaleString()}
            />
            <StatsCard
              label="Output Tokens"
              value={data.totals.output_tokens.toLocaleString()}
            />
          </div>

          <div style={{ fontSize: '0.8rem', opacity: 0.6, marginBottom: '1rem' }}>
            Period: {new Date(data.period.start).toLocaleDateString()} — {new Date(data.period.end).toLocaleDateString()}
          </div>

          {data.by_user && data.by_user.length > 0 && (
            <div className="dash-section">
              <h3 className="dash-section-title">By User</h3>
              <DataTable
                columns={userColumns}
                data={data.by_user as unknown as Record<string, unknown>[]}
                keyField="user_id"
                emptyMessage="No user data"
              />
            </div>
          )}

          {data.by_day && data.by_day.length > 0 && (
            <div className="dash-section">
              <h3 className="dash-section-title">By Day</h3>
              <DataTable
                columns={dayColumns}
                data={data.by_day as unknown as Record<string, unknown>[]}
                keyField="date"
                emptyMessage="No daily data"
              />
            </div>
          )}

          {(!data.by_user || data.by_user.length === 0) && (!data.by_day || data.by_day.length === 0) && (
            <p style={{ opacity: 0.6, fontSize: '0.875rem' }}>No detailed breakdown available for this period.</p>
          )}
        </>
      )}
    </div>
  );
}
