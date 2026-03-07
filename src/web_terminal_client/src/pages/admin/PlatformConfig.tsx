import { useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import {
  getPlatformConfig, updatePlatformConfig,
  getRetentionConfig, updateRetentionConfig, runRetention,
} from '../../adminApi';
import { ConfirmDialog, FormField, JsonEditor } from '../../components/dashboard';
import type { PlatformConfig as PlatformConfigType, RetentionConfig } from '../../types/admin';

export function PlatformConfig() {
  const { token, baseUrl } = useAuth();

  // Platform config state
  const [config, setConfig] = useState<PlatformConfigType | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configSaving, setConfigSaving] = useState(false);

  const [features, setFeatures] = useState<Record<string, unknown>>({});
  const [quotas, setQuotas] = useState<Record<string, unknown>>({});
  const [spending, setSpending] = useState<Record<string, unknown>>({});
  const [configDirty, setConfigDirty] = useState(false);

  // Retention state
  const [retention, setRetention] = useState<RetentionConfig | null>(null);
  const [retentionForm, setRetentionForm] = useState<RetentionConfig>({ usage_records: 90, events: 30, webhook_delivery_log: 30, api_key_audit_log: 365 });
  const [retentionDirty, setRetentionDirty] = useState(false);
  const [retentionSaving, setRetentionSaving] = useState(false);
  const [retentionError, setRetentionError] = useState<string | null>(null);

  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);
  const [purgeResult, setPurgeResult] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !baseUrl) return;
    Promise.all([
      getPlatformConfig(baseUrl, token),
      getRetentionConfig(baseUrl, token),
    ]).then(([c, r]) => {
      setConfig(c);
      setFeatures(c.default_features ?? {});
      setQuotas(c.default_quotas ?? {});
      setSpending(c.default_spending_limits ?? {});
      setRetention(r);
      setRetentionForm(r);
    }).catch((e) => {
      setConfigError(e instanceof Error ? e.message : String(e));
    });
  }, [token, baseUrl]);

  const handleSaveConfig = async () => {
    if (!token || !baseUrl) return;
    setConfigSaving(true);
    setConfigError(null);
    try {
      const updated = await updatePlatformConfig(baseUrl, token, { features, quotas, spending });
      setConfig(updated);
      setFeatures(updated.default_features ?? {});
      setQuotas(updated.default_quotas ?? {});
      setSpending(updated.default_spending_limits ?? {});
      setConfigDirty(false);
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : String(e));
    } finally {
      setConfigSaving(false);
    }
  };

  const handleSaveRetention = async () => {
    if (!token || !baseUrl) return;
    setRetentionSaving(true);
    setRetentionError(null);
    try {
      const updated = await updateRetentionConfig(baseUrl, token, retentionForm);
      setRetention(updated);
      setRetentionDirty(false);
    } catch (e) {
      setRetentionError(e instanceof Error ? e.message : String(e));
    } finally {
      setRetentionSaving(false);
    }
  };

  const handlePurge = async () => {
    if (!token || !baseUrl) return;
    setShowPurgeConfirm(false);
    setPurgeResult(null);
    try {
      const result = await runRetention(baseUrl, token);
      setPurgeResult(`Purge complete. ${result.total_purged} records removed.`);
    } catch (e) {
      setRetentionError(e instanceof Error ? e.message : String(e));
    }
  };

  const setRetentionField = (key: keyof RetentionConfig, value: number) => {
    setRetentionForm((prev) => ({ ...prev, [key]: value }));
    setRetentionDirty(true);
  };

  if (!config && !configError) return <div className="dash-loading">Loading...</div>;

  return (
    <div>
      <h2 className="dash-page-title">Platform Configuration</h2>

      <div className="dash-section">
        <h3 className="dash-section-title">Default Features</h3>
        {config && (
          <JsonEditor
            value={features}
            onChange={(v) => { setFeatures(v); setConfigDirty(true); }}
            rows={8}
          />
        )}
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Default Quotas</h3>
        {config && (
          <JsonEditor
            value={quotas}
            onChange={(v) => { setQuotas(v); setConfigDirty(true); }}
            rows={6}
          />
        )}
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Default Spending Limits</h3>
        {config && (
          <JsonEditor
            value={spending}
            onChange={(v) => { setSpending(v); setConfigDirty(true); }}
            rows={6}
          />
        )}
        {configError && <div className="dash-form-error">{configError}</div>}
        <div style={{ marginTop: '1rem' }}>
          <button
            className="dash-btn dash-btn-primary"
            onClick={handleSaveConfig}
            disabled={!configDirty || configSaving}
          >
            {configSaving ? 'Saving...' : configDirty ? 'Save Config' : 'Saved'}
          </button>
        </div>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Data Retention</h3>
        <p style={{ fontSize: '0.85rem', opacity: 0.7, marginBottom: '1rem' }}>
          Number of days to retain each record type before purging.
        </p>
        <div className="dash-form-grid">
          <FormField label="Usage Records (days)">
            <input
              className="dash-form-input"
              type="number"
              min={1}
              value={retentionForm.usage_records}
              onChange={(e) => setRetentionField('usage_records', parseInt(e.target.value, 10) || 90)}
            />
          </FormField>
          <FormField label="Events (days)">
            <input
              className="dash-form-input"
              type="number"
              min={1}
              value={retentionForm.events}
              onChange={(e) => setRetentionField('events', parseInt(e.target.value, 10) || 30)}
            />
          </FormField>
          <FormField label="Webhook Delivery Log (days)">
            <input
              className="dash-form-input"
              type="number"
              min={1}
              value={retentionForm.webhook_delivery_log}
              onChange={(e) => setRetentionField('webhook_delivery_log', parseInt(e.target.value, 10) || 30)}
            />
          </FormField>
          <FormField label="API Key Audit Log (days)">
            <input
              className="dash-form-input"
              type="number"
              min={1}
              value={retentionForm.api_key_audit_log}
              onChange={(e) => setRetentionField('api_key_audit_log', parseInt(e.target.value, 10) || 365)}
            />
          </FormField>
        </div>
        {retentionError && <div className="dash-form-error">{retentionError}</div>}
        {purgeResult && <div className="dash-success">{purgeResult}</div>}
        {retention && (
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
            <button
              className="dash-btn dash-btn-primary"
              onClick={handleSaveRetention}
              disabled={!retentionDirty || retentionSaving}
            >
              {retentionSaving ? 'Saving...' : retentionDirty ? 'Save Retention' : 'Saved'}
            </button>
            <button
              className="dash-btn dash-btn-danger"
              onClick={() => setShowPurgeConfirm(true)}
            >
              Run Purge Now
            </button>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={showPurgeConfirm}
        title="Run Purge Now"
        message="This will immediately purge all records older than the configured retention periods. This cannot be undone."
        confirmLabel="Run Purge"
        danger
        onConfirm={handlePurge}
        onCancel={() => setShowPurgeConfirm(false)}
      />
    </div>
  );
}
