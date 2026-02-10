/**
 * Utility functions
 *
 * Pure utility functions extracted from App.tsx for better modularity.
 */

import React from 'react';
import YAML from 'yaml';
import type { TerminalEvent, SessionResponse } from '../types.js';
import type { ResultStatus, ToolCallView, TodoItem, StructuredMessage } from '../types/conversation';
import {
  BLOCKED_HOTKEY_CODES,
  SESSION_ID_PATTERN,
  STATUS_ALIASES,
  STATUS_LABELS,
  ERROR_PLACEHOLDERS,
} from '../constants';

// Handler to block Alt+key hotkeys from inserting characters in input fields
export const blockAltKeyHotkeys = (e: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>): boolean => {
  if (e.altKey && BLOCKED_HOTKEY_CODES.includes(e.nativeEvent.code)) {
    e.preventDefault();
    e.stopPropagation();
    return true; // Indicates the event was blocked
  }
  return false;
};

/**
 * Calculate the text to insert with smart spacing.
 * Adds space before if the character before cursor is non-whitespace
 * AND the text to insert doesn't already start with whitespace.
 * Adds space after if the character after cursor is non-whitespace
 * AND the text to insert doesn't already end with whitespace.
 *
 * @param value - Current textarea value
 * @param start - Selection start (cursor position)
 * @param end - Selection end (same as start if no selection)
 * @param textToInsert - Text to insert at cursor
 * @returns Object with paddedText, newValue, and newCursorPos
 */
export function calculateInsertText(
  value: string,
  start: number,
  end: number,
  textToInsert: string
): { paddedText: string; newValue: string; newCursorPos: number } {
  // If nothing to insert, return unchanged
  if (!textToInsert) {
    return { paddedText: '', newValue: value, newCursorPos: start };
  }

  const charBefore = start > 0 ? value[start - 1] : '';
  const charAfter = end < value.length ? value[end] : '';

  // Don't add space if text already has leading/trailing whitespace
  const needsSpaceBefore = charBefore !== '' && !/\s/.test(charBefore) && !/^\s/.test(textToInsert);
  const needsSpaceAfter = charAfter !== '' && !/\s/.test(charAfter) && !/\s$/.test(textToInsert);

  const paddedText = (needsSpaceBefore ? ' ' : '') + textToInsert + (needsSpaceAfter ? ' ' : '');
  const newValue = value.slice(0, start) + paddedText + value.slice(end);
  const newCursorPos = start + paddedText.length;

  return { paddedText, newValue, newCursorPos };
}

export function isValidSessionId(sessionId: string | undefined | null): sessionId is string {
  if (!sessionId) return false;
  if (sessionId.length > 24) return false;
  return SESSION_ID_PATTERN.test(sessionId);
}

export function normalizeStatus(value: string): string {
  const statusValue = value.toLowerCase();
  return (STATUS_ALIASES[statusValue] ?? statusValue) || 'idle';
}

/**
 * Truncate session title to prevent UI breakage from huge inputs.
 * - Removes all whitespace characters (\r\n, tabs, multiple spaces)
 * - Takes first 80 chars max
 * - Forces word break after 40 chars to prevent overflow
 */
export function truncateSessionTitle(title: string | null | undefined): string {
  if (!title) return 'No task';
  // Normalize whitespace: replace all \r\n, tabs, and multiple spaces with single space
  const normalized = title.replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!normalized) return 'No task';
  // Take first 80 chars
  let truncated = normalized.slice(0, 80);
  // Force word break after 40 chars by inserting zero-width space
  if (truncated.length > 40) {
    // Find a good break point (space) near the 40 char mark, or force break
    const breakPoint = truncated.lastIndexOf(' ', 45);
    if (breakPoint > 30) {
      // There's a space reasonably close to 40 chars, keep it
      truncated = truncated.slice(0, breakPoint) + ' ' + truncated.slice(breakPoint + 1);
    } else {
      // No good break point, insert zero-width space at 40 chars to allow wrapping
      truncated = truncated.slice(0, 40) + '\u200B' + truncated.slice(40);
    }
  }
  // Add ellipsis if we truncated
  if (normalized.length > 80) {
    truncated += '…';
  }
  return truncated;
}

/**
 * Check if an error string represents a meaningful error that should be displayed.
 * Filters out empty values and placeholder text like "None", "None yet", "No error", etc.
 */
export function isMeaningfulError(error: string | undefined | null): boolean {
  if (!error) {
    return false;
  }
  const normalized = error.trim().toLowerCase();
  return normalized !== '' && !ERROR_PLACEHOLDERS.has(normalized);
}

// Mapping from short field names to canonical names expected by the codebase
const FIELD_NAME_ALIASES: Record<string, string> = {
  status: 'request_status',
  error: 'request_error_message',
};

// Known status field names that indicate a valid structured header
const KNOWN_STATUS_FIELDS = new Set([
  'status', 'request_status',
  'error', 'request_error_message',
]);

/**
 * Extract fields from a header block between start and end indices.
 */
export function parseHeaderBlock(lines: string[], startIdx: number, endIdx: number): Record<string, string> {
  const fields: Record<string, string> = {};
  lines.slice(startIdx + 1, endIdx).forEach((line) => {
    if (!line.trim()) {
      return;
    }
    const separatorIndex = line.indexOf(':');
    if (separatorIndex === -1) {
      return;
    }
    let key = line.slice(0, separatorIndex).trim().toLowerCase();
    const value = line.slice(separatorIndex + 1).trim();
    if (key) {
      // Map short field names to canonical names
      key = FIELD_NAME_ALIASES[key] ?? key;
      fields[key] = value;
    }
  });
  return fields;
}

/**
 * Find a trailing header block at the end of lines.
 * Returns [startIndex, endIndex] or [-1, -1] if not found.
 */
export function findTrailingHeader(lines: string[]): [number, number] {
  // Search backwards for the closing ---
  let endIdx = -1;
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (lines[i].trim() === '---') {
      endIdx = i;
      break;
    }
  }
  if (endIdx === -1) {
    return [-1, -1];
  }

  // Search backwards from endIdx for the opening ---
  let startIdx = -1;
  for (let i = endIdx - 1; i >= 0; i -= 1) {
    if (lines[i].trim() === '---') {
      startIdx = i;
      break;
    }
  }
  if (startIdx === -1) {
    return [-1, -1];
  }

  // Verify this looks like a valid header block (has key: value pairs)
  let hasField = false;
  for (let i = startIdx + 1; i < endIdx; i += 1) {
    const stripped = lines[i].trim();
    if (stripped && stripped.includes(':')) {
      hasField = true;
      break;
    }
  }
  if (!hasField) {
    return [-1, -1];
  }

  return [startIdx, endIdx];
}

/**
 * Find an unclosed trailing header block at the end of lines (no closing ---).
 * Detects: ---\nkey: value\nkey: value (at end of text, without closing ---).
 * Returns the start index of the opening ---, or -1 if not found.
 */
export function findUnclosedTrailingHeader(lines: string[]): number {
  if (lines.length < 2) {
    return -1;
  }

  // Search backwards for a --- line
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (lines[i].trim() === '---') {
      const trailing = lines.slice(i + 1);
      if (trailing.length === 0) {
        return -1; // --- at the very end with nothing after
      }

      let hasStatusField = false;
      let allValid = true;
      for (const line of trailing) {
        const stripped = line.trim();
        if (!stripped) {
          continue;
        }
        if (!stripped.includes(':')) {
          allValid = false;
          break;
        }
        const rawKey = stripped.slice(0, stripped.indexOf(':')).trim().toLowerCase();
        const canonical = FIELD_NAME_ALIASES[rawKey] ?? rawKey;
        if (KNOWN_STATUS_FIELDS.has(rawKey) || KNOWN_STATUS_FIELDS.has(canonical)) {
          hasStatusField = true;
        }
      }

      if (allValid && hasStatusField) {
        return i;
      }
      return -1; // Only check the last --- occurrence
    }
  }

  return -1;
}

export function parseStructuredMessage(text: string): StructuredMessage {
  if (!text) {
    return { body: text, fields: {} };
  }

  let payload = text;
  const isFenced = payload.trim().startsWith('```');
  if (isFenced) {
    const fenceEnd = payload.indexOf('\n');
    if (fenceEnd !== -1) {
      payload = payload.slice(fenceEnd + 1);
    }
  }

  const lines = payload.split('\n');

  // Try to find header at the START of the message
  if (lines.length >= 3 && lines[0]?.trim() === '---') {
    let endIndex = -1;
    for (let i = 1; i < lines.length; i += 1) {
      if (lines[i].trim() === '---') {
        endIndex = i;
        break;
      }
    }
    if (endIndex !== -1) {
      const fields = parseHeaderBlock(lines, 0, endIndex);
      if (Object.keys(fields).length > 0) {
        let bodyStartIndex = endIndex + 1;
        if (isFenced) {
          while (bodyStartIndex < lines.length && lines[bodyStartIndex].trim() === '') {
            bodyStartIndex += 1;
          }
          if (lines[bodyStartIndex]?.trim().startsWith('```')) {
            bodyStartIndex += 1;
          }
        }
        let body = lines.slice(bodyStartIndex).join('\n');
        if (body.startsWith('\n')) {
          body = body.slice(1);
        }
        const statusRaw = fields.request_status;
        const status = statusRaw ? (normalizeStatus(statusRaw) as ResultStatus) : undefined;
        const error = fields.request_error_message ?? undefined;
        return { body, fields, status, error };
      }
    }
  }

  // Try to find closed header at the END of the message
  const [startIdx, endIdx] = findTrailingHeader(lines);
  if (startIdx !== -1 && endIdx !== -1) {
    const fields = parseHeaderBlock(lines, startIdx, endIdx);
    if (Object.keys(fields).length > 0) {
      // Body is everything before the trailing header
      let bodyLines = lines.slice(0, startIdx);
      // Remove trailing empty lines from body
      while (bodyLines.length > 0 && !bodyLines[bodyLines.length - 1].trim()) {
        bodyLines.pop();
      }
      const body = bodyLines.join('\n');
      const statusRaw = fields.request_status;
      const status = statusRaw ? (normalizeStatus(statusRaw) as ResultStatus) : undefined;
      const error = fields.request_error_message ?? undefined;
      return { body, fields, status, error };
    }
  }

  // Try to find unclosed header at the END of the message (no closing ---)
  const unclosedStart = findUnclosedTrailingHeader(lines);
  if (unclosedStart !== -1) {
    const fields: Record<string, string> = {};
    lines.slice(unclosedStart + 1).forEach((line) => {
      const stripped = line.trim();
      if (!stripped || !stripped.includes(':')) {
        return;
      }
      let key = stripped.slice(0, stripped.indexOf(':')).trim().toLowerCase();
      const value = stripped.slice(stripped.indexOf(':') + 1).trim();
      if (key) {
        key = FIELD_NAME_ALIASES[key] ?? key;
        fields[key] = value;
      }
    });
    if (Object.keys(fields).length > 0) {
      let bodyLines = lines.slice(0, unclosedStart);
      while (bodyLines.length > 0 && !bodyLines[bodyLines.length - 1].trim()) {
        bodyLines.pop();
      }
      const body = bodyLines.join('\n');
      const statusRaw = fields.request_status;
      const status = statusRaw ? (normalizeStatus(statusRaw) as ResultStatus) : undefined;
      const error = fields.request_error_message ?? undefined;
      return { body, fields, status, error };
    }
  }

  return { body: text, fields: {} };
}

export function formatDuration(durationMs?: number | null): string {
  if (!durationMs) {
    return '0.0s';
  }
  return durationMs < 1000
    ? `${durationMs}ms`
    : `${(durationMs / 1000).toFixed(1)}s`;
}

// Extract text preview from subagent result (handles JSON array format with {type: 'text', text: '...'})
export function extractSubagentPreview(rawText: string): string {
  if (!rawText) return '';

  // Try to parse as JSON array with text content blocks
  const trimmed = rawText.trim();
  if (trimmed.startsWith('[') && trimmed.includes("'type': 'text'")) {
    // Extract text content from format like [{'type': 'text', 'text': 'actual content...'}]
    const textMatch = trimmed.match(/'text':\s*'([^']*)/);
    if (textMatch && textMatch[1]) {
      return textMatch[1];
    }
  }

  // Also try standard JSON format with double quotes
  if (trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed) && parsed.length > 0 && parsed[0].type === 'text' && parsed[0].text) {
        return parsed[0].text;
      }
    } catch {
      // Not valid JSON, use as-is
    }
  }

  // Return first line of raw text
  const firstLine = rawText.split('\n')[0];
  return firstLine;
}

export function formatCost(cost?: number | null): string {
  if (cost === null || cost === undefined) {
    return '$0.0000';
  }
  return `$${cost.toFixed(4)}`;
}

export function formatTimestamp(timestamp?: string): string {
  if (!timestamp) {
    return '--:--:--';
  }
  const date = new Date(timestamp);
  return date.toLocaleTimeString('en-US', { hour12: false });
}

export function isSafeRelativePath(path: string): boolean {
  return Boolean(path && !path.startsWith('/') && !path.startsWith('~') && !path.includes('..'));
}

export function getLastServerSequence(events: TerminalEvent[]): number | null {
  const sequences = events
    .filter((event) => event.type !== 'user_message' && Number.isFinite(event.sequence))
    .map((event) => event.sequence);
  if (sequences.length === 0) {
    return null;
  }
  return Math.max(...sequences);
}

export function seedSessionEvents(session: SessionResponse, historyEvents: TerminalEvent[]): TerminalEvent[] {
  const hasUserMessage = historyEvents.some((event) => event.type === 'user_message');
  if (hasUserMessage || !session.task) {
    return historyEvents;
  }
  return [
    {
      type: 'user_message',
      data: { text: session.task },
      timestamp: session.created_at ?? new Date().toISOString(),
      sequence: 0,
    },
    ...historyEvents,
  ];
}

export function extractFilePaths(toolInput: unknown): string[] {
  if (!toolInput || typeof toolInput !== 'object') {
    return [];
  }
  const input = toolInput as Record<string, unknown>;
  const paths: string[] = [];
  ['file_path', 'path', 'target_path', 'dest_path'].forEach((key) => {
    const value = input[key];
    if (typeof value === 'string' && isSafeRelativePath(value)) {
      paths.push(value);
    }
  });
  return paths;
}

export function formatToolInput(input: unknown): string {
  let obj: unknown = input;

  // If input is a string, try to parse it as JSON
  if (typeof input === 'string') {
    try {
      obj = JSON.parse(input);
    } catch {
      // Not valid JSON, return the string as-is
      return input;
    }
  }

  // If it's an object, return as JSON string for YAML conversion
  if (typeof obj === 'object' && obj !== null) {
    return JSON.stringify(obj);
  }

  return String(input);
}

export function formatToolName(name: string): string {
  // Handle double underscore prefix (mcp__ag3ntum__Bash -> Ag3ntumBash)
  if (name.startsWith('mcp__ag3ntum__')) {
    const suffix = name.slice('mcp__ag3ntum__'.length);
    return `Ag3ntum${suffix}`;
  }
  // Handle single underscore prefix (legacy: mcp_ag3ntum_bash -> Ag3ntumBash)
  if (name.startsWith('mcp_ag3ntum_')) {
    const suffix = name.slice('mcp_ag3ntum_'.length);
    const capitalized = suffix
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join('');
    return `Ag3ntum${capitalized}`;
  }
  return name;
}

export function getStatusLabel(status?: string): string {
  if (!status) {
    return '';
  }
  return STATUS_LABELS[status] ?? status;
}

export function extractTodos(toolCalls: ToolCallView[]): TodoItem[] | null {
  const todoTool = [...toolCalls].reverse().find((tool) => tool.tool === 'TodoWrite' && tool.input);
  if (!todoTool) {
    return null;
  }

  let input: unknown = todoTool.input;
  if (typeof input === 'string') {
    try {
      input = JSON.parse(input);
    } catch {
      return null;
    }
  }

  if (!input || typeof input !== 'object') {
    return null;
  }

  const rawTodos = (input as { todos?: unknown }).todos;
  if (!Array.isArray(rawTodos)) {
    return null;
  }

  return rawTodos
    .map((todo) => {
      if (!todo || typeof todo !== 'object') {
        return null;
      }
      const item = todo as { content?: unknown; status?: unknown; activeForm?: unknown };
      if (typeof item.content !== 'string' || typeof item.status !== 'string') {
        return null;
      }
      return {
        content: item.content,
        status: item.status,
        activeForm: typeof item.activeForm === 'string' ? item.activeForm : undefined,
      };
    })
    .filter((item): item is TodoItem => Boolean(item));
}

// Convert Python repr format to JSON string
export function pythonReprToJson(input: string): string {
  // Convert Python repr to JSON:
  // 1. Protect escaped single quotes (\') with placeholder - they become unescaped ' in JSON
  // 2. Escape existing double quotes (they'll be inside double-quoted JSON strings)
  // 3. Convert single quotes to double quotes
  // 4. Restore protected single quotes (no escaping needed in double-quoted strings)
  const SQ_PLACEHOLDER = '\x00SQ\x00';
  return input
    .replace(/\\'/g, SQ_PLACEHOLDER)  // Protect escaped single quotes
    .replace(/"/g, '\\"')             // Escape existing double quotes
    .replace(/'/g, '"')               // Convert single to double quotes
    .replace(new RegExp(SQ_PLACEHOLDER, 'g'), "'")  // Restore as unescaped single quotes
    .replace(/\bTrue\b/g, 'true')     // Python True -> JSON true
    .replace(/\bFalse\b/g, 'false')   // Python False -> JSON false
    .replace(/\bNone\b/g, 'null');    // Python None -> JSON null
}

// Convert text to YAML if it looks like JSON or Python repr
export function formatOutputAsYaml(output: string): { formatted: string; isYaml: boolean } {
  const trimmed = output.trim();
  // Check if it looks like JSON/Python dict/list (starts with { or [)
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
    return { formatted: output, isYaml: false };
  }

  // Check if output appears truncated (doesn't end with proper closing)
  const isTruncated = !trimmed.endsWith('}') && !trimmed.endsWith(']') &&
                      !trimmed.endsWith('}\n') && !trimmed.endsWith(']\n');

  // Try parsing as JSON first
  try {
    const parsed = JSON.parse(trimmed);
    const yamlStr = YAML.stringify(parsed, { indent: 2, lineWidth: 120 });
    return { formatted: yamlStr, isYaml: true };
  } catch {
    // Not valid JSON, continue to Python repr conversion
  }

  // Try converting Python repr format to JSON
  try {
    const jsonLike = pythonReprToJson(trimmed);
    const parsed = JSON.parse(jsonLike);
    const yamlStr = YAML.stringify(parsed, { indent: 2, lineWidth: 120 });
    return { formatted: yamlStr, isYaml: true };
  } catch {
    // Parsing failed - if truncated, try to complete the structure
    if (isTruncated) {
      // Try to fix truncated structure by adding closing brackets
      const jsonLike = pythonReprToJson(trimmed);
      // Count open/close brackets to determine what's missing
      let openBrackets = 0;
      let openBraces = 0;
      for (const char of jsonLike) {
        if (char === '[') openBrackets++;
        else if (char === ']') openBrackets--;
        else if (char === '{') openBraces++;
        else if (char === '}') openBraces--;
      }
      // Try to close the structure
      let fixedJson = jsonLike;
      // If we're in a string, close it
      const quoteCount = (jsonLike.match(/(?<!\\)"/g) || []).length;
      if (quoteCount % 2 !== 0) {
        fixedJson += '"';
      }
      // Add missing closing brackets
      fixedJson += '}'.repeat(Math.max(0, openBraces));
      fixedJson += ']'.repeat(Math.max(0, openBrackets));

      try {
        const parsed = JSON.parse(fixedJson);
        const yamlStr = YAML.stringify(parsed, { indent: 2, lineWidth: 120 });
        return { formatted: yamlStr + '\n... (truncated)', isYaml: true };
      } catch {
        // Still can't parse, return as-is
      }
    }

    // Final fallback: return as-is
    return { formatted: output, isYaml: false };
  }
}

// Strip <resume-context>...</resume-context> from display (LLM-only content)
export function stripResumeContext(text: string): string {
  return text.replace(/<resume-context>[\s\S]*?<\/resume-context>\s*/g, '').trim();
}

// Copy to clipboard utilities
export async function copyAsRichText(element: HTMLElement): Promise<boolean> {
  try {
    const html = element.innerHTML;
    const text = element.innerText;

    const htmlBlob = new Blob([html], { type: 'text/html' });
    const textBlob = new Blob([text], { type: 'text/plain' });

    const clipboardItem = new ClipboardItem({
      'text/html': htmlBlob,
      'text/plain': textBlob,
    });

    await navigator.clipboard.write([clipboardItem]);
    return true;
  } catch (err) {
    console.error('Failed to copy rich text:', err);
    return false;
  }
}

export async function copyAsMarkdown(markdown: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(markdown);
    return true;
  } catch (err) {
    console.error('Failed to copy markdown:', err);
    return false;
  }
}
