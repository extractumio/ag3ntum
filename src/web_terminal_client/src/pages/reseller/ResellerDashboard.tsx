import { useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { getResellerProfile } from '../../adminApi';
import { StatsCard, StatusBadge } from '../../components/dashboard';
import type { Reseller } from '../../types/admin';

export function ResellerDashboard() {
  const { token, baseUrl } = useAuth();
  const [profile, setProfile] = useState<Reseller | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !baseUrl) return;
    getResellerProfile(baseUrl, token)
      .then((data) => setProfile(data as Reseller))
      .catch((e) => setError(e.message));
  }, [token, baseUrl]);

  if (error) return <div className="dash-error">{error}</div>;
  if (!profile) return <div className="dash-loading">Loading profile...</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">
          {profile.name}
          <StatusBadge status={profile.is_active ? 'active' : 'suspended'} />
        </h2>
      </div>
      <div className="dash-stats-row">
        <StatsCard
          label="Users"
          value={`${profile.limits.current_users}/${profile.limits.max_users}`}
          accent="blue"
        />
        <StatsCard
          label="Monthly Spend"
          value={`$${profile.spending?.current?.monthly_usd?.toFixed(2) ?? '0.00'}`}
          sublabel={profile.spending?.limits?.monthly_usd ? `of $${profile.spending.limits.monthly_usd}` : 'No limit'}
          accent="yellow"
        />
        <StatsCard
          label="Daily Spend"
          value={`$${profile.spending?.current?.daily_usd?.toFixed(2) ?? '0.00'}`}
          sublabel={profile.spending?.limits?.daily_usd ? `of $${profile.spending.limits.daily_usd}` : 'No limit'}
        />
        <StatsCard label="Max Concurrent" value={profile.limits.max_concurrent_tasks} />
      </div>
    </div>
  );
}
