import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../AuthContext';
import {
  listWebhooks,
  createWebhook,
  updateWebhook,
  deleteWebhook,
  testWebhook,
  getWebhookDeliveries,
} from '../../adminApi';
import {
  DataTable,
  StatusBadge,
  SecretDisplay,
  ConfirmDialog,
  CheckboxGroup,
  FormField,
} from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { WebhookEndpoint, WebhookDelivery } from '../../types/admin';
import { WEBHOOK_EVENT_TYPES } from '../../types/admin';

const EVENT_OPTIONS = WEBHOOK_EVENT_TYPES.map((e) => ({ value: e, label: e }));

interface CreateForm {
  url: string;
  events: string[];
  description: string;
}

const EMPTY_CREATE: CreateForm = { url: '', events: [], description: '' };

interface EditState {
  webhookId: string;
  url: string;
  events: string[];
  description: string;
  is_active: boolean;
}

interface DeliveryRowState {
  webhookId: string;
  deliveries: WebhookDelivery[] | null;
  loading: boolean;
}

export function WebhookManagement() {
  const { token, baseUrl } = useAuth();
  const [webhooks, setWebhooks] = useState<WebhookEndpoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(EMPTY_CREATE);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newSecret, setNewSecret] = useState<string | null>(null);

  const [editState, setEditState] = useState<EditState | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const testTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const [deliveries, setDeliveries] = useState<DeliveryRowState | null>(null);

  const fetchWebhooks = useCallback(() => {
    if (!token || !baseUrl) return;
    listWebhooks(baseUrl, token)
      .then((r) => setWebhooks(r.webhooks))
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl]);

  useEffect(() => { fetchWebhooks(); }, [fetchWebhooks]);

  useEffect(() => () => {
    Object.values(testTimers.current).forEach(clearTimeout);
  }, []);

  const handleCreate = async () => {
    if (!token || !baseUrl) return;
    if (!createForm.url.trim()) { setCreateError('URL is required'); return; }
    if (createForm.events.length === 0) { setCreateError('Select at least one event'); return; }
    setCreating(true);
    setCreateError(null);
    try {
      const result = await createWebhook(baseUrl, token, {
        url: createForm.url.trim(),
        events: createForm.events,
        description: createForm.description.trim() || undefined,
      });
      setNewSecret(result.secret);
      setShowCreate(false);
      setCreateForm(EMPTY_CREATE);
      fetchWebhooks();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const startEdit = (wh: WebhookEndpoint) => {
    setEditState({
      webhookId: wh.id,
      url: wh.url,
      events: [...wh.events],
      description: wh.description ?? '',
      is_active: wh.is_active,
    });
    setEditError(null);
  };

  const handleSaveEdit = async () => {
    if (!token || !baseUrl || !editState) return;
    setSaving(true);
    setEditError(null);
    try {
      await updateWebhook(baseUrl, token, editState.webhookId, {
        url: editState.url,
        events: editState.events,
        description: editState.description || undefined,
        is_active: editState.is_active,
      });
      setEditState(null);
      fetchWebhooks();
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleToggleActive = async (wh: WebhookEndpoint) => {
    if (!token || !baseUrl) return;
    try {
      await updateWebhook(baseUrl, token, wh.id, { is_active: !wh.is_active });
      fetchWebhooks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleTest = async (id: string) => {
    if (!token || !baseUrl) return;
    try {
      const result = await testWebhook(baseUrl, token, id);
      setTestResult((prev) => ({ ...prev, [id]: result.status }));
      clearTimeout(testTimers.current[id]);
      testTimers.current[id] = setTimeout(() => setTestResult((prev) => { const n = { ...prev }; delete n[id]; return n; }), 5000);
    } catch (e) {
      setTestResult((prev) => ({ ...prev, [id]: `Error: ${e instanceof Error ? e.message : String(e)}` }));
    }
  };

  const handleDelete = async () => {
    if (!token || !baseUrl || !confirmDelete) return;
    try {
      await deleteWebhook(baseUrl, token, confirmDelete);
      setConfirmDelete(null);
      fetchWebhooks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleToggleDeliveries = async (webhookId: string) => {
    if (deliveries?.webhookId === webhookId) {
      setDeliveries(null);
      return;
    }
    if (!token || !baseUrl) return;
    setDeliveries({ webhookId, deliveries: null, loading: true });
    try {
      const r = await getWebhookDeliveries(baseUrl, token, webhookId);
      setDeliveries({ webhookId, deliveries: r.deliveries, loading: false });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeliveries(null);
    }
  };

  const deliveryColumns: Column<Record<string, unknown>>[] = [
    { key: 'event_type', header: 'Event', sortable: true },
    {
      key: 'status', header: 'Status',
      render: (r) => <StatusBadge status={r.status as string} />,
    },
    { key: 'attempts', header: 'Attempts' },
    {
      key: 'response_status', header: 'HTTP',
      render: (r) => String(r.response_status ?? '—'),
    },
    {
      key: 'error', header: 'Error',
      render: (r) => <span style={{ fontSize: '0.8rem', opacity: 0.8 }}>{(r.error as string) || '—'}</span>,
    },
    {
      key: 'created_at', header: 'Time',
      render: (r) => new Date(r.created_at as string).toLocaleString(),
    },
  ];

  if (error) return <div className="dash-error">{error}</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Webhooks</h2>
        <button
          className="dash-btn dash-btn-primary"
          onClick={() => { setShowCreate(!showCreate); setCreateForm(EMPTY_CREATE); setCreateError(null); }}
        >
          {showCreate ? 'Cancel' : 'Create Webhook'}
        </button>
      </div>

      {newSecret && <SecretDisplay value={newSecret} label="Webhook Secret (HMAC-SHA256)" />}

      {showCreate && (
        <div className="dash-section" style={{ marginBottom: '1rem' }}>
          <h3 className="dash-section-title">New Webhook</h3>
          {createError && <div className="dash-error">{createError}</div>}

          <FormField label="URL" required>
            <input
              className="dash-form-input"
              type="url"
              value={createForm.url}
              onChange={(e) => setCreateForm({ ...createForm, url: e.target.value })}
              placeholder="https://your-server.com/webhook"
            />
          </FormField>

          <FormField label="Events" required>
            <CheckboxGroup
              options={EVENT_OPTIONS}
              selected={createForm.events}
              onChange={(events) => setCreateForm({ ...createForm, events })}
              columns={3}
            />
          </FormField>

          <FormField label="Description" hint="Optional">
            <input
              className="dash-form-input"
              value={createForm.description}
              onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
              placeholder="What is this webhook for?"
            />
          </FormField>

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="dash-btn dash-btn-primary" onClick={handleCreate} disabled={creating}>
              {creating ? 'Creating...' : 'Create'}
            </button>
            <button
              className="dash-btn dash-btn-secondary"
              onClick={() => { setShowCreate(false); setCreateForm(EMPTY_CREATE); }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {webhooks.length === 0 ? (
        <div className="dash-table-wrap">
          <p style={{ padding: '2rem', textAlign: 'center', opacity: 0.6 }}>No webhooks configured</p>
        </div>
      ) : (
        webhooks.map((wh) => (
          <div key={wh.id} className="dash-section" style={{ marginBottom: '1rem' }}>
            {editState?.webhookId === wh.id ? (
              <div>
                {editError && <div className="dash-error">{editError}</div>}
                <FormField label="URL" required>
                  <input
                    className="dash-form-input"
                    type="url"
                    value={editState.url}
                    onChange={(e) => setEditState({ ...editState, url: e.target.value })}
                  />
                </FormField>
                <FormField label="Events" required>
                  <CheckboxGroup
                    options={EVENT_OPTIONS}
                    selected={editState.events}
                    onChange={(events) => setEditState({ ...editState, events })}
                    columns={3}
                  />
                </FormField>
                <FormField label="Description">
                  <input
                    className="dash-form-input"
                    value={editState.description}
                    onChange={(e) => setEditState({ ...editState, description: e.target.value })}
                  />
                </FormField>
                <div className="dash-form-group">
                  <label className="dash-checkbox-item">
                    <input
                      type="checkbox"
                      checked={editState.is_active}
                      onChange={(e) => setEditState({ ...editState, is_active: e.target.checked })}
                    />
                    <span>Active</span>
                  </label>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button className="dash-btn dash-btn-primary" onClick={handleSaveEdit} disabled={saving}>
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                  <button className="dash-btn dash-btn-secondary" onClick={() => setEditState(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.5rem' }}>
                  <div>
                    <div style={{ fontFamily: 'monospace', fontSize: '0.9rem', wordBreak: 'break-all' }}>
                      {wh.url.length > 50 ? `${wh.url.slice(0, 50)}...` : wh.url}
                    </div>
                    <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem', alignItems: 'center' }}>
                      <StatusBadge status={wh.is_active ? 'active' : 'suspended'} />
                      <span style={{ fontSize: '0.8rem', opacity: 0.7 }}>
                        {wh.events.length} event{wh.events.length !== 1 ? 's' : ''}
                      </span>
                      {wh.description && (
                        <span style={{ fontSize: '0.8rem', opacity: 0.7 }}>{wh.description}</span>
                      )}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <button
                      className="dash-btn dash-btn-sm dash-btn-secondary"
                      onClick={() => handleToggleActive(wh)}
                    >
                      {wh.is_active ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      className="dash-btn dash-btn-sm"
                      onClick={() => startEdit(wh)}
                    >
                      Edit
                    </button>
                    <button
                      className="dash-btn dash-btn-sm"
                      onClick={() => handleTest(wh.id)}
                    >
                      Test
                    </button>
                    <button
                      className="dash-btn dash-btn-sm"
                      onClick={() => handleToggleDeliveries(wh.id)}
                    >
                      {deliveries?.webhookId === wh.id ? 'Hide Log' : 'Delivery Log'}
                    </button>
                    <button
                      className="dash-btn dash-btn-sm dash-btn-danger"
                      onClick={() => setConfirmDelete(wh.id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {testResult[wh.id] && (
                  <div
                    className={testResult[wh.id].startsWith('Error') ? 'dash-error' : 'dash-success'}
                    style={{ marginTop: '0.5rem', fontSize: '0.85rem' }}
                  >
                    Test result: {testResult[wh.id]}
                  </div>
                )}

                {deliveries?.webhookId === wh.id && (
                  <div style={{ marginTop: '1rem' }}>
                    {deliveries.loading ? (
                      <div className="dash-loading">Loading deliveries...</div>
                    ) : deliveries.deliveries && deliveries.deliveries.length > 0 ? (
                      <DataTable
                        columns={deliveryColumns}
                        data={deliveries.deliveries as unknown as Record<string, unknown>[]}
                        keyField="id"
                        emptyMessage="No deliveries"
                      />
                    ) : (
                      <p style={{ opacity: 0.6, fontSize: '0.875rem' }}>No delivery records found.</p>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        ))
      )}

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete Webhook"
        message="Delete this webhook endpoint? This cannot be undone."
        confirmLabel="Delete"
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
