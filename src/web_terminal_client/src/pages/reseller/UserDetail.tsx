import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import { getResellerUser } from '../../adminApi';
import { StatsCard, StatusBadge } from '../../components/dashboard';

interface UserDetailData {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  settings_mode: string;
  spending_limits: {
    monthly_usd: number | null;
    daily_usd: number | null;
    per_session_usd: number | null;
  };
  usage: {
    total_sessions: number;
    total_cost_usd: number;
  };
  created_at: string;
}

export function UserDetail() {
  const { userId } = useParams<{ userId: string }>();
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [user, setUser] = useState<UserDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !baseUrl || !userId) return;
    getResellerUser(baseUrl, token, userId)
      .then((data) => setUser(data as UserDetailData))
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl, userId]);

  if (error) return <div className="dash-error">{error}</div>;
  if (!user) return <div className="dash-loading">Loading user...</div>;

  return (
    <div>
      <div className="dash-page-header">
        <div>
          <h2 className="dash-page-title">
            {user.username}
            <StatusBadge status={user.is_active ? 'active' : 'suspended'} />
          </h2>
          <span style={{ fontSize: '0.8rem', opacity: 0.6 }}>{user.email}</span>
        </div>
        <button className="dash-btn dash-btn-secondary" onClick={() => navigate('/reseller/users')}>
          Back
        </button>
      </div>

      <div className="dash-stats-row">
        <StatsCard label="Settings Mode" value={user.settings_mode || 'default'} />
        <StatsCard
          label="Total Sessions"
          value={user.usage?.total_sessions ?? 0}
          accent="blue"
        />
        <StatsCard
          label="Total Cost"
          value={`$${(user.usage?.total_cost_usd ?? 0).toFixed(2)}`}
          accent="yellow"
        />
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Spending Limits</h3>
        <div className="dash-stats-row">
          <StatsCard
            label="Monthly"
            value={user.spending_limits?.monthly_usd != null ? `$${user.spending_limits.monthly_usd}` : 'Inherit'}
          />
          <StatsCard
            label="Daily"
            value={user.spending_limits?.daily_usd != null ? `$${user.spending_limits.daily_usd}` : 'Inherit'}
          />
          <StatsCard
            label="Per Session"
            value={user.spending_limits?.per_session_usd != null ? `$${user.spending_limits.per_session_usd}` : 'Inherit'}
          />
        </div>
      </div>
    </div>
  );
}
