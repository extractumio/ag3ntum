import React, { useRef, useState } from 'react';
import { FormField } from '../../components/dashboard';
import { maskSSHKey, isEncryptedKey } from '../../utils/sshUtils';
import { testSSHConnection } from '../../sshApi';
import { useAuth } from '../../AuthContext';
import { ConnectionTestResult } from './ConnectionTestResult';
import { SSH_ACCESS_LEVELS } from '../../types/ssh';
import type { CreateSSHProfileRequest, SSHMode, SSHProfile, TestSSHConnectionResponse, UpdateSSHProfileRequest } from '../../types/ssh';

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

interface FormState {
  name: string;
  host: string;
  port: string;
  username: string;
  privateKey: string;
  rawKey: string;
  passphrase: string;
  privilegeLevel: number;
  description: string;
}

type FormErrors = Partial<Record<keyof FormState, string>>;

function buildInitialState(profile?: SSHProfile): FormState {
  if (profile) {
    return {
      name: profile.name,
      host: profile.host,
      port: String(profile.port),
      username: profile.username,
      privateKey: profile.key_preview || '',
      rawKey: '',
      passphrase: '',
      privilegeLevel: profile.privilege_level,
      description: profile.description || '',
    };
  }
  return {
    name: '',
    host: '',
    port: '22',
    username: '',
    privateKey: '',
    rawKey: '',
    passphrase: '',
    privilegeLevel: 1,
    description: '',
  };
}

function validateForm(state: FormState): FormErrors {
  const errors: FormErrors = {};
  if (!state.name.trim()) {
    errors.name = 'Profile name is required';
  } else if (!/^[a-z][a-z0-9._-]*$/.test(state.name)) {
    errors.name = 'Must start with a letter; lowercase letters, numbers, dots, _ or - only';
  }
  if (!state.host.trim()) errors.host = 'Host is required';
  if (!state.username.trim()) errors.username = 'Username is required';
  const portNum = Number(state.port);
  if (!state.port || isNaN(portNum) || portNum < 1 || portNum > 65535) {
    errors.port = 'Port must be 1–65535';
  }
  if (!state.rawKey && !state.privateKey) {
    errors.privateKey = 'Private key is required';
  }
  return errors;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SSHProfileFormProps {
  profile?: SSHProfile;
  onSave: (data: CreateSSHProfileRequest | UpdateSSHProfileRequest) => Promise<void>;
  onCancel: () => void;
  saving?: boolean;
  saveError?: string | null;
}

export function SSHProfileForm({
  profile,
  onSave,
  onCancel,
  saving = false,
  saveError = null,
}: SSHProfileFormProps) {
  const { token, baseUrl } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState<FormState>(() => buildInitialState(profile));
  const [errors, setErrors] = useState<FormErrors>({});
  const [keyLoaded, setKeyLoaded] = useState(false);
  const [testResult, setTestResult] = useState<TestSSHConnectionResponse | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);

  const needsPassphrase = isEncryptedKey(form.rawKey);
  const isEditing = !!profile;

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    if (errors[key]) setErrors((prev) => ({ ...prev, [key]: undefined }));
  };

  const handleKeyPaste = (raw: string) => {
    const trimmed = raw.trim();
    setField('rawKey', trimmed);
    setField('privateKey', maskSSHKey(trimmed));
    setKeyLoaded(true);
    setTestResult(null);
    if (errors.privateKey) setErrors((prev) => ({ ...prev, privateKey: undefined }));
  };

  const handleKeyFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const raw = (ev.target?.result as string) ?? '';
      handleKeyPaste(raw);
    };
    reader.readAsText(file);
    // Reset input so same file can be re-uploaded
    e.target.value = '';
  };

  const handleClearKey = () => {
    setField('rawKey', '');
    setField('privateKey', '');
    setField('passphrase', '');
    setKeyLoaded(false);
    setTestResult(null);
  };

  const handleTest = async () => {
    if (!token || !baseUrl) return;
    const keyToUse = form.rawKey;
    if (!keyToUse && !isEditing) {
      setTestError('Paste a private key first');
      return;
    }
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const result = await testSSHConnection(baseUrl, token, {
        host: form.host,
        port: form.port ? Number(form.port) : 22,
        username: form.username,
        private_key: keyToUse,
        passphrase: form.passphrase || undefined,
      });
      setTestResult(result);
    } catch (e) {
      setTestError(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const validationErrors = validateForm(form);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }

    const baseData = {
      name: form.name.trim(),
      host: form.host.trim(),
      port: form.port ? Number(form.port) : 22,
      username: form.username.trim(),
      mode: SSH_ACCESS_LEVELS.find((l) => l.value === form.privilegeLevel)?.mode ?? ('operations' as SSHMode),
      privilege_level: form.privilegeLevel,
      description: form.description.trim() || undefined,
    };

    if (isEditing) {
      const update: UpdateSSHProfileRequest = { ...baseData };
      if (form.rawKey) {
        update.private_key = form.rawKey;
        if (form.passphrase) update.passphrase = form.passphrase;
      }
      await onSave(update);
    } else {
      await onSave({
        ...baseData,
        private_key: form.rawKey,
        passphrase: form.passphrase || undefined,
      } as CreateSSHProfileRequest);
    }
  };

  return (
    <form onSubmit={handleSubmit} noValidate>
      {/* Profile name */}
      <FormField label="Profile Name" required error={errors.name} hint="Lowercase, letters, numbers, - or _">
        <input
          className="dash-form-input"
          value={form.name}
          onChange={(e) => setField('name', e.target.value.toLowerCase())}
          placeholder="e.g. prod-web-01"
          disabled={saving}
        />
      </FormField>

      {/* Host / User / Port row */}
      <div className="dash-form-row" style={{ gridTemplateColumns: '1fr 1fr auto' }}>
        <FormField label="Host" required error={errors.host}>
          <input
            className="dash-form-input"
            value={form.host}
            onChange={(e) => setField('host', e.target.value.trim())}
            placeholder="hostname or IP"
            disabled={saving}
          />
        </FormField>
        <FormField label="Username" required error={errors.username}>
          <input
            className="dash-form-input"
            value={form.username}
            onChange={(e) => setField('username', e.target.value.trim())}
            placeholder="e.g. root"
            disabled={saving}
          />
        </FormField>
        <FormField label="Port" error={errors.port}>
          <input
            className="dash-form-input"
            value={form.port}
            onChange={(e) => setField('port', e.target.value)}
            placeholder="22"
            style={{ width: 80 }}
            disabled={saving}
          />
        </FormField>
      </div>

      {/* Private key */}
      <FormField
        label={isEditing ? 'Replace Private Key (optional)' : 'Private Key'}
        required={!isEditing}
        error={errors.privateKey}
        hint={isEditing ? 'Leave blank to keep existing key' : 'Paste your PEM private key or upload a file'}
      >
        <div style={{ display: 'flex', gap: 'var(--spacing-md)', marginBottom: 'var(--spacing-xs)' }}>
          <textarea
            className="dash-form-input ssh-key-textarea"
            value={form.privateKey}
            onChange={(e) => {
              const raw = e.target.value;
              // If user is typing fresh (not masked), treat as raw
              if (!raw.includes('**')) {
                handleKeyPaste(raw);
              }
            }}
            onPaste={(e) => {
              e.preventDefault();
              const pasted = e.clipboardData.getData('text');
              handleKeyPaste(pasted);
            }}
            placeholder="-----BEGIN ... PRIVATE KEY-----"
            rows={4}
            style={{ fontFamily: 'monospace', resize: 'vertical' }}
            disabled={saving}
            spellCheck={false}
          />
        </div>
        <div style={{ display: 'flex', gap: 'var(--spacing-md)', alignItems: 'center' }}>
          <button
            type="button"
            className="dash-btn dash-btn-sm dash-btn-secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={saving}
          >
            Upload key file
          </button>
          {keyLoaded && (
            <button
              type="button"
              className="dash-btn dash-btn-sm dash-btn-danger"
              onClick={handleClearKey}
              disabled={saving}
            >
              Clear
            </button>
          )}
          {profile?.key_fingerprint && !keyLoaded && (
            <span className="ssh-fingerprint-display">
              Current: <code className="ssh-fingerprint">{profile.key_fingerprint}</code>
            </span>
          )}
          {keyLoaded && form.rawKey && (
            <span className="ssh-fingerprint-display">Key loaded ({form.rawKey.length} bytes)</span>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pem,.key,text/plain"
          style={{ display: 'none' }}
          onChange={handleKeyFileUpload}
        />
      </FormField>

      {/* Passphrase — shown only when key is encrypted */}
      {needsPassphrase && (
        <FormField label="Passphrase" hint="Required for encrypted keys">
          <input
            className="dash-form-input"
            type="password"
            value={form.passphrase}
            onChange={(e) => setField('passphrase', e.target.value)}
            placeholder="Key passphrase"
            disabled={saving}
          />
        </FormField>
      )}

      {/* Access level */}
      <div className="dash-form-group">
        <label className="dash-form-label">Access Level</label>
        <div className="ssh-access-levels">
          {SSH_ACCESS_LEVELS.map((level) => (
            <label
              key={level.value}
              className={`ssh-access-level${form.privilegeLevel === level.value ? ' ssh-access-level-active' : ''}`}
            >
              <input
                type="radio"
                name="privilege_level"
                value={level.value}
                checked={form.privilegeLevel === level.value}
                onChange={() => setField('privilegeLevel', level.value)}
                disabled={saving}
              />
              <span>
                {level.label}
                {level.recommended && (
                  <span className="ssh-recommended"> recommended</span>
                )}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* Description */}
      <FormField label="Description" hint="Optional note about this connection">
        <textarea
          className="dash-form-input"
          value={form.description}
          onChange={(e) => setField('description', e.target.value)}
          placeholder="e.g. Production web server"
          rows={2}
          disabled={saving}
        />
      </FormField>

      {/* Test connection */}
      {testError && <div className="dash-error">{testError}</div>}
      <ConnectionTestResult result={testResult} loading={testing} />

      {/* Actions */}
      {saveError && <div className="dash-error">{saveError}</div>}
      <div className="dash-form-actions">
        <button
          type="submit"
          className="dash-btn dash-btn-primary"
          disabled={saving || testing}
        >
          {saving ? 'Saving...' : isEditing ? 'Save Changes' : 'Add Profile'}
        </button>
        <button
          type="button"
          className="dash-btn dash-btn-secondary"
          onClick={handleTest}
          disabled={saving || testing || (!form.host || !form.username)}
        >
          {testing ? 'Testing...' : 'Test Connection'}
        </button>
        <button
          type="button"
          className="dash-btn dash-btn-secondary"
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
