/**
 * Application constants
 *
 * Extracted from App.tsx for better modularity.
 */

import type { TerminalEvent } from '../types.js';

// Global hotkey codes that should be blocked from inserting characters in input fields
// On macOS, Option+key produces special characters (e.g., Option+E = ´, Option+N = ˜)
export const BLOCKED_HOTKEY_CODES = ['KeyE', 'KeyD', 'KeyN', 'BracketLeft', 'BracketRight'];

// Session ID validation: must match backend pattern YYYYMMDD_HHMMSS_8hexchars
// Defense in depth - validates before API calls and URL navigation
export const SESSION_ID_PATTERN = /^\d{8}_\d{6}_[a-f0-9]{8}$/;

export const STATUS_LABELS: Record<string, string> = {
  idle: 'Idle',
  running: 'Running',
  complete: 'Complete',
  partial: 'Partial',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export const STATUS_CLASS: Record<string, string> = {
  idle: 'status-idle',
  running: 'status-running',
  complete: 'status-complete',
  partial: 'status-partial',
  failed: 'status-failed',
  cancelled: 'status-cancelled',
};

export const EMPTY_EVENTS: TerminalEvent[] = [];

export const TOOL_COLOR_CLASS: Record<string, string> = {
  Read: 'tool-read',
  Bash: 'tool-bash',
  Write: 'tool-write',
  WebFetch: 'tool-webfetch',
  Output: 'tool-output',
  Think: 'tool-think',
};

export const TOOL_SYMBOL: Record<string, string> = {
  Read: '◉',
  Bash: '▶',
  Write: '✎',
  WebFetch: '⬡',
  Output: '◈',
  Think: '◇',
};

export const OUTPUT_STATUS_CLASS: Record<string, string> = {
  complete: 'output-status-complete',
  partial: 'output-status-partial',
  failed: 'output-status-failed',
  running: 'output-status-running',
  cancelled: 'output-status-cancelled',
};

export const STATUS_ALIASES: Record<string, string> = {
  completed: 'complete',
  complete: 'complete',
  failed: 'failed',
  error: 'failed',
  cancelled: 'cancelled',
  canceled: 'cancelled',
  running: 'running',
  partial: 'partial',
};

// Placeholder values that don't represent real errors
export const ERROR_PLACEHOLDERS = new Set([
  'none', 'none yet', 'no error', 'no errors', 'n/a', 'na', 'null', 'undefined', 'empty', '-', ''
]);

// Collapsible output component configuration
export const COLLAPSED_LINE_COUNT = 10;

// Spinner animation frames
export const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

// Number of lines to show for collapsed large user messages
export const USER_MESSAGE_COLLAPSED_LINES = 10;

// Threshold for auto-collapsing user messages (in lines) - any message with more lines gets collapsed
export const USER_MESSAGE_AUTO_COLLAPSE_THRESHOLD = 20;
