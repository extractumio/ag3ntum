import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import { getReseller, updateReseller, suspendReseller, unsuspendReseller, deleteReseller } from '../../adminApi';
import {
  StatsCard, StatusBadge, ConfirmDialog, TabbedDetail,
  FormField, ReadonlyField, SpendingBar, JsonEditor, ImpactConfirmDialog,
} from '../../components/dashboard';
import type { Tab } from '../../components/dashboard';
import type { Reseller } from '../../types/admin';

const TABS: Tab[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'limits', label: 'Limits' },
  { id: 'features', label: 'Features' },
  { id: 'statistics', label: 'Statistics' },
  { id: 'notes', label: 'Notes' },
];

export function ResellerDetail() {
  const { id } = useParams<{ id: string }>();
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [reseller, setReseller] = useState<Reseller | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('overview');

  // Suspend/Unsuspend
  const [confirmAction, setConfirmAction] = useState<'suspend' | 'unsuspend' | null>(null);

  // Delete
  const [showDelete, setShowDelete] = useState(false);

  // Limits tab
  const [limitsForm, setLimitsForm] = useState({ max_users: 0, max_concurrent_tasks: 0, max_daily_tasks: 0, alert_threshold_pct: 80 });
  const [limitsDirty, setLimitsDirty] = useState(false);
  const [limitsSaving, setLimitsSaving] = useState(false);
  const [limitsError, setLimitsError] = useState<string | null>(null);

  // Features tab
  const [features, setFeatures] = useState<Record<string, unknown>>({});
  const [featuresDirty, setFeaturesDirty] = useState(false);
  const [featuresSaving, setFeaturesSaving] = useState(false);
  const [featuresError, setFeaturesError] = useState<string | null>(null);

  // Notes tab
  const [notes, setNotes] = useState('');
  const [notesDirty, setNotesDirty] = useState(false);
  const [notesSaving, setNotesSaving] = useState(false);
  const [notesError, setNotesError] = useState<string | null>(null);

  const applyReseller = useCallback((r: Reseller) => {
    setReseller(r);
    setLimitsForm({
      max_users: r.limits.max_users,
      max_concurrent_tasks: r.limits.max_concurrent_tasks,
      max_daily_tasks: r.limits.max_daily_tasks,
      alert_threshold_pct: r.spending.alert_threshold_pct ?? 80,
    });
    setFeatures(r.features ?? {});
    setNotes(r.notes ?? '');
    setLimitsDirty(false);
    setFeaturesDirty(false);
    setNotesDirty(false);
  }, []);

  const refetch = useCallback(() => {
    if (!token || !baseUrl || !id) return;
    getReseller(baseUrl, token, id)
      .then(applyReseller)
      .catch((e) => setError(e.message));
  }, [token, baseUrl, id, applyReseller]);

  useEffect(() => { refetch(); }, [refetch]);

  const handleToggleSuspend = async () => {
    if (!token || !baseUrl || !id || !reseller) return;
    try {
      if (reseller.is_active) {
        await suspendReseller(baseUrl, token, id);
      } else {
        await unsuspendReseller(baseUrl, token, id);
      }
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setConfirmAction(null);
  };

  const handleDelete = async () => {
    if (!token || !baseUrl || !id) return;
    try {
      await deleteReseller(baseUrl, token, id);
      navigate('/admin/resellers');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setShowDelete(false);
  };

  const handleSaveLimits = async () => {
    if (!token || !baseUrl || !id) return;
    setLimitsSaving(true);
    setLimitsError(null);
    try {
      const updated = await updateReseller(baseUrl, token, id, {
        max_users: limitsForm.max_users,
        max_concurrent_tasks: limitsForm.max_concurrent_tasks,
        max_daily_tasks: limitsForm.max_daily_tasks,
        spending_alert_threshold_pct: limitsForm.alert_threshold_pct,
      });
      applyReseller(updated);
    } catch (e) {
      setLimitsError(e instanceof Error ? e.message : String(e));
    } finally {
      setLimitsSaving(false);
    }
  };

  const handleSaveFeatures = async () => {
    if (!token || !baseUrl || !id) return;
    setFeaturesSaving(true);
    setFeaturesError(null);
    try {
      const updated = await updateReseller(baseUrl, token, id, { features });
      applyReseller(updated);
    } catch (e) {
      setFeaturesError(e instanceof Error ? e.message : String(e));
    } finally {
      setFeaturesSaving(false);
    }
  };

  const handleSaveNotes = async () => {
    if (!token || !baseUrl || !id) return;
    setNotesSaving(true);
    setNotesError(null);
    try {
      const updated = await updateReseller(baseUrl, token, id, { notes });
      applyReseller(updated);
    } catch (e) {
      setNotesError(e instanceof Error ? e.message : String(e));
    } finally {
      setNotesSaving(false);
    }
  };

  if (error) return <div className="dash-error">{error}</div>;
  if (!reseller) return <div className="dash-loading">Loading...</div>;

  const impactItems = [
    { label: 'users', count: reseller.stats?.user_count ?? reseller.limits.current_users },
    { label: 'sessions', count: reseller.stats?.total_sessions ?? 0 },
    { label: 'active API keys', count: reseller.stats?.api_keys_active ?? 0 },
  ];

  return (
    <div>
      <div className="dash-page-header">
        <div>
          <h2 className="dash-page-title">
            {reseller.name}
            <StatusBadge status={reseller.is_active ? 'active' : 'suspended'} />
          </h2>
          <span style={{ fontSize: '0.8rem', opacity: 0.6 }}>
            {reseller.contact_email}{reseller.company ? ` | ${reseller.company}` : ''}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            className={`dash-btn ${reseller.is_active ? 'dash-btn-danger' : 'dash-btn-primary'}`}
            onClick={() => setConfirmAction(reseller.is_active ? 'suspend' : 'unsuspend')}
          >
            {reseller.is_active ? 'Suspend' : 'Unsuspend'}
          </button>
          <button className="dash-btn dash-btn-danger" onClick={() => setShowDelete(true)}>
            Delete
          </button>
          <button className="dash-btn dash-btn-secondary" onClick={() => navigate('/admin/resellers')}>
            Back
          </button>
        </div>
      </div>

      <TabbedDetail tabs={TABS} activeTab={activeTab} onTabChange={setActiveTab}>
        {activeTab === 'overview' && (
          <div className="dash-form-grid">
            <ReadonlyField label="Name" value={reseller.name} />
            <ReadonlyField label="Company" value={reseller.company} />
            <ReadonlyField label="Contact Email" value={reseller.contact_email} />
            <ReadonlyField label="Owner Username" value={reseller.owner_username} />
            <div className="dash-form-group">
              <label className="dash-form-label">Status</label>
              <StatusBadge status={reseller.is_active ? 'active' : 'suspended'} />
            </div>
            <ReadonlyField label="Created" value={new Date(reseller.created_at).toLocaleString()} />
            <ReadonlyField label="Updated" value={new Date(reseller.updated_at).toLocaleString()} />
          </div>
        )}

        {activeTab === 'limits' && (
          <div>
            <div className="dash-form-grid">
              <FormField label="Max Users">
                <input
                  className="dash-form-input"
                  type="number"
                  min={1}
                  value={limitsForm.max_users}
                  onChange={(e) => { setLimitsForm((p) => ({ ...p, max_users: parseInt(e.target.value, 10) || 1 })); setLimitsDirty(true); }}
                />
              </FormField>
              <FormField label="Max Concurrent Tasks">
                <input
                  className="dash-form-input"
                  type="number"
                  min={1}
                  value={limitsForm.max_concurrent_tasks}
                  onChange={(e) => { setLimitsForm((p) => ({ ...p, max_concurrent_tasks: parseInt(e.target.value, 10) || 1 })); setLimitsDirty(true); }}
                />
              </FormField>
              <FormField label="Max Daily Tasks">
                <input
                  className="dash-form-input"
                  type="number"
                  min={1}
                  value={limitsForm.max_daily_tasks}
                  onChange={(e) => { setLimitsForm((p) => ({ ...p, max_daily_tasks: parseInt(e.target.value, 10) || 1 })); setLimitsDirty(true); }}
                />
              </FormField>
              <FormField label="Spending Alert Threshold (%)" hint="1–100">
                <input
                  className="dash-form-input"
                  type="number"
                  min={1}
                  max={100}
                  value={limitsForm.alert_threshold_pct}
                  onChange={(e) => { setLimitsForm((p) => ({ ...p, alert_threshold_pct: parseInt(e.target.value, 10) || 80 })); setLimitsDirty(true); }}
                />
              </FormField>
            </div>
            <div style={{ marginTop: '1rem' }}>
              <SpendingBar
                current={reseller.spending.current.monthly_usd}
                limit={reseller.spending.limits.monthly_usd}
                alertThreshold={limitsForm.alert_threshold_pct}
                label="Monthly Spending"
              />
              <SpendingBar
                current={reseller.spending.current.daily_usd}
                limit={reseller.spending.limits.daily_usd}
                alertThreshold={limitsForm.alert_threshold_pct}
                label="Daily Spending"
              />
            </div>
            {limitsError && <div className="dash-form-error">{limitsError}</div>}
            <div style={{ marginTop: '1rem' }}>
              <button
                className="dash-btn dash-btn-primary"
                onClick={handleSaveLimits}
                disabled={!limitsDirty || limitsSaving}
              >
                {limitsSaving ? 'Saving...' : limitsDirty ? 'Save Changes' : 'Saved'}
              </button>
            </div>
          </div>
        )}

        {activeTab === 'features' && (
          <div>
            <p style={{ fontSize: '0.85rem', opacity: 0.7, marginBottom: '0.75rem' }}>
              Platform defaults apply where not overridden. Set a key to null to explicitly inherit.
            </p>
            <JsonEditor
              value={features}
              onChange={(v) => { setFeatures(v); setFeaturesDirty(true); }}
              rows={10}
            />
            {featuresError && <div className="dash-form-error">{featuresError}</div>}
            <div style={{ marginTop: '1rem' }}>
              <button
                className="dash-btn dash-btn-primary"
                onClick={handleSaveFeatures}
                disabled={!featuresDirty || featuresSaving}
              >
                {featuresSaving ? 'Saving...' : featuresDirty ? 'Save Changes' : 'Saved'}
              </button>
            </div>
          </div>
        )}

        {activeTab === 'statistics' && (
          reseller.stats ? (
            <div className="dash-stats-row">
              <StatsCard label="Users" value={reseller.stats.user_count} accent="blue" />
              <StatsCard label="Active (30d)" value={reseller.stats.active_users_30d} accent="green" />
              <StatsCard label="Total Sessions" value={reseller.stats.total_sessions} />
              <StatsCard label="Total Cost" value={`$${reseller.stats.total_cost_usd.toFixed(2)}`} accent="yellow" />
              <StatsCard label="API Keys Active" value={reseller.stats.api_keys_active} accent="green" />
              <StatsCard label="Sessions This Month" value={reseller.stats.sessions_this_month} />
              <StatsCard label="Cost This Month" value={`$${reseller.stats.cost_this_month_usd.toFixed(2)}`} accent="yellow" />
            </div>
          ) : (
            <div style={{ opacity: 0.6, padding: '1rem' }}>No statistics available.</div>
          )
        )}

        {activeTab === 'notes' && (
          <div>
            <FormField label="Notes" hint="Max 5000 characters">
              <textarea
                className="dash-form-input"
                rows={8}
                maxLength={5000}
                value={notes}
                onChange={(e) => { setNotes(e.target.value); setNotesDirty(true); }}
                placeholder="Internal notes about this reseller..."
              />
            </FormField>
            {notesError && <div className="dash-form-error">{notesError}</div>}
            <button
              className="dash-btn dash-btn-primary"
              onClick={handleSaveNotes}
              disabled={!notesDirty || notesSaving}
            >
              {notesSaving ? 'Saving...' : notesDirty ? 'Save Notes' : 'Saved'}
            </button>
          </div>
        )}
      </TabbedDetail>

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

      <ImpactConfirmDialog
        open={showDelete}
        title="Delete Reseller"
        entityName={reseller.name}
        impact={impactItems}
        onConfirm={handleDelete}
        onCancel={() => setShowDelete(false)}
      />
    </div>
  );
}
