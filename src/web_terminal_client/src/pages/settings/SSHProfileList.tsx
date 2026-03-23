import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import {
  listSSHProfiles,
  deleteSSHProfile,
  testSavedSSHConnection,
  createSSHProfile,
  updateSSHProfile,
} from '../../sshApi';
import { DataTable, ConfirmDialog, StatusBadge } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import { SSHProfileForm } from './SSHProfileForm';
import { ConnectionTestResult } from './ConnectionTestResult';
import { SSH_ACCESS_LEVELS } from '../../types/ssh';
import type { SSHProfile, CreateSSHProfileRequest, UpdateSSHProfileRequest, TestSSHConnectionResponse } from '../../types/ssh';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function profileStatus(profile: SSHProfile): string {
  if (!profile.is_active) return 'suspended';
  if (profile.last_connection_error) return 'failed';
  if (profile.last_connected_at) return 'active';
  return 'pending';
}

function profileStatusLabel(profile: SSHProfile): string {
  if (!profile.is_active) return 'inactive';
  if (profile.last_connection_error) return 'error';
  if (profile.last_connected_at) return 'tested';
  return 'never used';
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SSHProfileList() {
  const { token, baseUrl } = useAuth();

  const [profiles, setProfiles] = useState<SSHProfile[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [showForm, setShowForm] = useState(false);
  const [editingProfile, setEditingProfile] = useState<SSHProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<SSHProfile | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestSSHConnectionResponse>>({});

  const fetchProfiles = useCallback(() => {
    if (!token || !baseUrl) return;
    setLoading(true);
    listSSHProfiles(baseUrl, token)
      .then((res) => setProfiles(res.profiles))
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, [token, baseUrl]);

  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);

  const handleSave = async (data: CreateSSHProfileRequest | UpdateSSHProfileRequest) => {
    if (!token || !baseUrl) return;
    setSaving(true);
    setSaveError(null);
    try {
      if (editingProfile) {
        await updateSSHProfile(baseUrl, token, editingProfile.id, data as UpdateSSHProfileRequest);
      } else {
        await createSSHProfile(baseUrl, token, data as CreateSSHProfileRequest);
      }
      setShowForm(false);
      setEditingProfile(null);
      fetchProfiles();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!token || !baseUrl || !deleteTarget) return;
    setDeleting(true);
    try {
      await deleteSSHProfile(baseUrl, token, deleteTarget.id);
      setDeleteTarget(null);
      fetchProfiles();
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  };

  const handleTest = async (profile: SSHProfile) => {
    if (!token || !baseUrl) return;
    setTestingId(profile.id);
    try {
      const result = await testSavedSSHConnection(baseUrl, token, profile.id);
      setTestResults((prev) => ({ ...prev, [profile.id]: result }));
    } catch (e) {
      setTestResults((prev) => ({
        ...prev,
        [profile.id]: {
          status: 'failed',
          message: e instanceof Error ? e.message : String(e),
        },
      }));
    } finally {
      setTestingId(null);
    }
  };

  const columns: Column<Record<string, unknown>>[] = [
    { key: 'name', header: 'Name', sortable: true },
    {
      key: 'host',
      header: 'Host:Port',
      render: (r) => `${String(r.host)}:${String(r.port)}`,
    },
    { key: 'username', header: 'Username', sortable: true },
    {
      key: 'privilege_level',
      header: 'Access',
      render: (r) => {
        const level = Number(r.privilege_level);
        const match = SSH_ACCESS_LEVELS.find(l => l.value === level);
        return <span className="badge">{match?.shortLabel ?? `P${level}`}</span>;
      },
    },
    {
      key: 'is_active',
      header: 'Status',
      sortable: true,
      render: (r) => {
        const profile = r as unknown as SSHProfile;
        return (
          <StatusBadge
            status={profileStatus(profile)}
            label={profileStatusLabel(profile)}
          />
        );
      },
    },
    {
      key: 'actions',
      header: '',
      render: (r) => {
        const profile = r as unknown as SSHProfile;
        const isTesting = testingId === profile.id;
        return (
          <div style={{ display: 'flex', gap: 'var(--spacing-sm)', justifyContent: 'flex-end' }}>
            <button
              className="dash-btn dash-btn-sm dash-btn-secondary"
              onClick={(e) => { e.stopPropagation(); handleTest(profile); }}
              disabled={isTesting}
            >
              {isTesting ? 'Testing...' : 'Test'}
            </button>
            <button
              className="dash-btn dash-btn-sm dash-btn-secondary"
              onClick={(e) => {
                e.stopPropagation();
                setEditingProfile(profile);
                setShowForm(true);
                setSaveError(null);
              }}
            >
              Edit
            </button>
            <button
              className="dash-btn dash-btn-sm dash-btn-danger"
              onClick={(e) => { e.stopPropagation(); setDeleteTarget(profile); }}
            >
              Delete
            </button>
          </div>
        );
      },
    },
  ];

  if (loading) return <div className="dash-loading">Loading SSH profiles...</div>;
  if (loadError) return <div className="dash-error">{loadError}</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">SSH Connections</h2>
        <button
          className="dash-btn dash-btn-primary"
          onClick={() => {
            setEditingProfile(null);
            setShowForm(!showForm);
            setSaveError(null);
          }}
        >
          {showForm && !editingProfile ? 'Cancel' : '+ Add Profile'}
        </button>
      </div>

      {/* Inline test results */}
      {Object.entries(testResults).map(([id, result]) => {
        const profile = profiles.find((p) => p.id === id);
        if (!profile) return null;
        return (
          <div key={id} style={{ marginBottom: 'var(--spacing-xl)' }}>
            <div style={{ fontSize: 'var(--font-xs)', color: 'var(--color-text-muted)', marginBottom: 'var(--spacing-xs)' }}>
              Test result for <strong>{profile.name}</strong>
            </div>
            <ConnectionTestResult result={result} loading={false} />
          </div>
        );
      })}

      {/* Add / Edit form */}
      {showForm && (
        <div className="dash-section">
          <h3 className="dash-section-title">
            {editingProfile ? `Edit: ${editingProfile.name}` : 'New SSH Profile'}
          </h3>
          <SSHProfileForm
            profile={editingProfile ?? undefined}
            onSave={handleSave}
            onCancel={() => {
              setShowForm(false);
              setEditingProfile(null);
              setSaveError(null);
            }}
            saving={saving}
            saveError={saveError}
          />
        </div>
      )}

      {/* Profiles table */}
      {profiles.length === 0 && !showForm ? (
        <div className="dash-table-wrap">
          <div className="dash-table-empty" style={{ padding: 'var(--spacing-4xl)', textAlign: 'center' }}>
            No SSH profiles yet. Add one to connect your agent to remote servers.
          </div>
        </div>
      ) : (
        profiles.length > 0 && (
          <DataTable
            columns={columns}
            data={profiles as unknown as Record<string, unknown>[]}
            keyField="id"
            emptyMessage="No SSH profiles found"
          />
        )
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete SSH Profile"
        message={`Delete profile "${deleteTarget?.name}"? This cannot be undone.`}
        confirmLabel={deleting ? 'Deleting...' : 'Delete'}
        danger
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
