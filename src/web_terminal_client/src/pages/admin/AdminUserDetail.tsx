import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import {
  listAllUsers, suspendUser, unsuspendUser,
  changeAdminUserPassword, deleteAdminUser,
  getUserFeatures, updateUserFeatures,
} from '../../adminApi';
import { StatusBadge, ConfirmDialog, FormField, ReadonlyField, ImpactConfirmDialog, TabbedDetail } from '../../components/dashboard';
import type { Tab } from '../../components/dashboard';
import type { AdminUser } from '../../types/admin';
import { AdminSSHProfilesTab } from './AdminSSHProfilesTab';

const TABS: Tab[] = [
  { id: 'details', label: 'Details' },
  { id: 'ssh', label: 'SSH Profiles' },
];

export function AdminUserDetail() {
  const { userId } = useParams<{ userId: string }>();
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const initialUser = (location.state as { user?: AdminUser } | null)?.user;
  const [user, setUser] = useState<AdminUser | null>(initialUser ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!initialUser);

  const [activeTab, setActiveTab] = useState<'details' | 'ssh'>('details');

  const [confirmSuspend, setConfirmSuspend] = useState<'suspend' | 'unsuspend' | null>(null);
  const [showDelete, setShowDelete] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [newPassword, setNewPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordSuccess, setPasswordSuccess] = useState(false);

  // Feature flags
  const [sshEnabled, setSshEnabled] = useState<boolean | null>(null);
  const [featureToggling, setFeatureToggling] = useState(false);
  const [confirmSshToggle, setConfirmSshToggle] = useState<boolean | null>(null);

  const refetch = useCallback(() => {
    if (!token || !baseUrl || !userId) return;
    setLoading(true);
    listAllUsers(baseUrl, token, { search: userId })
      .then((res) => {
        const found = res.users.find((u) => u.id === userId) ?? null;
        setUser(found);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [token, baseUrl, userId]);

  const fetchFeatures = useCallback(() => {
    if (!token || !baseUrl || !userId) return;
    getUserFeatures(baseUrl, token, userId)
      .then((res) => {
        setSshEnabled(Boolean(res.effective.ssh_enabled));
      })
      .catch(() => setSshEnabled(null));
  }, [token, baseUrl, userId]);

  useEffect(() => {
    if (!initialUser) refetch();
    fetchFeatures();
  }, [refetch, fetchFeatures]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleToggleSshConfirmed = async () => {
    if (!token || !baseUrl || !userId || confirmSshToggle === null) return;
    setFeatureToggling(true);
    setActionError(null);
    try {
      const res = await updateUserFeatures(baseUrl, token, userId, {
        ssh_enabled: confirmSshToggle,
      });
      setSshEnabled(Boolean(res.effective.ssh_enabled));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setFeatureToggling(false);
      setConfirmSshToggle(null);
    }
  };

  const handleToggleSuspend = async () => {
    if (!token || !baseUrl || !userId) return;
    setActionError(null);
    try {
      if (user?.is_active) {
        await suspendUser(baseUrl, token, userId);
      } else {
        await unsuspendUser(baseUrl, token, userId);
      }
      refetch();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
    setConfirmSuspend(null);
  };

  const handleDelete = async () => {
    if (!token || !baseUrl || !userId) return;
    setActionError(null);
    try {
      await deleteAdminUser(baseUrl, token, userId);
      navigate('/admin/users');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
    setShowDelete(false);
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 8) { setPasswordError('Min 8 characters'); return; }
    if (!token || !baseUrl || !userId) return;
    setPasswordSaving(true);
    setPasswordError(null);
    setPasswordSuccess(false);
    try {
      await changeAdminUserPassword(baseUrl, token, userId, newPassword);
      setNewPassword('');
      setShowPasswordForm(false);
      setPasswordSuccess(true);
    } catch (e) {
      setPasswordError(e instanceof Error ? e.message : String(e));
    } finally {
      setPasswordSaving(false);
    }
  };

  if (loading) return <div className="dash-loading">Loading...</div>;
  if (error) return <div className="dash-error">{error}</div>;
  if (!user) return <div className="dash-error">User not found.</div>;

  return (
    <div>
      <div className="dash-page-header">
        <div>
          <h2 className="dash-page-title">
            {user.username}
            <StatusBadge status={user.is_active ? 'active' : 'suspended'} />
          </h2>
          <span style={{ fontSize: '0.8rem', opacity: 0.6 }}>
            {user.email}{user.reseller_name ? ` | ${user.reseller_name}` : ''}
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <button
            className={`dash-btn ${user.is_active ? 'dash-btn-danger' : 'dash-btn-primary'}`}
            onClick={() => setConfirmSuspend(user.is_active ? 'suspend' : 'unsuspend')}
          >
            {user.is_active ? 'Suspend' : 'Unsuspend'}
          </button>
          <button
            className="dash-btn dash-btn-secondary"
            onClick={() => { setShowPasswordForm(!showPasswordForm); setPasswordError(null); setPasswordSuccess(false); }}
          >
            Change Password
          </button>
          <button className="dash-btn dash-btn-danger" onClick={() => setShowDelete(true)}>
            Delete
          </button>
          <button className="dash-btn dash-btn-secondary" onClick={() => navigate('/admin/users')}>
            Back
          </button>
        </div>
      </div>

      {actionError && <div className="dash-error">{actionError}</div>}
      {passwordSuccess && <div className="dash-success">Password changed successfully.</div>}

      {showPasswordForm && (
        <div className="dash-section">
          <h3 className="dash-section-title">Change Password</h3>
          <form onSubmit={handleChangePassword} noValidate style={{ maxWidth: 360 }}>
            <FormField label="New Password" required error={passwordError ?? undefined}>
              <input
                className="dash-form-input"
                type="password"
                value={newPassword}
                onChange={(e) => { setNewPassword(e.target.value); setPasswordError(null); }}
                placeholder="Min 8 characters"
                autoFocus
              />
            </FormField>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button type="submit" className="dash-btn dash-btn-primary" disabled={passwordSaving}>
                {passwordSaving ? 'Saving...' : 'Set Password'}
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={() => { setShowPasswordForm(false); setNewPassword(''); setPasswordError(null); }}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <TabbedDetail tabs={TABS} activeTab={activeTab} onTabChange={(id) => setActiveTab(id as 'details' | 'ssh')}>
        {activeTab === 'details' && (
          <div>
            <div className="dash-form-grid">
              <ReadonlyField label="Username" value={user.username} />
              <ReadonlyField label="Email" value={user.email} />
              <ReadonlyField label="Role" value={user.role} />
              <ReadonlyField label="Reseller" value={user.reseller_name ?? '—'} />
              <div className="dash-form-group">
                <label className="dash-form-label">Status</label>
                <StatusBadge status={user.is_active ? 'active' : 'suspended'} />
              </div>
              <ReadonlyField label="Created" value={new Date(user.created_at).toLocaleString()} />
            </div>

            {/* Feature Flags */}
            <div className="dash-section" style={{ marginTop: 'var(--spacing-xl, 1.5rem)' }}>
              <h3 className="dash-section-title">Feature Access</h3>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-md, 0.75rem)', padding: 'var(--spacing-sm, 0.5rem) 0' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-sm, 0.5rem)', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={sshEnabled === true}
                    disabled={featureToggling || sshEnabled === null}
                    onChange={() => setConfirmSshToggle(!sshEnabled)}
                    style={{ width: 18, height: 18, cursor: 'pointer' }}
                  />
                  <span style={{ fontWeight: 500 }}>SSH Access</span>
                </label>
                {sshEnabled === null && <span style={{ fontSize: '0.8rem', opacity: 0.5 }}>loading...</span>}
                {sshEnabled !== null && (
                  <StatusBadge status={sshEnabled ? 'active' : 'suspended'} label={sshEnabled ? 'enabled' : 'disabled'} />
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'ssh' && <AdminSSHProfilesTab userId={user.id} />}
      </TabbedDetail>

      <ConfirmDialog
        open={confirmSuspend !== null}
        title={confirmSuspend === 'suspend' ? 'Suspend User' : 'Unsuspend User'}
        message={
          confirmSuspend === 'suspend'
            ? `This will suspend ${user.username}. Active sessions will be cancelled.`
            : `This will restore access for ${user.username}.`
        }
        confirmLabel={confirmSuspend === 'suspend' ? 'Suspend' : 'Unsuspend'}
        danger={confirmSuspend === 'suspend'}
        onConfirm={handleToggleSuspend}
        onCancel={() => setConfirmSuspend(null)}
      />

      <ConfirmDialog
        open={confirmSshToggle !== null}
        title={confirmSshToggle ? 'Enable SSH Access' : 'Disable SSH Access'}
        message={
          confirmSshToggle
            ? `Enable SSH access for ${user.username}? They will be able to create SSH connection profiles.`
            : `Disable SSH access for ${user.username}? Active SSH sessions will lose access within 30 seconds.`
        }
        confirmLabel={featureToggling ? 'Saving...' : (confirmSshToggle ? 'Enable' : 'Disable')}
        danger={!confirmSshToggle}
        onConfirm={handleToggleSshConfirmed}
        onCancel={() => setConfirmSshToggle(null)}
      />

      <ImpactConfirmDialog
        open={showDelete}
        title="Delete User"
        entityName={user.username}
        impact={[{ label: 'user will be permanently deleted', count: 1 }]}
        onConfirm={handleDelete}
        onCancel={() => setShowDelete(false)}
      />
    </div>
  );
}
