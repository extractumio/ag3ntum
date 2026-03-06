import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { getPlatformUsage } from '../../adminApi';
import { StatsCard, DataTable } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { UsageResponse } from '../../types/admin';

const PERIOD_OPTIONS = [
  { value: 'day', label: 'Today' },
  { value: 'week', label: 'This Week' },
  { value: 'month', label: 'This Month' },
];

export function AdminUsage() {
  const { token, baseUrl } = useAuth();
  const [period, setPeriod] = useState('month');
  const [data, setData] = useState<UsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchUsage = useCallback(() => {
    if (!token || !baseUrl) return;
    getPlatformUsage(baseUrl, token, { period })
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl, period]);

  useEffect(() => { fetchUsage(); }, [fetchUsage]);

  const userColumns: Column<Record<string, unknown>>[] = [
    { key: 'username', header: 'Username', sortable: true },
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

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Usage</h2>
        <select
          className="dash-select"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
        >
          {PERIOD_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {!data && !error && <div className="dash-loading">Loading...</div>}
      {error && <div className="dash-error">{error}</div>}

      {data && (
        <>
          <div className="dash-stats-row">
            <StatsCard label="Sessions" value={data.totals.sessions} accent="blue" />
            <StatsCard label="Active Users" value={data.totals.active_users} accent="green" />
            <StatsCard
              label="Input Tokens"
              value={data.totals.input_tokens.toLocaleString()}
            />
            <StatsCard
              label="Output Tokens"
              value={data.totals.output_tokens.toLocaleString()}
            />
            <StatsCard
              label="Total Cost"
              value={`$${data.totals.cost_usd.toFixed(4)}`}
              accent="yellow"
            />
          </div>

          {data.period && (
            <p style={{ fontSize: '0.8rem', opacity: 0.6, marginBottom: '1rem' }}>
              Period: {new Date(data.period.start).toLocaleDateString()} — {new Date(data.period.end).toLocaleDateString()}
            </p>
          )}

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
        </>
      )}
    </div>
  );
}
