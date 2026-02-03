/**
 * Unified localStorage utility.
 *
 * All browser-side persistent storage goes through this module.
 * Every call is wrapped in try-catch with sensible defaults.
 * Keys use the `ag3ntum_` prefix (except `auth_token` for backward compat).
 *
 * Adding a new key? Add it to StorageSchema and DEFAULTS, then pick
 * the right accessor (getString/getBoolean/getNumber/getJson).
 */

import type { DynamicMountRequest } from './api';

// ── Storage Schema ──────────────────────────────────────────────

interface StorageSchema {
  auth_token:                     string | null;
  ag3ntum_dynamic_mounts:         DynamicMountRequest[] | null;
  ag3ntum_right_panel_collapsed:  boolean;
  ag3ntum_selected_model:         string | null;
  ag3ntum_right_panel_width:      number;
}

const DEFAULTS: StorageSchema = {
  auth_token:                     null,
  ag3ntum_dynamic_mounts:         null,
  ag3ntum_right_panel_collapsed:  false,
  ag3ntum_selected_model:         null,
  ag3ntum_right_panel_width:      400,
};

// ── Type Buckets ────────────────────────────────────────────────

type StringKeys  = 'auth_token' | 'ag3ntum_selected_model';
type BooleanKeys = 'ag3ntum_right_panel_collapsed';
type NumberKeys  = 'ag3ntum_right_panel_width';
type JsonKeys    = 'ag3ntum_dynamic_mounts';

// ── Accessors ───────────────────────────────────────────────────

export function getString(key: StringKeys): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return DEFAULTS[key];
  }
}

export function setString(key: StringKeys, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage full or unavailable
  }
}

export function getBoolean(key: BooleanKeys): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return DEFAULTS[key];
    return raw === 'true';
  } catch {
    return DEFAULTS[key];
  }
}

export function setBoolean(key: BooleanKeys, value: boolean): void {
  try {
    localStorage.setItem(key, value ? 'true' : 'false');
  } catch {
    // Storage full or unavailable
  }
}

export function getNumber(key: NumberKeys): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return DEFAULTS[key];
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : DEFAULTS[key];
  } catch {
    return DEFAULTS[key];
  }
}

export function setNumber(key: NumberKeys, value: number): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    // Storage full or unavailable
  }
}

export function getJson<K extends JsonKeys>(key: K): StorageSchema[K] {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return DEFAULTS[key];
    return JSON.parse(raw);
  } catch {
    return DEFAULTS[key];
  }
}

export function setJson<K extends JsonKeys>(key: K, value: StorageSchema[K]): void {
  try {
    if (value === null || (Array.isArray(value) && value.length === 0)) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, JSON.stringify(value));
    }
  } catch {
    // Storage full or unavailable
  }
}

export function remove(key: keyof StorageSchema): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // Storage full or unavailable
  }
}

