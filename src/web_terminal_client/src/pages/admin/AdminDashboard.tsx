import { useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { getPlatformStats } from '../../adminApi';
import { StatsCard } from '../../components/dashboard';
import type { PlatformStats } from '../../types/admin';

export function AdminDashboard() {
  const { token, baseUrl } = useAuth();
  const [stats, setStats] = useState<PlatformStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !baseUrl) return;
    getPlatformStats(baseUrl, token)
      .then(setStats)
      .catch((e) => setError(e.message));
  }, [token, baseUrl]);

  if (error) return <div className="dash-error">{error}</div>;
  if (!stats) return <div className="dash-loading">Loading stats...</div>;

  return (
    <div>
      <h2 className="dash-page-title">Platform Overview</h2>
      <div className="dash-stats-row">
        <StatsCard label="Version" value={stats.platform.version} />
        <StatsCard label="Total Resellers" value={stats.resellers.total} accent="blue" />
        <StatsCard label="Active Users" value={stats.users.active} accent="green" />
        <StatsCard label="Sessions Today" value={stats.sessions.today} accent="blue" />
      </div>
      <div className="dash-stats-row">
        <StatsCard
          label="Active Now"
          value={stats.sessions.active_now}
          sublabel={`${stats.sessions.queued} queued`}
          accent="green"
        />
        <StatsCard
          label="Cost This Month"
          value={`$${stats.usage_this_month.cost_usd.toFixed(2)}`}
          sublabel={`${stats.usage_this_month.total_sessions} sessions`}
          accent="yellow"
        />
        <StatsCard
          label="Tokens This Month"
          value={(stats.usage_this_month.input_tokens + stats.usage_this_month.output_tokens).toLocaleString()}
          sublabel={`in: ${stats.usage_this_month.input_tokens.toLocaleString()} / out: ${stats.usage_this_month.output_tokens.toLocaleString()}`}
        />
        <StatsCard
          label="Suspended"
          value={stats.users.suspended + stats.resellers.suspended}
          accent={stats.users.suspended > 0 ? 'red' : 'default'}
          sublabel={`${stats.resellers.suspended} resellers, ${stats.users.suspended} users`}
        />
      </div>
    </div>
  );
}
