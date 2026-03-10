import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { adminListUserSSHProfiles, adminDeleteUserSSHProfile } from '../../adminApi';
import { DataTable, ConfirmDialog } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { SSHProfile } from '../../types/ssh';

interface Props {
  userId: string;
}

function sshStatus(profile: SSHProfile): { label: string; color: string } {
  if (profile.last_connection_error) {
    return { label: 'Error', color: 'var(--dash-danger, #e53e3e)' };
  }
  if (profile.last_connected_at) {
    return { label: 'Connected', color: 'var(--dash-success, #38a169)' };
  }
  return { label: 'Never tested', color: 'var(--dash-muted, #718096)' };
}

export function AdminSSHProfilesTab({ userId }: Props) {
  const { token, baseUrl } = useAuth();
  const [profiles, setProfiles] = useState<SSHProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SSHProfile | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const fetchProfiles = useCallback(() => {
    if (!token || !baseUrl) return;
    setLoading(true);
    setError(null);
    adminListUserSSHProfiles(baseUrl, token, userId)
      .then((res) => setProfiles(res.profiles))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [token, baseUrl, userId]);

  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);

  const handleDelete = async () => {
    if (!token || !baseUrl || !deleteTarget) return;
    setDeleteError(null);
    try {
      await adminDeleteUserSSHProfile(baseUrl, token, userId, deleteTarget.id);
      setDeleteTarget(null);
      fetchProfiles();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : String(e));
      setDeleteTarget(null);
    }
  };

  const columns: Column<Record<string, unknown>>[] = [
    { key: 'name', header: 'Name', sortable: true },
    {
      key: 'host',
      header: 'Host:Port',
      render: (row) => {
        const p = row as unknown as SSHProfile;
        return `${p.host}:${p.port}`;
      },
    },
    { key: 'username', header: 'Username', sortable: true },
    { key: 'mode', header: 'Mode', sortable: true },
    {
      key: 'privilege_level',
      header: 'Privilege Level',
      render: (row) => String((row as unknown as SSHProfile).privilege_level),
    },
    {
      key: 'key_type',
      header: 'Key Type',
      render: (row) => {
        const p = row as unknown as SSHProfile;
        if (!p.key_type) return '—';
        const fp = p.key_fingerprint;
        return (
          <span>
            {p.key_type}
            {fp && (
              <>
                {' '}
                <code style={{ fontSize: '0.75em', opacity: 0.75 }}>{fp}</code>
              </>
            )}
          </span>
        );
      },
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => {
        const { label, color } = sshStatus(row as unknown as SSHProfile);
        return (
          <span style={{ color, fontWeight: 500 }}>{label}</span>
        );
      },
    },
    {
      key: 'actions',
      header: 'Actions',
      render: (row) => {
        const p = row as unknown as SSHProfile;
        return (
          <button
            className="dash-btn dash-btn-danger"
            style={{ padding: '0.2rem 0.6rem', fontSize: '0.8rem' }}
            onClick={(e) => { e.stopPropagation(); setDeleteTarget(p); }}
          >
            Delete
          </button>
        );
      },
    },
  ];

  if (loading) return <div className="dash-loading">Loading SSH profiles...</div>;
  if (error) return <div className="dash-error">{error}</div>;

  return (
    <div>
      {deleteError && <div className="dash-error">{deleteError}</div>}

      <DataTable
        columns={columns}
        data={profiles as unknown as Record<string, unknown>[]}
        keyField="id"
        emptyMessage="No SSH profiles for this user."
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete SSH Profile"
        message={
          deleteTarget
            ? `Delete profile "${deleteTarget.name}" (${deleteTarget.host}:${deleteTarget.port})? This cannot be undone.`
            : ''
        }
        confirmLabel="Delete"
        danger
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
