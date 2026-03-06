import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../../AuthContext';
import {
  getResellerUser,
  suspendResellerUser,
  unsuspendResellerUser,
  changeResellerUserPassword,
  deleteResellerUser,
  getUserSpending,
  setUserSpendingLimits,
  setUserSettingsMode,
  getUserSecurity,
  updateUserSecurity,
  getUserSSHFilters,
  updateUserSSHFilters,
  getUserEnvVars,
  setUserEnvVars,
  deleteUserEnvVar,
  getUserSkills,
  assignUserSkill,
  removeUserSkill,
  enableUserSkill,
  disableUserSkill,
  getSkillLibrary,
} from '../../adminApi';
import {
  StatusBadge,
  StatsCard,
  TabbedDetail,
  ConfirmDialog,
  ImpactConfirmDialog,
  SpendingBar,
  TagInput,
  FormField,
} from '../../components/dashboard';
import type { Tab } from '../../components/dashboard';
import type {
  ResellerUser,
  SettingsMode,
  SpendingStatus,
  SecurityConfig,
  SSHFilters,
  SkillInfo,
} from '../../types/admin';

// ---------------------------------------------------------------------------
// Overview tab
// ---------------------------------------------------------------------------

function useSavedFlash(delay = 2000): [boolean, () => void] {
  const [saved, setSaved] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  const flash = useCallback(() => {
    setSaved(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setSaved(false), delay);
  }, [delay]);
  return [saved, flash];
}

interface OverviewTabProps {
  user: ResellerUser;
  onRefresh: () => void;
}

function OverviewTab({ user, onRefresh }: OverviewTabProps) {
  const { token: t, baseUrl: b } = useAuth();
  const token = t!;
  const baseUrl = b!;
  const navigate = useNavigate();
  const [confirmSuspend, setConfirmSuspend] = useState(false);
  const [confirmUnsuspend, setConfirmUnsuspend] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [newPassword, setNewPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleSuspend = async () => {
    try {
      await suspendResellerUser(baseUrl, token, user.id);
      setConfirmSuspend(false);
      onRefresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleUnsuspend = async () => {
    try {
      await unsuspendResellerUser(baseUrl, token, user.id);
      setConfirmUnsuspend(false);
      onRefresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleChangePassword = async () => {
    if (newPassword.length < 8) {
      setPasswordError('Password must be at least 8 characters');
      return;
    }
    try {
      await changeResellerUserPassword(baseUrl, token, user.id, newPassword);
      setShowChangePassword(false);
      setNewPassword('');
      setPasswordError(null);
    } catch (e) {
      setPasswordError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleDelete = async () => {
    try {
      await deleteResellerUser(baseUrl, token, user.id);
      navigate('/reseller/users');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div>
      {actionError && <div className="dash-error">{actionError}</div>}

      <div className="dash-stats-row">
        <StatsCard label="Email" value={user.email} />
        <StatsCard
          label="Total Sessions"
          value={user.sessions_total ?? 0}
          accent="blue"
        />
        <StatsCard
          label="Total Cost"
          value={`$${(user.usage_summary?.total_cost_usd ?? 0).toFixed(2)}`}
          accent="yellow"
        />
        <StatsCard
          label="Last Session"
          value={user.last_session_at ? new Date(user.last_session_at).toLocaleDateString() : 'Never'}
        />
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Account Info</h3>
        <div className="dash-form-group">
          <label className="dash-form-label">Created</label>
          <div>{new Date(user.created_at).toLocaleString()}</div>
        </div>
        <div className="dash-form-group">
          <label className="dash-form-label">Status</label>
          <div><StatusBadge status={user.is_active ? 'active' : 'suspended'} /></div>
        </div>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Actions</h3>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {user.is_active ? (
            <button className="dash-btn dash-btn-secondary" onClick={() => setConfirmSuspend(true)}>
              Suspend User
            </button>
          ) : (
            <button className="dash-btn dash-btn-primary" onClick={() => setConfirmUnsuspend(true)}>
              Unsuspend User
            </button>
          )}
          <button
            className="dash-btn dash-btn-secondary"
            onClick={() => { setShowChangePassword(!showChangePassword); setPasswordError(null); setNewPassword(''); }}
          >
            Change Password
          </button>
          <button className="dash-btn dash-btn-danger" onClick={() => setConfirmDelete(true)}>
            Delete User
          </button>
        </div>

        {showChangePassword && (
          <div style={{ marginTop: '1rem', maxWidth: 400 }}>
            <FormField label="New Password" error={passwordError ?? undefined} required>
              <input
                className="dash-form-input"
                type="password"
                value={newPassword}
                onChange={(e) => { setNewPassword(e.target.value); setPasswordError(null); }}
                placeholder="Min. 8 characters"
              />
            </FormField>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="dash-btn dash-btn-primary" onClick={handleChangePassword}>Save</button>
              <button
                className="dash-btn dash-btn-secondary"
                onClick={() => { setShowChangePassword(false); setNewPassword(''); setPasswordError(null); }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmSuspend}
        title="Suspend User"
        message={`Suspend ${user.username}? Active sessions will be cancelled.`}
        confirmLabel="Suspend"
        danger
        onConfirm={handleSuspend}
        onCancel={() => setConfirmSuspend(false)}
      />

      <ConfirmDialog
        open={confirmUnsuspend}
        title="Unsuspend User"
        message={`Restore access for ${user.username}?`}
        confirmLabel="Unsuspend"
        onConfirm={handleUnsuspend}
        onCancel={() => setConfirmUnsuspend(false)}
      />

      <ImpactConfirmDialog
        open={confirmDelete}
        title={`Delete ${user.username}`}
        entityName={user.username}
        impact={[
          { label: 'sessions deleted', count: user.sessions_total ?? 0 },
        ]}
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spending tab
// ---------------------------------------------------------------------------

interface SpendingTabProps {
  userId: string;
  initialSettingsMode?: SettingsMode;
}

function SpendingTab({ userId, initialSettingsMode }: SpendingTabProps) {
  const { token: t, baseUrl: b } = useAuth();
  const token = t!;
  const baseUrl = b!;
  const [spending, setSpending] = useState<SpendingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [monthly, setMonthly] = useState('');
  const [daily, setDaily] = useState('');
  const [perSession, setPerSession] = useState('');
  const [settingsMode, setSettingsMode] = useState<SettingsMode>(initialSettingsMode ?? 'readonly');
  const [saving, setSaving] = useState(false);
  const [saved, flashSaved] = useSavedFlash();

  useEffect(() => {
    getUserSpending(baseUrl, token, userId)
      .then((s) => {
        setSpending(s);
        setMonthly(s.limits.monthly_usd != null ? String(s.limits.monthly_usd) : '');
        setDaily(s.limits.daily_usd != null ? String(s.limits.daily_usd) : '');
        setPerSession(s.limits.per_session_usd != null ? String(s.limits.per_session_usd) : '');
      })
      .catch((e: Error) => setError(e.message));
  }, [userId, token, baseUrl]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const [spendingResult] = await Promise.all([
        setUserSpendingLimits(baseUrl, token, userId, {
          max_monthly_usd: monthly ? parseFloat(monthly) : null,
          max_daily_usd: daily ? parseFloat(daily) : null,
          max_per_session_usd: perSession ? parseFloat(perSession) : null,
        }),
        setUserSettingsMode(baseUrl, token, userId, { mode: settingsMode }),
      ]);
      flashSaved();
      setSpending(spendingResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (error) return <div className="dash-error">{error}</div>;
  if (!spending) return <div className="dash-loading">Loading spending...</div>;

  return (
    <div>
      <div className="dash-section">
        <h3 className="dash-section-title">Current Usage</h3>
        <SpendingBar
          current={spending.current.monthly_usd}
          limit={spending.limits.monthly_usd}
          label="Monthly"
        />
        <SpendingBar
          current={spending.current.daily_usd}
          limit={spending.limits.daily_usd}
          label="Daily"
        />
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Spending Limits</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem' }}>
          <FormField label="Monthly Limit (USD)" hint="Leave blank to inherit">
            <input
              className="dash-form-input"
              type="number"
              min="0"
              step="0.01"
              value={monthly}
              onChange={(e) => setMonthly(e.target.value)}
              placeholder="Inherit"
            />
          </FormField>
          <FormField label="Daily Limit (USD)" hint="Leave blank to inherit">
            <input
              className="dash-form-input"
              type="number"
              min="0"
              step="0.01"
              value={daily}
              onChange={(e) => setDaily(e.target.value)}
              placeholder="Inherit"
            />
          </FormField>
          <FormField label="Per Session Limit (USD)" hint="Leave blank to inherit">
            <input
              className="dash-form-input"
              type="number"
              min="0"
              step="0.01"
              value={perSession}
              onChange={(e) => setPerSession(e.target.value)}
              placeholder="Inherit"
            />
          </FormField>
        </div>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Settings Mode</h3>
        <FormField label="Mode" hint="Controls whether the user can override settings">
          <select
            className="dash-form-input"
            value={settingsMode}
            onChange={(e) => setSettingsMode(e.target.value as SettingsMode)}
          >
            <option value="readonly">Readonly (no overrides)</option>
            <option value="configurable">Configurable (user can override)</option>
          </select>
        </FormField>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button className="dash-btn dash-btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
        {saved && <span className="dash-success">Saved</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Security tab
// ---------------------------------------------------------------------------

interface SecurityTabProps {
  userId: string;
}

function SecurityTab({ userId }: SecurityTabProps) {
  const { token: t, baseUrl: b } = useAuth();
  const token = t!;
  const baseUrl = b!;
  const [config, setConfig] = useState<SecurityConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, flashSaved] = useSavedFlash();

  useEffect(() => {
    getUserSecurity(baseUrl, token, userId)
      .then(setConfig)
      .catch((e: Error) => setError(e.message));
  }, [userId, token, baseUrl]);

  const update = (patch: Partial<SecurityConfig>) => setConfig((c) => c ? { ...c, ...patch } : c);

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const updated = await updateUserSecurity(baseUrl, token, userId, config);
      setConfig(updated);
      flashSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (error) return <div className="dash-error">{error}</div>;
  if (!config) return <div className="dash-loading">Loading security config...</div>;

  return (
    <div>
      <div className="dash-section">
        <h3 className="dash-section-title">Tool Restrictions</h3>
        <FormField label="Allowed Tools" hint="If set, only these tools are permitted">
          <TagInput
            tags={config.allowed_tools ?? []}
            onChange={(tags) => update({ allowed_tools: tags })}
            placeholder="Add tool name..."
          />
        </FormField>
        <FormField label="Disabled Tools" hint="These tools are always blocked">
          <TagInput
            tags={config.disabled_tools ?? []}
            onChange={(tags) => update({ disabled_tools: tags })}
            placeholder="Add tool name..."
          />
        </FormField>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Command Restrictions</h3>
        <FormField label="Block Patterns" hint="Regex patterns for blocked commands">
          <TagInput
            tags={config.command_block_patterns ?? []}
            onChange={(tags) => update({ command_block_patterns: tags })}
            placeholder="Add pattern..."
          />
        </FormField>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Network Restrictions</h3>
        <FormField label="Allowed Domains">
          <TagInput
            tags={config.network_allowed_domains ?? []}
            onChange={(tags) => update({ network_allowed_domains: tags })}
            placeholder="example.com"
          />
        </FormField>
        <FormField label="Blocked Domains">
          <TagInput
            tags={config.network_blocked_domains ?? []}
            onChange={(tags) => update({ network_blocked_domains: tags })}
            placeholder="blocked.com"
          />
        </FormField>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Path Restrictions</h3>
        <FormField label="Additional Blocklist Paths">
          <TagInput
            tags={config.path_blocklist_additions ?? []}
            onChange={(tags) => update({ path_blocklist_additions: tags })}
            placeholder="/path/to/block"
          />
        </FormField>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button className="dash-btn dash-btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
        {saved && <span className="dash-success">Saved</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SSH Filters tab
// ---------------------------------------------------------------------------

interface SSHFiltersTabProps {
  userId: string;
}

function SSHFiltersTab({ userId }: SSHFiltersTabProps) {
  const { token: t, baseUrl: b } = useAuth();
  const token = t!;
  const baseUrl = b!;
  const [filters, setFilters] = useState<SSHFilters | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, flashSaved] = useSavedFlash();

  useEffect(() => {
    getUserSSHFilters(baseUrl, token, userId)
      .then(setFilters)
      .catch((e: Error) => setError(e.message));
  }, [userId, token, baseUrl]);

  const update = (patch: Partial<SSHFilters>) => setFilters((f) => f ? { ...f, ...patch } : f);

  const handleSave = async () => {
    if (!filters) return;
    setSaving(true);
    try {
      const updated = await updateUserSSHFilters(baseUrl, token, userId, filters);
      setFilters(updated);
      flashSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (error) return <div className="dash-error">{error}</div>;
  if (!filters) return <div className="dash-loading">Loading SSH filters...</div>;

  return (
    <div>
      <div className="dash-section">
        <h3 className="dash-section-title">Host Restrictions</h3>
        <FormField label="Blocked Hosts">
          <TagInput
            tags={filters.blocked_hosts ?? []}
            onChange={(tags) => update({ blocked_hosts: tags })}
            placeholder="blocked.host.com"
          />
        </FormField>
        <FormField label="Allowed Hosts" hint="If set, only these hosts are permitted">
          <TagInput
            tags={filters.allowed_hosts ?? []}
            onChange={(tags) => update({ allowed_hosts: tags })}
            placeholder="allowed.host.com"
          />
        </FormField>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Command Restrictions</h3>
        <FormField label="Block Patterns">
          <TagInput
            tags={filters.command_block_patterns ?? []}
            onChange={(tags) => update({ command_block_patterns: tags })}
            placeholder="Add pattern..."
          />
        </FormField>
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Connection Limits</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
          <FormField label="Max Connections" hint="1-20">
            <input
              className="dash-form-input"
              type="number"
              min="1"
              max="20"
              value={filters.max_connections ?? ''}
              onChange={(e) => update({ max_connections: e.target.value ? parseInt(e.target.value, 10) : undefined })}
            />
          </FormField>
          <FormField label="Session Timeout (seconds)" hint="60-86400">
            <input
              className="dash-form-input"
              type="number"
              min="60"
              max="86400"
              value={filters.session_timeout_seconds ?? ''}
              onChange={(e) => update({ session_timeout_seconds: e.target.value ? parseInt(e.target.value, 10) : undefined })}
            />
          </FormField>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button className="dash-btn dash-btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </button>
        {saved && <span className="dash-success">Saved</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Env Vars tab
// ---------------------------------------------------------------------------

interface EnvVarsTabProps {
  userId: string;
}

function EnvVarsTab({ userId }: EnvVarsTabProps) {
  const { token: t, baseUrl: b } = useAuth();
  const token = t!;
  const baseUrl = b!;
  const [envVars, setEnvVars] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [addError, setAddError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchEnvVars = useCallback(() => {
    getUserEnvVars(baseUrl, token, userId)
      .then((r) => setEnvVars(r.env_vars ?? {}))
      .catch((e: Error) => setError(e.message));
  }, [userId, token, baseUrl]);

  useEffect(() => { fetchEnvVars(); }, [fetchEnvVars]);

  const handleAdd = async () => {
    const key = newKey.trim();
    const value = newValue.trim();
    if (!key) { setAddError('Key is required'); return; }
    setSaving(true);
    setAddError(null);
    try {
      const updated = { ...envVars, [key]: value };
      await setUserEnvVars(baseUrl, token, userId, updated);
      setEnvVars(updated);
      setNewKey('');
      setNewValue('');
    } catch (e) {
      setAddError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (key: string) => {
    try {
      await deleteUserEnvVar(baseUrl, token, userId, key);
      setEnvVars((prev) => { const next = { ...prev }; delete next[key]; return next; });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (error) return <div className="dash-error">{error}</div>;

  const entries = Object.entries(envVars);

  return (
    <div>
      <div className="dash-section">
        <h3 className="dash-section-title">Environment Variables</h3>
        {entries.length === 0 ? (
          <p style={{ opacity: 0.6, fontSize: '0.875rem' }}>No environment variables set.</p>
        ) : (
          <div className="dash-table-wrap">
            <table className="dash-table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Value</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([key, value]) => (
                  <tr key={key}>
                    <td><code>{key}</code></td>
                    <td><code>{value}</code></td>
                    <td>
                      <button
                        className="dash-btn dash-btn-sm dash-btn-danger"
                        onClick={() => handleDelete(key)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">Add Variable</h3>
        {addError && <div className="dash-error">{addError}</div>}
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
          <FormField label="Key">
            <input
              className="dash-form-input"
              value={newKey}
              onChange={(e) => { setNewKey(e.target.value); setAddError(null); }}
              placeholder="VARIABLE_NAME"
            />
          </FormField>
          <FormField label="Value">
            <input
              className="dash-form-input"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder="value"
            />
          </FormField>
          <div className="dash-form-group">
            <button
              className="dash-btn dash-btn-primary"
              onClick={handleAdd}
              disabled={saving}
            >
              {saving ? 'Adding...' : 'Add'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skills tab
// ---------------------------------------------------------------------------

interface SkillsTabProps {
  userId: string;
}

function SkillsTab({ userId }: SkillsTabProps) {
  const { token: t, baseUrl: b } = useAuth();
  const token = t!;
  const baseUrl = b!;
  const [library, setLibrary] = useState<SkillInfo[]>([]);
  const [userSkills, setUserSkills] = useState<SkillInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchUserSkills = useCallback(() => {
    getUserSkills(baseUrl, token, userId)
      .then((usr) => setUserSkills(usr.skills))
      .catch((e: Error) => setError(e.message));
  }, [userId, token, baseUrl]);

  useEffect(() => {
    Promise.all([
      getSkillLibrary(baseUrl, token),
      getUserSkills(baseUrl, token, userId),
    ])
      .then(([lib, usr]) => {
        setLibrary(lib.skills);
        setUserSkills(usr.skills);
      })
      .catch((e: Error) => setError(e.message));
  }, [userId, token, baseUrl]);

  const handleAssign = async (skill: SkillInfo) => {
    try {
      await assignUserSkill(baseUrl, token, userId, { name: skill.name, source: skill.source });
      fetchUserSkills();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleRemove = async (skillName: string) => {
    try {
      await removeUserSkill(baseUrl, token, userId, skillName);
      fetchUserSkills();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleToggleEnable = async (skill: SkillInfo) => {
    try {
      if (skill.is_enabled) {
        await disableUserSkill(baseUrl, token, userId, skill.name);
      } else {
        await enableUserSkill(baseUrl, token, userId, skill.name);
      }
      fetchUserSkills();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (error) return <div className="dash-error">{error}</div>;

  const assignedNames = new Set(userSkills.map((s) => s.name));
  const availableInLibrary = library.filter((s) => !assignedNames.has(s.name));

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
      <div className="dash-section">
        <h3 className="dash-section-title">Skill Library</h3>
        {availableInLibrary.length === 0 ? (
          <p style={{ opacity: 0.6, fontSize: '0.875rem' }}>All library skills are assigned.</p>
        ) : (
          <div className="dash-table-wrap">
            <table className="dash-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Source</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {availableInLibrary.map((skill) => (
                  <tr key={skill.name}>
                    <td>{skill.name}</td>
                    <td style={{ fontSize: '0.8rem', opacity: 0.7 }}>{skill.source}</td>
                    <td>
                      <button
                        className="dash-btn dash-btn-sm dash-btn-primary"
                        onClick={() => handleAssign(skill)}
                      >
                        Assign
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="dash-section">
        <h3 className="dash-section-title">User&apos;s Skills</h3>
        {userSkills.length === 0 ? (
          <p style={{ opacity: 0.6, fontSize: '0.875rem' }}>No skills assigned.</p>
        ) : (
          <div className="dash-table-wrap">
            <table className="dash-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {userSkills.map((skill) => (
                  <tr key={skill.name}>
                    <td>{skill.name}</td>
                    <td>
                      <StatusBadge status={skill.is_enabled ? 'active' : 'suspended'} />
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: '0.25rem' }}>
                        <button
                          className="dash-btn dash-btn-sm dash-btn-secondary"
                          onClick={() => handleToggleEnable(skill)}
                        >
                          {skill.is_enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button
                          className="dash-btn dash-btn-sm dash-btn-danger"
                          onClick={() => handleRemove(skill.name)}
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main UserDetail component
// ---------------------------------------------------------------------------

const TABS: Tab[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'spending', label: 'Spending' },
  { id: 'security', label: 'Security' },
  { id: 'ssh', label: 'SSH Filters' },
  { id: 'env', label: 'Env Vars' },
  { id: 'skills', label: 'Skills' },
];

export function UserDetail() {
  const { userId } = useParams<{ userId: string }>();
  const { token, baseUrl } = useAuth();
  const navigate = useNavigate();
  const [user, setUser] = useState<ResellerUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('overview');

  const fetchUser = useCallback(() => {
    if (!token || !baseUrl || !userId) return;
    getResellerUser(baseUrl, token, userId)
      .then((data) => setUser(data))
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl, userId]);

  useEffect(() => { fetchUser(); }, [fetchUser]);

  if (error) return <div className="dash-error">{error}</div>;
  if (!user || !userId || !token || !baseUrl) return <div className="dash-loading">Loading user...</div>;

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

      <TabbedDetail tabs={TABS} activeTab={activeTab} onTabChange={setActiveTab}>
        {activeTab === 'overview' && (
          <OverviewTab user={user} onRefresh={fetchUser} />
        )}
        {activeTab === 'spending' && (
          <SpendingTab userId={userId} initialSettingsMode={user.settings_mode} />
        )}
        {activeTab === 'security' && (
          <SecurityTab userId={userId} />
        )}
        {activeTab === 'ssh' && (
          <SSHFiltersTab userId={userId} />
        )}
        {activeTab === 'env' && (
          <EnvVarsTab userId={userId} />
        )}
        {activeTab === 'skills' && (
          <SkillsTab userId={userId} />
        )}
      </TabbedDetail>
    </div>
  );
}
