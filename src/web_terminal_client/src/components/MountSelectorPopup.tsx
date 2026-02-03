/**
 * Mount Selector Popup Component
 *
 * A professional popup interface for selecting dynamic mounts.
 * Features:
 * - Prominent folder icon trigger button
 * - Popup with mount selection form
 * - One-line status showing active mounts
 * - Persistent storage via localStorage for follow-up sessions
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import type { DynamicMountRequest, DynamicBaseInfo, AvailableDynamicMountsResponse } from '../api';

// Re-export types for consumers that import from this module
export type { DynamicMountRequest, DynamicBaseInfo, AvailableDynamicMountsResponse };

interface Props {
  baseUrl: string;
  token: string;
  selectedMounts: DynamicMountRequest[];
  onMountsChange: (mounts: DynamicMountRequest[]) => void;
}

const STORAGE_KEY = 'ag3ntum_dynamic_mounts';

// localStorage helpers (same pattern as model/panel persistence)
function getStoredMounts(): DynamicMountRequest[] | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return JSON.parse(saved);
    }
  } catch {
    // Invalid data, ignore
  }
  return null;
}

function setStoredMounts(mounts: DynamicMountRequest[]): void {
  try {
    if (mounts.length > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(mounts));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Ignore storage errors
  }
}

export function MountSelectorPopup({ baseUrl, token, selectedMounts, onMountsChange }: Props): JSX.Element | null {
  const [availableBases, setAvailableBases] = useState<DynamicBaseInfo[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [newMount, setNewMount] = useState<Partial<DynamicMountRequest>>({});
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [maxMounts, setMaxMounts] = useState(10);
  const [error, setError] = useState<string | null>(null);
  const popupRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    async function loadBases() {
      try {
        const response = await fetch(baseUrl + '/api/v1/sessions/dynamic-mounts/available', {
          headers: { Authorization: 'Bearer ' + token }
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

  useEffect(() => {
    const saved = getStoredMounts();
    if (saved) {
      onMountsChange(saved);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateMounts = useCallback((mounts: DynamicMountRequest[]) => {
    onMountsChange(mounts);
    setStoredMounts(mounts);
  }, [onMountsChange]);

  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setError(null);
      }
    };

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsOpen(false);
        setError(null);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  const handleAddMount = () => {
    if (!newMount.base || !newMount.alias) {
      setError('Base and alias are required');
      return;
    }

    if (selectedMounts.some(m => m.alias === newMount.alias)) {
      setError('Alias already in use');
      return;
    }

    if (selectedMounts.length >= maxMounts) {
      setError('Maximum ' + maxMounts + ' mounts allowed per session');
      return;
    }

    const baseInfo = availableBases.find(b => b.name === newMount.base);
    const requestedMode = newMount.mode || 'ro';
    const effectiveMode = baseInfo && baseInfo.max_mode === 'ro' ? 'ro' : requestedMode;

    const mount: DynamicMountRequest = {
      base: newMount.base,
      subpath: newMount.subpath || undefined,
      alias: newMount.alias,
      mode: effectiveMode,
    };

    updateMounts([...selectedMounts, mount]);
    setNewMount({});
    setError(null);
  };

  const handleRemoveMount = (alias: string) => {
    updateMounts(selectedMounts.filter(m => m.alias !== alias));
  };

  const handleClearAll = () => {
    updateMounts([]);
    setError(null);
  };

  const handleClose = () => {
    setIsOpen(false);
    setError(null);
  };

  if (loading || !enabled || availableBases.length === 0) {
    return null;
  }

  const getMountStatusText = (): string => {
    if (selectedMounts.length === 0) {
      return 'Mount';
    }
    if (selectedMounts.length === 1) {
      return selectedMounts[0].alias;
    }
    return selectedMounts.length + ' mounts';
  };

  return (
    <div className="mount-selector-wrapper" ref={popupRef}>
      <button
        type="button"
        className={'mount-trigger-btn ' + (selectedMounts.length > 0 ? 'has-mounts' : '')}
        onClick={() => setIsOpen(!isOpen)}
        title={selectedMounts.length > 0 ? 'Mounts: ' + selectedMounts.map(m => m.alias).join(', ') : 'Configure mounts'}
      >
        <span className="mount-trigger-icon">+</span>
        <span className="mount-trigger-text">[{getMountStatusText()}]</span>
      </button>

      {isOpen && (
        <div className="mount-popup">
          <div className="mount-popup-header">
            <h3 className="mount-popup-title">Dynamic Mounts</h3>
            <span className="mount-popup-count">{selectedMounts.length}/{maxMounts}</span>
            {selectedMounts.length > 0 && (
              <button
                type="button"
                className="mount-popup-clear"
                onClick={handleClearAll}
                title="Clear all mounts"
              >
                Clear
              </button>
            )}
            <button
              type="button"
              className="mount-popup-close"
              onClick={handleClose}
              title="Close"
            >
              ✓
            </button>
          </div>

          {error && <div className="mount-popup-error">{error}</div>}

          {selectedMounts.length > 0 && (
            <div className="mount-popup-list">
              {selectedMounts.map((mount) => (
                <div key={mount.alias} className="mount-popup-item">
                  <span className="mount-item-icon">{mount.mode === 'rw' ? 'R/W' : 'R/O'}</span>
                  <div className="mount-item-info">
                    <span className="mount-item-alias">{mount.alias}</span>
                    <span className="mount-item-path">
                      {mount.base}{mount.subpath ? '/' + mount.subpath : ''}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="mount-item-remove"
                    onClick={() => handleRemoveMount(mount.alias)}
                    title="Remove mount"
                  >
                    x
                  </button>
                </div>
              ))}
            </div>
          )}

          {selectedMounts.length < maxMounts && (
            <div className="mount-popup-form">
              <div className="mount-form-row">
                <select
                  value={newMount.base || ''}
                  onChange={(e) => setNewMount({ ...newMount, base: e.target.value })}
                  className="mount-form-select"
                >
                  <option value="">Select mount base...</option>
                  {availableBases.map((base) => (
                    <option key={base.name} value={base.name}>
                      {base.name} ({base.max_mode})
                    </option>
                  ))}
                </select>
              </div>

              {newMount.base && (
                <>
                  <div className="mount-form-row mount-form-row-split">
                    <input
                      type="text"
                      placeholder="Alias (required)"
                      value={newMount.alias || ''}
                      onChange={(e) => setNewMount({
                        ...newMount,
                        alias: e.target.value.replace(/[^a-zA-Z0-9_-]/g, '')
                      })}
                      className="mount-form-input"
                    />
                    <select
                      value={newMount.mode || 'ro'}
                      onChange={(e) => setNewMount({ ...newMount, mode: e.target.value as 'ro' | 'rw' })}
                      className="mount-form-mode"
                      disabled={availableBases.find(b => b.name === newMount.base)?.max_mode === 'ro'}
                    >
                      <option value="ro">R/O</option>
                      <option value="rw">R/W</option>
                    </select>
                  </div>

                  <div className="mount-form-row">
                    <input
                      type="text"
                      placeholder="Subpath (optional)"
                      value={newMount.subpath || ''}
                      onChange={(e) => setNewMount({ ...newMount, subpath: e.target.value })}
                      className="mount-form-input"
                    />
                  </div>

                  <div className="mount-form-actions">
                    <button
                      type="button"
                      className="mount-form-add"
                      onClick={handleAddMount}
                      disabled={!newMount.base || !newMount.alias}
                    >
                      + Add Mount
                    </button>
                  </div>
                </>
              )}

              {!newMount.base && selectedMounts.length === 0 && (
                <p className="mount-popup-hint">
                  Select a base folder to mount into the agent workspace.
                </p>
              )}
            </div>
          )}

          {selectedMounts.length >= maxMounts && (
            <p className="mount-popup-limit">Maximum mounts reached ({maxMounts})</p>
          )}
        </div>
      )}
    </div>
  );
}

export default MountSelectorPopup;
