import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import { getReseller, suspendReseller, unsuspendReseller } from '../../adminApi';
import { StatsCard, StatusBadge, ConfirmDialog } from '../../components/dashboard';
import type { Reseller } from '../../types/admin';

export function ResellerDetail() {
  const { id } = useParams<{ id: string }>();
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [reseller, setReseller] = useState<Reseller | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<'suspend' | 'unsuspend' | null>(null);

  useEffect(() => {
    if (!token || !baseUrl || !id) return;
    getReseller(baseUrl, token, id)
      .then(setReseller)
      .catch((e) => setError(e.message));
  }, [token, baseUrl, id]);

  const handleToggleSuspend = async () => {
    if (!token || !baseUrl || !id || !reseller) return;
    try {
      if (reseller.is_active) {
        await suspendReseller(baseUrl, token, id);
      } else {
        await unsuspendReseller(baseUrl, token, id);
      }
      const updated = await getReseller(baseUrl, token, id);
      setReseller(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setConfirmAction(null);
  };

  if (error) return <div className="dash-error">{error}</div>;
  if (!reseller) return <div className="dash-loading">Loading...</div>;

  return (
    <div>
      <div className="dash-page-header">
        <div>
          <h2 className="dash-page-title">
            {reseller.name}
            <StatusBadge status={reseller.is_active ? 'active' : 'suspended'} />
          </h2>
          <span style={{ fontSize: '0.8rem', opacity: 0.6 }}>
            {reseller.contact_email} {reseller.company && `| ${reseller.company}`}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            className={`dash-btn ${reseller.is_active ? 'dash-btn-danger' : 'dash-btn-primary'}`}
            onClick={() => setConfirmAction(reseller.is_active ? 'suspend' : 'unsuspend')}
          >
            {reseller.is_active ? 'Suspend' : 'Unsuspend'}
          </button>
          <button className="dash-btn dash-btn-secondary" onClick={() => navigate('/admin/resellers')}>
            Back
          </button>
        </div>
      </div>

      <div className="dash-stats-row">
        <StatsCard label="Users" value={`${reseller.limits.current_users}/${reseller.limits.max_users}`} accent="blue" />
        <StatsCard
          label="Monthly Spend"
          value={`$${reseller.spending.current.monthly_usd.toFixed(2)}`}
          sublabel={reseller.spending.limits.monthly_usd ? `of $${reseller.spending.limits.monthly_usd}` : 'No limit'}
          accent={reseller.spending.current.monthly_usd > 0 ? 'yellow' : 'default'}
        />
        <StatsCard label="Max Concurrent" value={reseller.limits.max_concurrent_tasks} />
        <StatsCard label="Max Daily Tasks" value={reseller.limits.max_daily_tasks} />
      </div>

      {reseller.stats && (
        <div className="dash-section">
          <h3 className="dash-section-title">Statistics</h3>
          <div className="dash-stats-row">
            <StatsCard label="Total Sessions" value={reseller.stats.total_sessions} />
            <StatsCard label="Total Cost" value={`$${reseller.stats.total_cost_usd.toFixed(2)}`} accent="yellow" />
            <StatsCard label="API Keys Active" value={reseller.stats.api_keys_active} accent="green" />
            <StatsCard label="This Month" value={`$${reseller.stats.cost_this_month_usd.toFixed(2)}`} />
          </div>
        </div>
      )}

      {reseller.features && Object.keys(reseller.features).length > 0 && (
        <div className="dash-section">
          <h3 className="dash-section-title">Feature Overrides</h3>
          <div className="dash-table-wrap">
            <table className="dash-table">
              <thead>
                <tr><th>Feature</th><th>Value</th></tr>
              </thead>
              <tbody>
                {Object.entries(reseller.features).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td>{String(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction === 'suspend' ? 'Suspend Reseller' : 'Unsuspend Reseller'}
        message={
          confirmAction === 'suspend'
            ? `This will suspend ${reseller.name} and all their users. Active sessions will be cancelled.`
            : `This will restore ${reseller.name} and their users.`
        }
        confirmLabel={confirmAction === 'suspend' ? 'Suspend' : 'Unsuspend'}
        danger={confirmAction === 'suspend'}
        onConfirm={handleToggleSuspend}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  );
}
