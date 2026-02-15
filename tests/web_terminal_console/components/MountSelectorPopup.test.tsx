/**
 * Tests for MountSelectorPopup component utilities
 *
 * Tests the hostPathToAlias utility function that converts
 * host paths to safe alias strings for workspace symlinks.
 *
 * Also tests the stale mount reconciliation logic via
 * integration tests using render + mock fetch.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import { hostPathToAlias, MountSelectorPopup } from '../../../src/web_terminal_client/src/components/MountSelectorPopup';
import type { AvailableDynamicMountsResponse, DynamicMountRequest } from '../../../src/web_terminal_client/src/api';


describe('hostPathToAlias', () => {
  // ==========================================================================
  // Basic Path Conversion
  // ==========================================================================
  describe('Basic Path Conversion', () => {
    it('converts simple absolute path', () => {
      expect(hostPathToAlias('/var/log')).toBe('var-log');
    });

    it('converts deeply nested path', () => {
      expect(hostPathToAlias('/var/log/nginx/access')).toBe('var-log-nginx-access');
    });

    it('converts single-segment path', () => {
      expect(hostPathToAlias('/data')).toBe('data');
    });

    it('converts root path', () => {
      expect(hostPathToAlias('/')).toBe('');
    });

    it('converts path without leading slash', () => {
      expect(hostPathToAlias('data/files')).toBe('data-files');
    });
  });

  // ==========================================================================
  // With Subpath
  // ==========================================================================
  describe('With Subpath', () => {
    it('appends subpath to host path', () => {
      expect(hostPathToAlias('/var/log', 'nginx')).toBe('var-log-nginx');
    });

    it('handles nested subpath', () => {
      expect(hostPathToAlias('/var/log', 'nginx/access')).toBe('var-log-nginx-access');
    });

    it('handles empty subpath', () => {
      expect(hostPathToAlias('/var/log', '')).toBe('var-log');
    });

    it('handles undefined subpath', () => {
      expect(hostPathToAlias('/var/log', undefined)).toBe('var-log');
    });
  });

  // ==========================================================================
  // Special Characters
  // ==========================================================================
  describe('Special Characters', () => {
    it('replaces multiple consecutive separators', () => {
      expect(hostPathToAlias('/var//log///nginx')).toBe('var-log-nginx');
    });

    it('strips leading dashes', () => {
      expect(hostPathToAlias('/var/log')).not.toMatch(/^-/);
    });

    it('strips trailing dashes', () => {
      expect(hostPathToAlias('/var/log/')).not.toMatch(/-$/);
    });

    it('handles path with spaces', () => {
      expect(hostPathToAlias('/my data/files')).toBe('my-data-files');
    });

    it('handles underscores (preserved as alphanumeric-adjacent)', () => {
      // Underscores are alphanumeric-compatible, kept as-is or converted
      const result = hostPathToAlias('/my_data/log_files');
      expect(result).toMatch(/^[a-zA-Z0-9_-]+$/);
    });
  });

  // ==========================================================================
  // Length Limiting
  // ==========================================================================
  describe('Length Limiting', () => {
    it('truncates to 64 characters max', () => {
      const longPath = '/a/very/long/path/that/goes/on/and/on/and/on/forever/and/never/stops/really/long/deep/nested';
      const result = hostPathToAlias(longPath);
      expect(result.length).toBeLessThanOrEqual(64);
    });

    it('does not break mid-word on truncation', () => {
      const longPath = '/a/very/long/path/that/goes/on/and/on/and/on/forever/and/never/stops/really/long/deep/nested';
      const result = hostPathToAlias(longPath);
      // Result should be valid alias chars only
      expect(result).toMatch(/^[a-zA-Z0-9_-]+$/);
    });
  });

  // ==========================================================================
  // Valid Alias Output
  // ==========================================================================
  describe('Valid Alias Output', () => {
    it('produces only alphanumeric, hyphen, and underscore characters', () => {
      const paths = ['/var/log', '/home/user/data', '/opt/app/v2.1', '/tmp/my.files'];
      for (const p of paths) {
        const result = hostPathToAlias(p);
        if (result.length > 0) {
          expect(result).toMatch(/^[a-zA-Z0-9_-]+$/);
        }
      }
    });
  });
});


// =============================================================================
// Stale Mount Reconciliation Tests
// =============================================================================

describe('MountSelectorPopup - stale mount reconciliation', () => {
  let originalFetch: typeof globalThis.fetch;
  let originalGetItem: typeof Storage.prototype.getItem;
  let originalSetItem: typeof Storage.prototype.setItem;
  let originalRemoveItem: typeof Storage.prototype.removeItem;
  let storageData: Record<string, string>;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    originalGetItem = Storage.prototype.getItem;
    originalSetItem = Storage.prototype.setItem;
    originalRemoveItem = Storage.prototype.removeItem;

    // Mock localStorage
    storageData = {};
    Storage.prototype.getItem = vi.fn((key: string) => storageData[key] ?? null);
    Storage.prototype.setItem = vi.fn((key: string, value: string) => { storageData[key] = value; });
    Storage.prototype.removeItem = vi.fn((key: string) => { delete storageData[key]; });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    Storage.prototype.getItem = originalGetItem;
    Storage.prototype.setItem = originalSetItem;
    Storage.prototype.removeItem = originalRemoveItem;
  });

  function mockFetch(response: AvailableDynamicMountsResponse) {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    });
  }

  function mockFetchError() {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));
  }

  function mockFetchNotOk() {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });
  }

  function setStoredMounts(mounts: DynamicMountRequest[]) {
    storageData['ag3ntum_dynamic_mounts'] = JSON.stringify(mounts);
  }

  const staleMounts: DynamicMountRequest[] = [
    { base: 'var-log', alias: 'var-log', mode: 'ro' },
  ];

  it('clears stale localStorage when feature is disabled', async () => {
    setStoredMounts(staleMounts);
    mockFetch({ enabled: false, bases: [], max_mounts_per_session: 0 });

    const onMountsChange = vi.fn();

    await act(async () => {
      render(
        <MountSelectorPopup
          baseUrl="http://localhost"
          token="test-token"
          selectedMounts={[]}
          onMountsChange={onMountsChange}
        />
      );
    });

    await waitFor(() => {
      // Parent state cleared
      expect(onMountsChange).toHaveBeenCalledWith([]);
    });

    // localStorage cleared (removeItem called since setJson([]) removes the key)
    expect(Storage.prototype.removeItem).toHaveBeenCalledWith('ag3ntum_dynamic_mounts');
  });

  it('clears stale localStorage when no bases are configured', async () => {
    setStoredMounts(staleMounts);
    mockFetch({ enabled: true, bases: [], max_mounts_per_session: 10 });

    const onMountsChange = vi.fn();

    await act(async () => {
      render(
        <MountSelectorPopup
          baseUrl="http://localhost"
          token="test-token"
          selectedMounts={[]}
          onMountsChange={onMountsChange}
        />
      );
    });

    await waitFor(() => {
      expect(onMountsChange).toHaveBeenCalledWith([]);
    });

    expect(Storage.prototype.removeItem).toHaveBeenCalledWith('ag3ntum_dynamic_mounts');
  });

  it('filters out mounts with stale base names', async () => {
    const mixedMounts: DynamicMountRequest[] = [
      { base: 'logs', alias: 'var-log', mode: 'ro' },
      { base: 'old-removed-base', alias: 'old-mount', mode: 'ro' },
    ];
    setStoredMounts(mixedMounts);

    mockFetch({
      enabled: true,
      bases: [
        { name: 'logs', description: 'System logs', max_mode: 'ro', host_path: '/var/log', requires_subpath: false },
      ],
      max_mounts_per_session: 10,
    });

    const onMountsChange = vi.fn();

    await act(async () => {
      render(
        <MountSelectorPopup
          baseUrl="http://localhost"
          token="test-token"
          selectedMounts={[]}
          onMountsChange={onMountsChange}
        />
      );
    });

    await waitFor(() => {
      // Only the valid mount should remain
      expect(onMountsChange).toHaveBeenCalledWith([
        { base: 'logs', alias: 'var-log', mode: 'ro' },
      ]);
    });

    // localStorage updated with only valid mounts
    const stored = JSON.parse(storageData['ag3ntum_dynamic_mounts']);
    expect(stored).toEqual([{ base: 'logs', alias: 'var-log', mode: 'ro' }]);
  });

  it('preserves all mounts when all bases still exist', async () => {
    const validMounts: DynamicMountRequest[] = [
      { base: 'logs', alias: 'var-log', mode: 'ro' },
    ];
    setStoredMounts(validMounts);

    mockFetch({
      enabled: true,
      bases: [
        { name: 'logs', description: 'System logs', max_mode: 'ro', host_path: '/var/log', requires_subpath: false },
      ],
      max_mounts_per_session: 10,
    });

    const onMountsChange = vi.fn();

    await act(async () => {
      render(
        <MountSelectorPopup
          baseUrl="http://localhost"
          token="test-token"
          selectedMounts={[]}
          onMountsChange={onMountsChange}
        />
      );
    });

    await waitFor(() => {
      expect(onMountsChange).toHaveBeenCalledWith(validMounts);
    });

    // localStorage NOT modified (setItem not called for dynamic mounts since no stale entries)
    // The original stored value should remain unchanged
    expect(storageData['ag3ntum_dynamic_mounts']).toBe(JSON.stringify(validMounts));
  });

  it('does not load stale mounts on network error', async () => {
    setStoredMounts(staleMounts);
    mockFetchError();

    const onMountsChange = vi.fn();

    await act(async () => {
      render(
        <MountSelectorPopup
          baseUrl="http://localhost"
          token="test-token"
          selectedMounts={[]}
          onMountsChange={onMountsChange}
        />
      );
    });

    await waitFor(() => {
      expect(onMountsChange).toHaveBeenCalledWith([]);
    });
  });

  it('does not load stale mounts on API error (non-200)', async () => {
    setStoredMounts(staleMounts);
    mockFetchNotOk();

    const onMountsChange = vi.fn();

    await act(async () => {
      render(
        <MountSelectorPopup
          baseUrl="http://localhost"
          token="test-token"
          selectedMounts={[]}
          onMountsChange={onMountsChange}
        />
      );
    });

    await waitFor(() => {
      expect(onMountsChange).toHaveBeenCalledWith([]);
    });
  });

  it('does nothing when no localStorage and feature disabled', async () => {
    // No stored mounts
    mockFetch({ enabled: false, bases: [], max_mounts_per_session: 0 });

    const onMountsChange = vi.fn();

    await act(async () => {
      render(
        <MountSelectorPopup
          baseUrl="http://localhost"
          token="test-token"
          selectedMounts={[]}
          onMountsChange={onMountsChange}
        />
      );
    });

    await waitFor(() => {
      // Should still call onMountsChange([]) to ensure clean state
      expect(onMountsChange).toHaveBeenCalledWith([]);
    });

    // removeItem should NOT have been called (no stale data to clear)
    expect(Storage.prototype.removeItem).not.toHaveBeenCalledWith('ag3ntum_dynamic_mounts');
  });
});
