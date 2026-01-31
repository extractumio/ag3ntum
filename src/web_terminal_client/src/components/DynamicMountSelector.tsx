import { useState, useEffect, useCallback } from 'react';
import Cookies from 'js-cookie';

export interface DynamicMountRequest {
  base: string;
  subpath?: string;
  alias: string;
  mode?: 'ro' | 'rw';
}

export interface DynamicBaseInfo {
  name: string;
  description: string;
  max_mode: string;
  requires_subpath: boolean;
}

export interface AvailableDynamicMountsResponse {
  enabled: boolean;
  bases: DynamicBaseInfo[];
  max_mounts_per_session: number;
}

interface Props {
  baseUrl: string;
  token: string;
  onMountsChange: (mounts: DynamicMountRequest[]) => void;
}

const COOKIE_KEY = 'ag3ntum_dynamic_mounts';
const COOKIE_EXPIRY = 30; // days

export function DynamicMountSelector({ baseUrl, token, onMountsChange }: Props): JSX.Element | null {
  const [availableBases, setAvailableBases] = useState<DynamicBaseInfo[]>([]);
  const [selectedMounts, setSelectedMounts] = useState<DynamicMountRequest[]>([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newMount, setNewMount] = useState<Partial<DynamicMountRequest>>({});
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [maxMounts, setMaxMounts] = useState(10);
  const [error, setError] = useState<string | null>(null);

  // Load available bases from API
  useEffect(() => {
    async function loadBases() {
      try {
        const response = await fetch(`${baseUrl}/api/v1/sessions/dynamic-mounts/available`, {
          headers: { Authorization: `Bearer ${token}` }
        });
        if (response.ok) {
          const data: AvailableDynamicMountsResponse = await response.json();
          setEnabled(data.enabled);
          setAvailableBases(data.bases);
          setMaxMounts(data.max_mounts_per_session);
        }
      } catch (e) {
        console.error('Failed to load available mounts:', e);
        setEnabled(false);
      } finally {
        setLoading(false);
      }
    }
    if (token) {
      loadBases();
    }
  }, [baseUrl, token]);

  // Load saved mounts from cookie on mount
  useEffect(() => {
    const saved = Cookies.get(COOKIE_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setSelectedMounts(parsed);
        onMountsChange(parsed);
      } catch {
        // Invalid cookie, ignore
      }
    }
  }, [onMountsChange]);

  // Save to cookie whenever mounts change
  const updateMounts = useCallback((mounts: DynamicMountRequest[]) => {
    setSelectedMounts(mounts);
    onMountsChange(mounts);
    if (mounts.length > 0) {
      Cookies.set(COOKIE_KEY, JSON.stringify(mounts), { expires: COOKIE_EXPIRY });
    } else {
      Cookies.remove(COOKIE_KEY);
    }
  }, [onMountsChange]);

  const handleAddMount = () => {
    if (!newMount.base || !newMount.alias) {
      setError('Base and alias are required');
      return;
    }

    // Check for duplicate alias
    if (selectedMounts.some(m => m.alias === newMount.alias)) {
      setError('Alias already in use');
      return;
    }

    // Check max mounts limit
    if (selectedMounts.length >= maxMounts) {
      setError(`Maximum ${maxMounts} mounts allowed per session`);
      return;
    }

    const mount: DynamicMountRequest = {
      base: newMount.base,
      subpath: newMount.subpath || undefined,
      alias: newMount.alias,
      mode: newMount.mode || 'ro',
    };

    updateMounts([...selectedMounts, mount]);
    setNewMount({});
    setShowAddForm(false);
    setError(null);
  };

  const handleRemoveMount = (alias: string) => {
    updateMounts(selectedMounts.filter(m => m.alias !== alias));
  };

  const handleClearAll = () => {
    updateMounts([]);
  };

  // Don't render if feature is disabled or still loading
  if (loading) {
    return null;
  }

  if (!enabled || availableBases.length === 0) {
    return null;
  }

  return (
    <div className="dynamic-mount-selector">
      <div className="dynamic-mount-header">
        <span className="dynamic-mount-title">
          Dynamic Mounts ({selectedMounts.length}/{maxMounts})
        </span>
        {selectedMounts.length > 0 && (
          <button
            className="clear-mounts-btn"
            onClick={handleClearAll}
            title="Clear all mounts"
          >
            Clear
          </button>
        )}
      </div>

      {error && <div className="mount-error">{error}</div>}

      {/* Selected mounts */}
      {selectedMounts.length > 0 && (
        <div className="selected-mounts">
          {selectedMounts.map((mount) => (
            <div key={mount.alias} className="mount-chip">
              <span className="mount-chip-icon">{mount.mode === 'rw' ? '📝' : '📖'}</span>
              <span className="mount-chip-alias">{mount.alias}</span>
              <span className="mount-chip-base">({mount.base}{mount.subpath ? `/${mount.subpath}` : ''})</span>
              <button
                className="mount-chip-remove"
                onClick={() => handleRemoveMount(mount.alias)}
                title="Remove mount"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add mount form */}
      {showAddForm ? (
        <div className="add-mount-form">
          <select
            value={newMount.base || ''}
            onChange={(e) => setNewMount({ ...newMount, base: e.target.value })}
            className="mount-select"
          >
            <option value="">Select base...</option>
            {availableBases.map((base) => (
              <option key={base.name} value={base.name}>
                {base.name} - {base.description} ({base.max_mode})
              </option>
            ))}
          </select>

          <input
            type="text"
            placeholder="Subpath (optional)"
            value={newMount.subpath || ''}
            onChange={(e) => setNewMount({ ...newMount, subpath: e.target.value })}
            className="mount-input"
          />

          <input
            type="text"
            placeholder="Alias (required)"
            value={newMount.alias || ''}
            onChange={(e) => setNewMount({ ...newMount, alias: e.target.value.replace(/[^a-zA-Z0-9_-]/g, '') })}
            className="mount-input"
          />

          <select
            value={newMount.mode || 'ro'}
            onChange={(e) => setNewMount({ ...newMount, mode: e.target.value as 'ro' | 'rw' })}
            className="mount-select mode-select"
          >
            <option value="ro">Read-only</option>
            <option value="rw">Read-write</option>
          </select>

          <div className="mount-form-buttons">
            <button className="mount-add-btn" onClick={handleAddMount}>Add</button>
            <button className="mount-cancel-btn" onClick={() => { setShowAddForm(false); setError(null); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <button
          className="add-mount-btn"
          onClick={() => setShowAddForm(true)}
          disabled={selectedMounts.length >= maxMounts}
        >
          + Add Mount
        </button>
      )}
    </div>
  );
}
