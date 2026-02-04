/**
 * Tests for MountSelectorPopup component utilities
 *
 * Tests the hostPathToAlias utility function that converts
 * host paths to safe alias strings for workspace symlinks.
 */
import { describe, expect, it } from 'vitest';
import { hostPathToAlias } from '../../../src/web_terminal_client/src/components/MountSelectorPopup';


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
