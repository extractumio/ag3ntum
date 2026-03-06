import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { listApiKeys, createApiKey, revokeApiKey, rotateApiKey } from '../../adminApi';
import {
  DataTable,
  StatusBadge,
  SecretDisplay,
  ConfirmDialog,
  CheckboxGroup,
  TagInput,
  FormField,
} from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { ApiKey } from '../../types/admin';
import { VALID_API_KEY_SCOPES } from '../../types/admin';

const SCOPE_OPTIONS = VALID_API_KEY_SCOPES.map((s) => ({ value: s, label: s }));

interface CreateForm {
  name: string;
  scopes: string[];
  ipAllowlist: string[];
  rateLimit: string;
  expiresAt: string;
}

const EMPTY_FORM: CreateForm = {
  name: '',
  scopes: [],
  ipAllowlist: [],
  rateLimit: '60',
  expiresAt: '',
};

export function ApiKeyManagement() {
  const { token, baseUrl } = useAuth();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);

  const fetchKeys = useCallback(() => {
    if (!token || !baseUrl) return;
    listApiKeys(baseUrl, token)
      .then((data) => setKeys(data.api_keys || []))
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl]);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  const handleCreate = async () => {
    if (!token || !baseUrl || !form.name.trim()) {
      setCreateError('Key name is required');
      return;
    }
    if (form.scopes.length === 0) {
      setCreateError('Select at least one scope');
      return;
    }
    const rateLimitNum = parseInt(form.rateLimit, 10);
    if (isNaN(rateLimitNum) || rateLimitNum < 1 || rateLimitNum > 1000) {
      setCreateError('Rate limit must be 1-1000');
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      const body: Parameters<typeof createApiKey>[2] = {
        name: form.name.trim(),
        scopes: form.scopes,
        ip_allowlist: form.ipAllowlist.length > 0 ? form.ipAllowlist : null,
        rate_limit_per_minute: rateLimitNum,
      };
      if (form.expiresAt) {
        body.expires_at = new Date(form.expiresAt).toISOString();
      }
      const result = await createApiKey(baseUrl, token, body);
      setNewSecret(result.key);
      setShowCreate(false);
      setForm(EMPTY_FORM);
      fetchKeys();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async () => {
    if (!token || !baseUrl || !confirmRevoke) return;
    try {
      await revokeApiKey(baseUrl, token, confirmRevoke);
      setConfirmRevoke(null);
      fetchKeys();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleRotate = async (id: string) => {
    if (!token || !baseUrl) return;
    try {
      const result = await rotateApiKey(baseUrl, token, id);
      setNewSecret(result.key);
      fetchKeys();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const columns: Column<Record<string, unknown>>[] = [
    { key: 'name', header: 'Name', sortable: true },
    { key: 'key_prefix', header: 'Prefix' },
    {
      key: 'scopes', header: 'Scopes',
      render: (r) => {
        const scopes = r.scopes as string[];
        return <span>{scopes.length} scope{scopes.length !== 1 ? 's' : ''}</span>;
      },
    },
    {
      key: 'ip_allowlist', header: 'IP Allowlist',
      render: (r) => {
        const ips = r.ip_allowlist as string[] | null;
        return <span>{ips && ips.length > 0 ? `${ips.length} IP${ips.length !== 1 ? 's' : ''}` : 'Any'}</span>;
      },
    },
    {
      key: 'rate_limit_per_minute', header: 'Rate Limit',
      render: (r) => <span>{r.rate_limit_per_minute as number}/min</span>,
    },
    {
      key: 'is_active', header: 'Status',
      render: (r) => <StatusBadge status={r.is_active ? 'active' : 'suspended'} />,
    },
    {
      key: 'last_used_at', header: 'Last Used',
      render: (r) => r.last_used_at ? new Date(r.last_used_at as string).toLocaleDateString() : 'Never',
    },
    {
      key: 'expires_at', header: 'Expires',
      render: (r) => r.expires_at ? new Date(r.expires_at as string).toLocaleDateString() : 'Never',
    },
    {
      key: 'actions', header: '',
      render: (r) => (
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            className="dash-btn dash-btn-sm"
            onClick={(e) => { e.stopPropagation(); handleRotate(r.id as string); }}
          >
            Rotate
          </button>
          <button
            className="dash-btn dash-btn-sm dash-btn-danger"
            onClick={(e) => { e.stopPropagation(); setConfirmRevoke(r.id as string); }}
          >
            Revoke
          </button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">API Keys</h2>
        <button
          className="dash-btn dash-btn-primary"
          onClick={() => { setShowCreate(!showCreate); setForm(EMPTY_FORM); setCreateError(null); }}
        >
          {showCreate ? 'Cancel' : 'Create Key'}
        </button>
      </div>

      {error && <div className="dash-error">{error}</div>}
      {newSecret && <SecretDisplay value={newSecret} label="New API Key" />}

      {showCreate && (
        <div className="dash-section" style={{ marginBottom: '1rem' }}>
          <h3 className="dash-section-title">New API Key</h3>
          {createError && <div className="dash-error">{createError}</div>}

          <FormField label="Key Name" required>
            <input
              className="dash-form-input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. WHMCS Integration"
            />
          </FormField>

          <FormField label="Scopes" required hint="Select permissions for this key">
            <CheckboxGroup
              options={SCOPE_OPTIONS}
              selected={form.scopes}
              onChange={(scopes) => setForm({ ...form, scopes })}
              columns={3}
            />
          </FormField>

          <FormField label="IP Allowlist" hint="Leave empty to allow all IPs">
            <TagInput
              tags={form.ipAllowlist}
              onChange={(ipAllowlist) => setForm({ ...form, ipAllowlist })}
              placeholder="192.168.1.0/24"
            />
          </FormField>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <FormField label="Rate Limit (per minute)" hint="1-1000, default 60">
              <input
                className="dash-form-input"
                type="number"
                min="1"
                max="1000"
                value={form.rateLimit}
                onChange={(e) => setForm({ ...form, rateLimit: e.target.value })}
              />
            </FormField>
            <FormField label="Expiry Date" hint="Optional">
              <input
                className="dash-form-input"
                type="date"
                value={form.expiresAt}
                onChange={(e) => setForm({ ...form, expiresAt: e.target.value })}
              />
            </FormField>
          </div>

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className="dash-btn dash-btn-primary"
              onClick={handleCreate}
              disabled={creating}
            >
              {creating ? 'Creating...' : 'Create'}
            </button>
            <button
              className="dash-btn dash-btn-secondary"
              onClick={() => { setShowCreate(false); setForm(EMPTY_FORM); }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <DataTable
        columns={columns}
        data={keys as unknown as Record<string, unknown>[]}
        keyField="id"
        emptyMessage="No API keys yet"
      />

      <ConfirmDialog
        open={confirmRevoke !== null}
        title="Revoke API Key"
        message="This will permanently deactivate this API key. Any integrations using it will stop working."
        confirmLabel="Revoke"
        danger
        onConfirm={handleRevoke}
        onCancel={() => setConfirmRevoke(null)}
      />
    </div>
  );
}
