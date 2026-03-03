import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { listApiKeys, createApiKey, revokeApiKey, rotateApiKey } from '../../adminApi';
import { DataTable, StatusBadge, SecretDisplay, ConfirmDialog } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';

interface ApiKeyData {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

interface ApiKeysResponse {
  keys: ApiKeyData[];
}

export function ApiKeyManagement() {
  const { token, baseUrl } = useAuth();
  const [keys, setKeys] = useState<ApiKeyData[]>([]);
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);

  const fetchKeys = useCallback(() => {
    if (!token || !baseUrl) return;
    listApiKeys(baseUrl, token)
      .then((data) => setKeys((data as ApiKeysResponse).keys || []))
      .catch((e) => setError(e.message));
  }, [token, baseUrl]);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  const handleCreate = async () => {
    if (!token || !baseUrl || !createName.trim()) return;
    try {
      const result = await createApiKey(baseUrl, token, {
        name: createName.trim(),
        scopes: ['users:manage', 'usage:read'],
      });
      setNewSecret((result as { key: string }).key);
      setShowCreate(false);
      setCreateName('');
      fetchKeys();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
      setNewSecret((result as { key: string }).key);
      fetchKeys();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const columns: Column<Record<string, unknown>>[] = [
    { key: 'name', header: 'Name', sortable: true },
    { key: 'key_prefix', header: 'Prefix' },
    {
      key: 'is_active', header: 'Status',
      render: (r) => <StatusBadge status={r.is_active ? 'active' : 'suspended'} />,
    },
    {
      key: 'last_used_at', header: 'Last Used',
      render: (r) => r.last_used_at ? new Date(r.last_used_at as string).toLocaleDateString() : 'Never',
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
        <button className="dash-btn dash-btn-primary" onClick={() => setShowCreate(true)}>
          Create Key
        </button>
      </div>

      {error && <div className="dash-error">{error}</div>}
      {newSecret && <SecretDisplay value={newSecret} label="New API Key" />}

      {showCreate && (
        <div className="dash-section" style={{ marginBottom: '1rem' }}>
          <div className="dash-form-group">
            <label className="dash-form-label">Key Name</label>
            <input
              className="dash-form-input"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              placeholder="e.g. WHMCS Integration"
            />
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="dash-btn dash-btn-primary" onClick={handleCreate}>Create</button>
            <button className="dash-btn dash-btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </div>
      )}

      <DataTable
        columns={columns}
        data={keys as unknown as Record<string, unknown>[]}
        keyField="id"
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
