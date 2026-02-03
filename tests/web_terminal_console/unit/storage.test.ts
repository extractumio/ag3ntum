import { describe, it, expect, beforeEach } from 'vitest';
import {
  getString, setString,
  getBoolean, setBoolean,
  getNumber, setNumber,
  getJson, setJson,
  remove,
} from '../../../src/web_terminal_client/src/storage';

describe('storage utility', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('getString / setString', () => {
    it('returns null when key is absent', () => {
      expect(getString('auth_token')).toBeNull();
    });

    it('round-trips a string value', () => {
      setString('auth_token', 'test-token-123');
      expect(getString('auth_token')).toBe('test-token-123');
    });

    it('works with ag3ntum_selected_model key', () => {
      setString('ag3ntum_selected_model', 'claude-sonnet-4-20250514');
      expect(getString('ag3ntum_selected_model')).toBe('claude-sonnet-4-20250514');
    });
  });

  describe('getBoolean / setBoolean', () => {
    it('returns default (false) when key is absent', () => {
      expect(getBoolean('ag3ntum_right_panel_collapsed')).toBe(false);
    });

    it('round-trips true', () => {
      setBoolean('ag3ntum_right_panel_collapsed', true);
      expect(getBoolean('ag3ntum_right_panel_collapsed')).toBe(true);
    });

    it('round-trips false', () => {
      setBoolean('ag3ntum_right_panel_collapsed', false);
      expect(getBoolean('ag3ntum_right_panel_collapsed')).toBe(false);
    });

    it('stores as string "true"/"false"', () => {
      setBoolean('ag3ntum_right_panel_collapsed', true);
      expect(localStorage.getItem('ag3ntum_right_panel_collapsed')).toBe('true');
    });
  });

  describe('getNumber / setNumber', () => {
    it('returns default (400) when key is absent', () => {
      expect(getNumber('ag3ntum_right_panel_width')).toBe(400);
    });

    it('round-trips a number', () => {
      setNumber('ag3ntum_right_panel_width', 600);
      expect(getNumber('ag3ntum_right_panel_width')).toBe(600);
    });

    it('returns default for NaN', () => {
      localStorage.setItem('ag3ntum_right_panel_width', 'not-a-number');
      expect(getNumber('ag3ntum_right_panel_width')).toBe(400);
    });

    it('stores as string', () => {
      setNumber('ag3ntum_right_panel_width', 350);
      expect(localStorage.getItem('ag3ntum_right_panel_width')).toBe('350');
    });
  });

  describe('getJson / setJson', () => {
    it('returns null when key is absent', () => {
      expect(getJson('ag3ntum_dynamic_mounts')).toBeNull();
    });

    it('round-trips a JSON array', () => {
      const mounts = [{ base: 'data', alias: 'mydata', mode: 'ro' as const }];
      setJson('ag3ntum_dynamic_mounts', mounts);
      expect(getJson('ag3ntum_dynamic_mounts')).toEqual(mounts);
    });

    it('removes key when set to null', () => {
      const mounts = [{ base: 'data', alias: 'mydata' }];
      setJson('ag3ntum_dynamic_mounts', mounts as any);
      setJson('ag3ntum_dynamic_mounts', null);
      expect(localStorage.getItem('ag3ntum_dynamic_mounts')).toBeNull();
    });

    it('removes key when set to empty array', () => {
      const mounts = [{ base: 'data', alias: 'mydata' }];
      setJson('ag3ntum_dynamic_mounts', mounts as any);
      setJson('ag3ntum_dynamic_mounts', [] as any);
      expect(localStorage.getItem('ag3ntum_dynamic_mounts')).toBeNull();
    });

    it('returns null for invalid JSON', () => {
      localStorage.setItem('ag3ntum_dynamic_mounts', '{bad json');
      expect(getJson('ag3ntum_dynamic_mounts')).toBeNull();
    });
  });

  describe('remove', () => {
    it('removes existing key', () => {
      setString('auth_token', 'token');
      remove('auth_token');
      expect(getString('auth_token')).toBeNull();
    });

    it('does not throw for absent key', () => {
      expect(() => remove('auth_token')).not.toThrow();
    });
  });

});
