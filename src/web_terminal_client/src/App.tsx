import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import YAML from 'yaml';

import {
  cancelSession,
  continueTask,
  getConfig,
  getSession,
  getSessionEvents,
  listSessions,
  runTask,
} from './api';
import { AuthProvider, useAuth } from './AuthContext';
import { loadConfig } from './config';
import { FileExplorer } from './FileExplorer';
import { ProtectedRoute } from './ProtectedRoute';
import { connectSSE } from './sse';
import type { AppConfig, SessionResponse, TerminalEvent } from './types';

type ResultStatus = 'complete' | 'partial' | 'failed' | 'running' | 'cancelled';

type ConversationItem =
  | {
      type: 'user';
      id: string;
      time: string;
      content: string;
    }
  | {
      type: 'agent_message';
      id: string;
      time: string;
      content: string;
      toolCalls: ToolCallView[];
      subagents: SubagentView[];
      status?: ResultStatus;
      comments?: string;
      files?: string[];
      structuredStatus?: ResultStatus;
      structuredError?: string;
      structuredFields?: Record<string, string>;
      isStreaming?: boolean;
    }
  | {
      type: 'output';
      id: string;
      time: string;
      output: string;
      comments?: string;
      files: string[];
      status: ResultStatus;
      error?: string;
    };

type ToolCallView = {
  id: string;
  tool: string;
  time: string;
  status: 'running' | 'complete' | 'failed';
  durationMs?: number;
  input?: unknown;
  output?: string;
  outputTruncated?: boolean;
  outputLineCount?: number;
  thinking?: string;
  error?: string;
  suggestion?: string;
};

type SystemEventView = {
  id: string;
  time: string;
  eventType: 'permission_denied' | 'hook_triggered' | 'profile_switch';
  toolName?: string;
  decision?: string;
  message?: string;
  profileName?: string;
};

type SubagentView = {
  id: string;
  taskId: string;
  name: string;
  time: string;
  status: 'running' | 'complete' | 'failed';
  durationMs?: number;
  promptPreview?: string;
  resultPreview?: string;
  messageBuffer?: string;
};

type TodoItem = {
  content: string;
  status: string;
  activeForm?: string;
};

const STATUS_LABELS: Record<string, string> = {
  idle: 'Idle',
  running: 'Running',
  complete: 'Complete',
  partial: 'Partial',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

// Session ID validation: must match backend pattern YYYYMMDD_HHMMSS_8hexchars
// Defense in depth - validates before API calls and URL navigation
const SESSION_ID_PATTERN = /^\d{8}_\d{6}_[a-f0-9]{8}$/;

function isValidSessionId(sessionId: string | undefined | null): sessionId is string {
  if (!sessionId) return false;
  if (sessionId.length > 24) return false;
  return SESSION_ID_PATTERN.test(sessionId);
}

const STATUS_CLASS: Record<string, string> = {
  idle: 'status-idle',
  running: 'status-running',
  complete: 'status-complete',
  partial: 'status-partial',
  failed: 'status-failed',
  cancelled: 'status-cancelled',
};

const EMPTY_EVENTS: TerminalEvent[] = [];

const TOOL_COLOR_CLASS: Record<string, string> = {
  Read: 'tool-read',
  Bash: 'tool-bash',
  Write: 'tool-write',
  WebFetch: 'tool-webfetch',
  Output: 'tool-output',
  Think: 'tool-think',
};

const TOOL_SYMBOL: Record<string, string> = {
  Read: '◉',
  Bash: '▶',
  Write: '✎',
  WebFetch: '⬡',
  Output: '◈',
  Think: '◇',
};

// Copy to clipboard utilities
async function copyAsRichText(element: HTMLElement): Promise<boolean> {
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

async function copyAsMarkdown(markdown: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(markdown);
    return true;
  } catch (err) {
    console.error('Failed to copy markdown:', err);
    return false;
  }
}

function CopyButtons({
  contentRef,
  markdown,
  className = '',
}: {
  contentRef: React.RefObject<HTMLElement | null>;
  markdown: string;
  className?: string;
}): JSX.Element {
  const [copiedRich, setCopiedRich] = useState(false);
  const [copiedMd, setCopiedMd] = useState(false);

  const handleCopyRich = async () => {
    if (contentRef.current) {
      const success = await copyAsRichText(contentRef.current);
      if (success) {
        setCopiedRich(true);
        setTimeout(() => setCopiedRich(false), 1500);
      }
    }
  };

  const handleCopyMd = async () => {
    const success = await copyAsMarkdown(markdown);
    if (success) {
      setCopiedMd(true);
      setTimeout(() => setCopiedMd(false), 1500);
    }
  };

  return (
    <div className={`copy-buttons ${className}`}>
      <button
        type="button"
        className={`filter-button ${copiedRich ? 'copied' : ''}`}
        onClick={handleCopyRich}
        title="Copy as rich text (with formatting)"
      >
        {copiedRich ? '[✓ rich]' : '[rich]'}
      </button>
      <button
        type="button"
        className={`filter-button ${copiedMd ? 'copied' : ''}`}
        onClick={handleCopyMd}
        title="Copy as markdown"
      >
        {copiedMd ? '[✓ md]' : '[md]'}
      </button>
    </div>
  );
}

const OUTPUT_STATUS_CLASS: Record<string, string> = {
  complete: 'output-status-complete',
  partial: 'output-status-partial',
  failed: 'output-status-failed',
  running: 'output-status-running',
  cancelled: 'output-status-cancelled',
};

function normalizeStatus(value: string): string {
  const statusValue = value.toLowerCase();
  if (statusValue === 'completed' || statusValue === 'complete') {
    return 'complete';
  }
  if (statusValue === 'failed' || statusValue === 'error') {
    return 'failed';
  }
  if (statusValue === 'cancelled' || statusValue === 'canceled') {
    return 'cancelled';
  }
  if (statusValue === 'running') {
    return 'running';
  }
  if (statusValue === 'partial') {
    return 'partial';
  }
  return statusValue || 'idle';
}

type StructuredMessage = {
  body: string;
  fields: Record<string, string>;
  status?: ResultStatus;
  error?: string;
};

function coerceStructuredFields(value: unknown): Record<string, string> | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) {
    return null;
  }
  const fields: Record<string, string> = {};
  entries.forEach(([key, fieldValue]) => {
    if (typeof fieldValue === 'string') {
      fields[key.toLowerCase()] = fieldValue;
    }
  });
  return Object.keys(fields).length > 0 ? fields : null;
}

function parseStructuredMessage(text: string): StructuredMessage {
  if (!text) {
    return { body: text, fields: {} };
  }

  const lines = text.split('\n');
  const isFenced = lines[0]?.trim().startsWith('```');
  const startIndex = isFenced ? 1 : 0;
  if (lines.length < startIndex + 3 || lines[startIndex]?.trim() !== '---') {
    return { body: text, fields: {} };
  }

  let endIndex = -1;
  for (let i = startIndex + 1; i < lines.length; i += 1) {
    if (lines[i].trim() === '---') {
      endIndex = i;
      break;
    }
  }
  if (endIndex === -1) {
    return { body: text, fields: {} };
  }

  const fields: Record<string, string> = {};
  lines.slice(startIndex + 1, endIndex).forEach((line) => {
    if (!line.trim()) {
      return;
    }
    const separatorIndex = line.indexOf(':');
    if (separatorIndex === -1) {
      return;
    }
    const key = line.slice(0, separatorIndex).trim().toLowerCase();
    const value = line.slice(separatorIndex + 1).trim();
    if (key) {
      fields[key] = value;
    }
  });

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

  const statusRaw = fields.status;
  const status = statusRaw ? (normalizeStatus(statusRaw) as ResultStatus) : undefined;
  const error = fields.error ?? undefined;

  return { body, fields, status, error };
}

function formatDuration(durationMs?: number | null): string {
  if (!durationMs) {
    return '0.0s';
  }
  return durationMs < 1000
    ? `${durationMs}ms`
    : `${(durationMs / 1000).toFixed(1)}s`;
}

// Extract text preview from subagent result (handles JSON array format with {type: 'text', text: '...'})
function extractSubagentPreview(rawText: string): string {
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

function formatCost(cost?: number | null): string {
  if (cost === null || cost === undefined) {
    return '$0.0000';
  }
  return `$${cost.toFixed(4)}`;
}

function formatTimestamp(timestamp?: string): string {
  if (!timestamp) {
    return '--:--:--';
  }
  const date = new Date(timestamp);
  return date.toLocaleTimeString('en-US', { hour12: false });
}

function renderMarkdown(text: string): JSX.Element[] {
  const lines = text.split('\n');
  const elements: JSX.Element[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];

  const isTableSeparator = (line: string): boolean => {
    const trimmed = line.trim();
    if (!trimmed) {
      return false;
    }
    const normalized = trimmed.startsWith('|') ? trimmed : `|${trimmed}`;
    return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(normalized);
  };

  const splitTableRow = (line: string): string[] => {
    const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    return trimmed.split('|').map((cell) => cell.trim());
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <pre key={`code-${i}`} className="md-code-block">
            {codeLines.join('\n')}
          </pre>
        );
        codeLines = [];
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    let element: JSX.Element;

    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headerCells = splitTableRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|')) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      i -= 1;

      element = (
        <table key={`table-${i}`} className="md-table">
          <thead>
            <tr>
              {headerCells.map((cell, idx) => (
                <th key={`th-${idx}`}>{renderInlineMarkdown(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`tr-${rowIndex}`}>
                {row.map((cell, cellIndex) => (
                  <td key={`td-${rowIndex}-${cellIndex}`}>{renderInlineMarkdown(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
      elements.push(element);
      continue;
    }

    if (line.startsWith('### ')) {
      element = (
        <div key={i} className="md-h3">
          {renderInlineMarkdown(line.slice(4))}
        </div>
      );
    } else if (line.startsWith('## ')) {
      element = (
        <div key={i} className="md-h2">
          {renderInlineMarkdown(line.slice(3))}
        </div>
      );
    } else if (line.startsWith('# ')) {
      element = (
        <div key={i} className="md-h1">
          {renderInlineMarkdown(line.slice(2))}
        </div>
      );
    } else if (/^[-—─]{3,}$/.test(line.trim())) {
      element = <hr key={i} className="md-hr" />;
    } else if (line.trimStart().startsWith('- ') || line.trimStart().startsWith('* ')) {
      const indent = line.length - line.trimStart().length;
      element = (
        <div key={i} className="md-li" style={{ marginLeft: indent * 4 }}>
          • {renderInlineMarkdown(line.trimStart().slice(2))}
        </div>
      );
    } else if (/^\s*\d+\.\s/.test(line)) {
      const match = line.match(/^(\s*)(\d+)\.\s(.*)$/);
      if (match) {
        const [, spaces, num, content] = match;
        element = (
          <div key={i} className="md-li" style={{ marginLeft: (spaces?.length ?? 0) * 4 }}>
            {num}. {renderInlineMarkdown(content)}
          </div>
        );
      } else {
        element = <div key={i}>{renderInlineMarkdown(line)}</div>;
      }
    } else if (line.trim() === '') {
      element = <div key={i} className="md-spacer" />;
    } else {
      element = <div key={i}>{renderInlineMarkdown(line)}</div>;
    }

    elements.push(element);
  }

  if (inCodeBlock && codeLines.length > 0) {
    elements.push(
      <pre key="code-final" className="md-code-block">
        {codeLines.join('\n')}
      </pre>
    );
  }

  return elements;
}

function renderInlineMarkdown(text: string): (string | JSX.Element)[] {
  const result: (string | JSX.Element)[] = [];
  let key = 0;

  const regex = /(!\[(.*?)\]\(([^)]+)\)|\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      result.push(text.slice(lastIndex, match.index));
    }

    const [
      fullMatch,
      ,
      imageAlt,
      imageUrl,
      bold,
      italic,
      code,
      linkText,
      linkUrl,
    ] = match;

    if (imageUrl) {
      result.push(
        <img
          key={key++}
          src={imageUrl}
          alt={imageAlt ?? ''}
          className="md-image"
        />
      );
    } else if (bold) {
      result.push(
        <strong key={key++} className="md-bold">
          {bold}
        </strong>
      );
    } else if (italic) {
      result.push(
        <em key={key++} className="md-italic">
          {italic}
        </em>
      );
    } else if (code) {
      result.push(
        <code key={key++} className="md-code">
          {code}
        </code>
      );
    } else if (linkText && linkUrl) {
      result.push(
        <a key={key++} href={linkUrl} className="md-link" target="_blank" rel="noopener noreferrer">
          {linkText}
        </a>
      );
    }

    lastIndex = match.index + fullMatch.length;
  }

  if (lastIndex < text.length) {
    result.push(text.slice(lastIndex));
  }

  return result.length > 0 ? result : [text];
}

function isSafeRelativePath(path: string): boolean {
  return Boolean(path && !path.startsWith('/') && !path.startsWith('~') && !path.includes('..'));
}

function getLastServerSequence(events: TerminalEvent[]): number | null {
  const sequences = events
    .filter((event) => event.type !== 'user_message' && Number.isFinite(event.sequence))
    .map((event) => event.sequence);
  if (sequences.length === 0) {
    return null;
  }
  return Math.max(...sequences);
}

function seedSessionEvents(session: SessionResponse, historyEvents: TerminalEvent[]): TerminalEvent[] {
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

function extractFilePaths(toolInput: unknown): string[] {
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

function formatToolInput(input: unknown): string {
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

function formatToolName(name: string): string {
  // Handle double underscore prefix (mcp__ag3ntum__WriteOutput -> Ag3ntumWriteOutput)
  if (name.startsWith('mcp__ag3ntum__')) {
    const suffix = name.slice('mcp__ag3ntum__'.length);
    return `Ag3ntum${suffix}`;
  }
  // Handle single underscore prefix (legacy: mcp_ag3ntum_write_output -> Ag3ntumWriteOutput)
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

function getStatusLabel(status?: string): string {
  if (!status) {
    return '';
  }
  return STATUS_LABELS[status] ?? status;
}

function extractTodos(toolCalls: ToolCallView[]): TodoItem[] | null {
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

// Convert text to YAML if it looks like JSON or Python repr
function formatOutputAsYaml(output: string): { formatted: string; isYaml: boolean } {
  const trimmed = output.trim();
  // Check if it looks like JSON/Python dict/list (starts with { or [)
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
    return { formatted: output, isYaml: false };
  }
  try {
    const parsed = JSON.parse(trimmed);
    const yamlStr = YAML.stringify(parsed, { indent: 2, lineWidth: 120 });
    return { formatted: yamlStr, isYaml: true };
  } catch {
    // Try converting Python repr format (single quotes) to JSON (double quotes)
    try {
      // Replace single quotes with double quotes, handling escaped quotes
      // This handles Python's repr() output format
      const jsonLike = trimmed
        .replace(/'/g, '"')           // Convert single to double quotes
        .replace(/True/g, 'true')     // Python True -> JSON true
        .replace(/False/g, 'false')   // Python False -> JSON false
        .replace(/None/g, 'null');    // Python None -> JSON null
      const parsed = JSON.parse(jsonLike);
      const yamlStr = YAML.stringify(parsed, { indent: 2, lineWidth: 120 });
      return { formatted: yamlStr, isYaml: true };
    } catch {
      // Not valid JSON or Python repr, return as-is
      return { formatted: output, isYaml: false };
    }
  }
}

// Collapsible output component with first N lines visible
const COLLAPSED_LINE_COUNT = 10;

function CollapsibleOutput({
  output,
  className
}: {
  output: string;
  className?: string;
}): JSX.Element {
  const [isExpanded, setIsExpanded] = useState(false);

  const { formatted, isYaml } = useMemo(() => formatOutputAsYaml(output), [output]);
  const lines = formatted.split('\n');
  const totalLines = lines.length;
  const needsCollapse = totalLines > COLLAPSED_LINE_COUNT;

  const displayedContent = useMemo(() => {
    if (!needsCollapse || isExpanded) {
      return formatted;
    }
    return lines.slice(0, COLLAPSED_LINE_COUNT).join('\n');
  }, [formatted, lines, needsCollapse, isExpanded]);

  const formatBadge = isYaml ? ' · YAML' : '';

  return (
    <div className={`collapsible-output ${className || ''}`}>
      <pre className="tool-section-body tool-output">
        {displayedContent}
      </pre>
      {needsCollapse && (
        <button
          className="output-expand-toggle"
          onClick={() => setIsExpanded(!isExpanded)}
          type="button"
        >
          {isExpanded
            ? `▲ Collapse${formatBadge}`
            : `▼ Expand All (${totalLines} lines)${formatBadge}`
          }
        </button>
      )}
      {!needsCollapse && isYaml && (
        <span className="output-format-badge">YAML</span>
      )}
    </div>
  );
}

function useSpinnerFrame(intervalMs: number = 80): number {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setFrame((prev) => (prev + 1) % SPINNER_FRAMES.length);
    }, intervalMs);
    return () => clearInterval(interval);
  }, [intervalMs]);

  return frame;
}

// Hook to display elapsed time since a start timestamp, updating every second
function useElapsedTime(startTime: string | null, isRunning: boolean): string {
  const [elapsed, setElapsed] = useState('');

  useEffect(() => {
    if (!isRunning || !startTime) {
      setElapsed('');
      return;
    }

    const updateElapsed = () => {
      const start = new Date(startTime).getTime();
      const now = Date.now();
      const diffMs = now - start;

      if (diffMs < 0) {
        setElapsed('0s');
        return;
      }

      const seconds = Math.floor(diffMs / 1000);
      const minutes = Math.floor(seconds / 60);
      const hours = Math.floor(minutes / 60);

      if (hours > 0) {
        setElapsed(`${hours}h ${minutes % 60}m ${seconds % 60}s`);
      } else if (minutes > 0) {
        setElapsed(`${minutes}m ${seconds % 60}s`);
      } else {
        setElapsed(`${seconds}s`);
      }
    };

    // Update immediately
    updateElapsed();

    // Then update every second
    const interval = setInterval(updateElapsed, 1000);
    return () => clearInterval(interval);
  }, [startTime, isRunning]);

  return elapsed;
}

function ToolTag({ type, count, showSymbol = true }: { type: string; count?: number; showSymbol?: boolean }): JSX.Element {
  const colorClass = TOOL_COLOR_CLASS[type] ?? 'tool-read';
  const symbol = TOOL_SYMBOL[type] ?? TOOL_SYMBOL.Read;
  const displayName = formatToolName(type);

  return (
    <span className={`tool-tag ${colorClass}`}>
      {showSymbol && <span className="tool-symbol">{symbol}</span>}
      <span className="tool-name">{displayName}</span>
      {count !== undefined && (
        <span className="tool-count">×{count}</span>
      )}
    </span>
  );
}

function SubagentTag({ name, count }: { name: string; count?: number }): JSX.Element {
  return (
    <span className="subagent-tag-stat">
      <span className="subagent-icon">◈</span>
      <span className="subagent-name">{name}</span>
      {count !== undefined && (
        <span className="subagent-count">×{count}</span>
      )}
    </span>
  );
}

function generateConversationMarkdown(conversation: ConversationItem[]): string {
  const lines: string[] = [];
  
  conversation.forEach((item) => {
    if (item.type === 'user') {
      lines.push(`## User @ ${item.time}\n`);
      lines.push(item.content);
      lines.push('\n---\n');
    } else if (item.type === 'agent_message') {
      lines.push(`## Agent @ ${item.time}\n`);
      if (item.content) {
        lines.push(item.content);
      }
      if (item.toolCalls.length > 0) {
        lines.push('\n### Tool Calls\n');
        item.toolCalls.forEach((tool) => {
          lines.push(`- **${tool.tool}** @ ${tool.time}`);
          if (tool.input) {
            lines.push(`  - Input: \`${JSON.stringify(tool.input).slice(0, 100)}...\``);
          }
        });
      }
      if (item.subagents.length > 0) {
        lines.push('\n### SubAgents\n');
        item.subagents.forEach((subagent) => {
          lines.push(`- **${subagent.name}** @ ${subagent.time} (${subagent.status})`);
        });
      }
      lines.push('\n---\n');
    } else if (item.type === 'output') {
      lines.push(`## Output @ ${item.time} [${item.status}]\n`);
      lines.push(item.output);
      if (item.error) {
        lines.push(`\n**Error:** ${item.error}`);
      }
      lines.push('\n---\n');
    }
  });
  
  return lines.join('\n');
}

function FooterCopyButtons({
  conversation,
  outputRef,
}: {
  conversation: ConversationItem[];
  outputRef: React.RefObject<HTMLDivElement | null>;
}): JSX.Element {
  const [copiedRich, setCopiedRich] = useState(false);
  const [copiedMd, setCopiedMd] = useState(false);

  const handleCopyRich = async () => {
    if (outputRef.current) {
      const success = await copyAsRichText(outputRef.current);
      if (success) {
        setCopiedRich(true);
        setTimeout(() => setCopiedRich(false), 1500);
      }
    }
  };

  const handleCopyMd = async () => {
    const markdown = generateConversationMarkdown(conversation);
    const success = await copyAsMarkdown(markdown);
    if (success) {
      setCopiedMd(true);
      setTimeout(() => setCopiedMd(false), 1500);
    }
  };

  return (
    <div className="footer-copy-buttons">
      <button
        type="button"
        className={`filter-button ${copiedRich ? 'copied' : ''}`}
        onClick={handleCopyRich}
        title="Copy entire conversation as rich text (with formatting)"
      >
        {copiedRich ? '[✓ rich]' : '[rich]'}
      </button>
      <button
        type="button"
        className={`filter-button ${copiedMd ? 'copied' : ''}`}
        onClick={handleCopyMd}
        title="Copy entire conversation as markdown"
      >
        {copiedMd ? '[✓ md]' : '[md]'}
      </button>
    </div>
  );
}

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

function AgentSpinner(): JSX.Element {
  const frame = useSpinnerFrame();

  return (
    <span className="agent-spinner">
      <span className="agent-spinner-char">{SPINNER_FRAMES[frame]}</span>
      <span className="agent-spinner-label">processing...</span>
    </span>
  );
}

function InlineStreamSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="inline-stream-spinner">{SPINNER_FRAMES[frame]}</span>;
}

function TrailingWaitSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="trailing-wait-spinner">{SPINNER_FRAMES[frame]}</span>;
}

function StatusSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="status-spinner">{SPINNER_FRAMES[frame]}</span>;
}

function TodoProgressList({
  todos,
  overallStatus,
}: {
  todos: TodoItem[];
  overallStatus: ResultStatus | undefined;
}): JSX.Element {
  const isRunning = overallStatus === 'running' || !overallStatus;
  const isCancelled = overallStatus === 'cancelled';
  const isFailed = overallStatus === 'failed';
  const isDone = !isRunning;
  const frame = useSpinnerFrame();

  return (
    <div className={`todo-progress${isDone ? ' todo-progress-done' : ''}`}>
      {todos.map((todo, index) => {
        const status = todo.status?.toLowerCase?.() ?? 'pending';
        const isActive = status === 'in_progress' && isRunning;
        const isCompleted = isDone || status === 'completed';
        const label = isActive && todo.activeForm ? todo.activeForm : todo.content;
        const showCancel = (isCancelled || isFailed) && status === 'in_progress';
        const bullet = showCancel
          ? '✗'
          : isActive
            ? SPINNER_FRAMES[frame]
            : isCompleted
              ? '✓'
              : '•';

        return (
          <div
            key={`${todo.content}-${index}`}
            className={`todo-item todo-${status}${showCancel ? ' todo-cancelled' : ''}`}
          >
            <span className="todo-bullet">
              {bullet}
            </span>
            <span
              className={`todo-text${isActive ? ' todo-active' : ''}${isCompleted ? ' todo-completed' : ''}`}
            >
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function MessageBlock({ 
  sender, 
  time, 
  content,
  rightPanelCollapsed,
  isMobile,
}: { 
  sender: string; 
  time: string; 
  content: string;
  rightPanelCollapsed: boolean;
  isMobile: boolean;
}): JSX.Element {
  const contentRef = useRef<HTMLDivElement>(null);
  
  // Match the layout of agent messages
  const showRightPanel = isMobile ? false : !rightPanelCollapsed;

  return (
    <div className={`message-block user-message ${isMobile ? 'mobile-layout' : ''} ${rightPanelCollapsed && !isMobile ? 'right-collapsed' : ''}`}>
      <div className="message-header">
        <span className="message-icon">⟩</span>
        <span className="message-sender">{sender}</span>
        <span className="message-time">@ {time}</span>
        <CopyButtons contentRef={contentRef} markdown={content} className="message-header-copy-buttons" />
      </div>
      <div className="message-body">
        <div className={`message-column-left ${!showRightPanel ? 'full-width' : ''}`}>
          <div ref={contentRef} className="message-content">{content}</div>
        </div>
        {showRightPanel && (
          <div className="message-column-right">
            {/* Empty right panel for consistent layout */}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolCallBlock({
  tool,
  expanded,
  onToggle,
  isLast,
}: {
  tool: ToolCallView;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
}): JSX.Element {
  const frame = useSpinnerFrame();
  const hasContent = Boolean(tool.thinking || tool.input || tool.output || tool.error);
  const treeChar = isLast ? '└──' : '├──';
  
  // Status icon: spinner while running, checkmark/cross when done
  const statusIcon = 
    tool.status === 'running' ? SPINNER_FRAMES[frame] :
    tool.status === 'complete' ? '✓' :
    tool.status === 'failed' ? '✗' : null;
  
  const statusClass = 
    tool.status === 'complete' ? 'tool-status-success' :
    tool.status === 'failed' ? 'tool-status-error' : 
    tool.status === 'running' ? 'tool-status-running' : '';

  // Tool-specific input preview (only while running)
  const getRunningPreview = (): string | null => {
    if (tool.status !== 'running' || !tool.input) return null;
    
    const input = tool.input as Record<string, unknown>;
    const toolName = tool.tool.toLowerCase();
    
    // Ag3ntumWebFetch - show URL
    if (toolName.includes('webfetch') || toolName.includes('fetch')) {
      const url = input.url as string | undefined;
      return url ? url.slice(0, 60) + (url.length > 60 ? '...' : '') : null;
    }
    
    // Ag3ntumBash - show first 40 chars of command
    if (toolName.includes('bash') || toolName.includes('shell')) {
      const cmd = input.command as string | undefined;
      return cmd ? cmd.split('\n')[0].slice(0, 40) + (cmd.length > 40 ? '...' : '') : null;
    }
    
    // Ag3ntumRead/Write/Edit - show file path
    if (toolName.includes('read') || toolName.includes('write') || toolName.includes('edit')) {
      const path = (input.file_path || input.path || input.file) as string | undefined;
      return path ? path.slice(0, 50) + (path.length > 50 ? '...' : '') : null;
    }
    
    // Ag3ntumGrep - show pattern
    if (toolName.includes('grep')) {
      const pattern = input.pattern as string | undefined;
      return pattern ? `/${pattern.slice(0, 30)}${pattern.length > 30 ? '...' : ''}/` : null;
    }
    
    // Ag3ntumGlob/LS - show path
    if (toolName.includes('glob') || toolName.includes('ls')) {
      const path = (input.path || input.pattern || input.directory) as string | undefined;
      return path ? path.slice(0, 50) + (path.length > 50 ? '...' : '') : null;
    }
    
    return null;
  };
  
  const runningPreview = getRunningPreview();

  return (
    <div className={`tool-call ${statusClass}`}>
      <div className="tool-call-header" onClick={hasContent ? onToggle : undefined} role="button">
        <span className="tool-tree">{treeChar}</span>
        {statusIcon && (
          <span className={`tool-status-icon ${statusClass}`}>{statusIcon}</span>
        )}
        {hasContent && !statusIcon && <span className="tool-toggle">{expanded ? '▼' : '▶'}</span>}
        {hasContent && statusIcon && <span className="tool-toggle">{expanded ? '▼' : '▶'}</span>}
        <ToolTag type={tool.tool} showSymbol={false} />
        <span className="tool-time">@ {tool.time}</span>
        {tool.status !== 'running' && tool.durationMs !== undefined && (
          <span className="tool-duration">({formatDuration(tool.durationMs)})</span>
        )}
        {!expanded && runningPreview && (
          <span className="tool-preview tool-running-preview">
            → {runningPreview}
          </span>
        )}
      </div>
      {expanded && hasContent && (
        <div className="tool-call-body">
          {tool.thinking && (
            <div className="tool-thinking">💭 {tool.thinking}</div>
          )}
          {tool.input !== undefined && tool.input !== null && (
            <div className="tool-section">
              <div className="tool-section-title">┌─ command ─────────</div>
              <CollapsibleOutput output={formatToolInput(tool.input)} />
              <div className="tool-section-title">└────────────────────</div>
            </div>
          )}
          {tool.output && (
            <div className="tool-section">
              <div className="tool-section-title">
                ┌─ output ──────────
                {tool.outputTruncated && tool.outputLineCount && (
                  <span className="output-truncated-badge">
                    ({tool.outputLineCount} lines, truncated)
                  </span>
                )}
              </div>
              <CollapsibleOutput output={tool.output} />
              <div className="tool-section-title">└────────────────────</div>
            </div>
          )}
          {tool.error && (
            <div className="tool-error">
              <div className="tool-error-title">⚠ ERROR: {tool.error}</div>
              {tool.suggestion && <div className="tool-suggestion">→ {tool.suggestion}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SubagentBlock({
  subagent,
  expanded,
  onToggle,
  isLast,
}: {
  subagent: SubagentView;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
}): JSX.Element {
  const hasContent = Boolean(subagent.promptPreview || subagent.resultPreview || subagent.messageBuffer);
  const treeChar = isLast ? '└──' : '├──';
  const isRunning = subagent.status === 'running';
  const frame = useSpinnerFrame();
  const rawPreview = subagent.resultPreview || subagent.messageBuffer || subagent.promptPreview || '';
  const previewText = extractSubagentPreview(rawPreview);

  // Status icon: spinner while running, checkmark/cross when done
  const statusIcon = 
    isRunning ? SPINNER_FRAMES[frame] :
    subagent.status === 'complete' ? '✓' :
    subagent.status === 'failed' ? '✗' : null;
  
  const statusClass = 
    subagent.status === 'complete' ? 'subagent-status-success' :
    subagent.status === 'failed' ? 'subagent-status-error' : 
    isRunning ? 'subagent-status-running' : '';

  return (
    <div className={`subagent-call ${statusClass}`}>
      <div className="subagent-call-header" onClick={hasContent ? onToggle : undefined} role="button">
        <span className="tool-tree">{treeChar}</span>
        {statusIcon && (
          <span className={`subagent-status-icon ${statusClass}`}>{statusIcon}</span>
        )}
        {hasContent && <span className="tool-toggle">{expanded ? '▼' : '▶'}</span>}
        <span className="subagent-tag">
          <span className="subagent-icon">◈</span>
          <span className="subagent-name">{subagent.name}</span>
        </span>
        <span className="tool-time">@ {subagent.time}</span>
        {!isRunning && subagent.durationMs !== undefined && (
          <span className="subagent-duration">({formatDuration(subagent.durationMs)})</span>
        )}
      </div>
      {!expanded && previewText && (
        <div className="subagent-preview">
          <span className="subagent-preview-tree">{isLast ? ' ' : '│'}</span>
          <span className="subagent-preview-text">
            {previewText.slice(0, 80)}
            {previewText.length > 80 ? '...' : ''}
          </span>
        </div>
      )}
      {expanded && hasContent && (
        <div className="subagent-call-body">
          {subagent.promptPreview && (
            <div className="tool-section">
              <div className="tool-section-title">┌─ prompt ───────────</div>
              <CollapsibleOutput output={subagent.promptPreview} />
              <div className="tool-section-title">└────────────────────</div>
            </div>
          )}
          {subagent.messageBuffer && (
            <div className="tool-section">
              <div className="tool-section-title">┌─ messages ─────────</div>
              <CollapsibleOutput output={subagent.messageBuffer} />
              <div className="tool-section-title">└────────────────────</div>
            </div>
          )}
          {subagent.resultPreview && (
            <div className="tool-section">
              <div className="tool-section-title">┌─ result ───────────</div>
              <CollapsibleOutput output={subagent.resultPreview} />
              <div className="tool-section-title">└────────────────────</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SystemEventsToggle({ 
  count, 
  deniedCount, 
  onClick 
}: { 
  count: number; 
  deniedCount: number; 
  onClick: () => void;
}): JSX.Element | null {
  if (count === 0) return null;
  
  return (
    <button 
      className={`system-events-toggle-btn ${deniedCount > 0 ? 'has-warnings' : ''}`}
      onClick={onClick}
      title="Show system events"
    >
      <span className="system-events-toggle-icon">⚙</span>
      <span className="system-events-toggle-count">{count}</span>
      {deniedCount > 0 && (
        <span className="system-events-toggle-warning">🚫{deniedCount}</span>
      )}
    </button>
  );
}

function SystemEventsPanel({ 
  events, 
  onClose 
}: { 
  events: SystemEventView[]; 
  onClose: () => void;
}): JSX.Element | null {
  if (events.length === 0) return null;
  
  const permissionDenials = events.filter(e => e.eventType === 'permission_denied');
  
  return (
    <div className="system-events-panel">
      <div 
        className="system-events-header" 
        onClick={onClose}
        role="button"
      >
        <span className="system-events-toggle">▼</span>
        <span className="system-events-icon">⚙</span>
        <span className="system-events-title">System Events ({events.length})</span>
        {permissionDenials.length > 0 && (
          <span className="system-events-badge system-events-badge-warning">
            {permissionDenials.length} denied
          </span>
        )}
        <span className="system-events-close">✕</span>
      </div>
      <div className="system-events-list">
        {events.map(event => (
          <div key={event.id} className={`system-event system-event-${event.eventType}`}>
            <span className="system-event-time">{event.time}</span>
            {event.eventType === 'permission_denied' && (
              <>
                <span className="system-event-badge-denied">🚫 DENIED</span>
                <span className="system-event-tool">{event.toolName}</span>
                {event.message && (
                  <span className="system-event-message">— {event.message.slice(0, 100)}{event.message.length > 100 ? '...' : ''}</span>
                )}
              </>
            )}
            {event.eventType === 'profile_switch' && (
              <>
                <span className="system-event-badge-info">⚙ PROFILE</span>
                <span className="system-event-profile">{event.profileName}</span>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function ResultSection({
  comments,
  commentsExpanded,
  onToggleComments,
  files,
  filesExpanded,
  onToggleFiles,
  onFileAction,
}: {
  comments?: string;
  commentsExpanded?: boolean;
  onToggleComments?: () => void;
  files?: string[];
  filesExpanded?: boolean;
  onToggleFiles?: () => void;
  onFileAction?: (filePath: string, mode: 'view' | 'download') => void;
}): JSX.Element | null {
  const hasComments = Boolean(comments);
  const hasFiles = Boolean(files && files.length > 0);

  if (!hasComments && !hasFiles) {
    return null;
  }

  return (
    <div className="result-section">
      <div className="result-title">Result</div>
      {hasComments && comments && (
        <div className="result-item">
          <div className="result-item-header" onClick={onToggleComments} role="button">
            <span className="result-tree">└──</span>
            <span className="result-toggle">{commentsExpanded ? '▼' : '▶'}</span>
            <span className="result-label">Comments</span>
            <span className="result-count">({comments.length})</span>
          </div>
          {commentsExpanded && (
            <div className="result-item-body md-container">
              {renderMarkdown(comments)}
            </div>
          )}
        </div>
      )}
      {hasFiles && files && (
        <div className="result-item">
          <div className="result-item-header" onClick={onToggleFiles} role="button">
            <span className="result-tree">└──</span>
            <span className="result-toggle">{filesExpanded ? '▼' : '▶'}</span>
            <span className="result-label">Files</span>
            <span className="result-count">({files.length})</span>
          </div>
          {filesExpanded && (
            <div className="result-item-body result-files-list">
              {files.map((file) => (
                <div key={file} className="result-file-item">
                  <span className="result-file-icon">📄</span>
                  <span className="result-file-name">{file}</span>
                  {onFileAction && (
                    <div className="result-file-actions">
                      <button
                        type="button"
                        className="result-file-action"
                        onClick={() => onFileAction(file, 'view')}
                      >
                        view
                      </button>
                      <button
                        type="button"
                        className="result-file-action"
                        onClick={() => onFileAction(file, 'download')}
                      >
                        download
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AgentMessageBlock({
  id,
  time,
  content,
  toolCalls,
  subagents,
  todos,
  toolExpanded,
  onToggleTool,
  subagentExpanded,
  onToggleSubagent,
  status,
  structuredStatus,
  structuredError,
  comments,
  commentsExpanded,
  onToggleComments,
  files,
  filesExpanded,
  onToggleFiles,
  isStreaming,
  sessionRunning,
  rightPanelCollapsed,
  isMobile,
  mobileExpanded,
  onToggleMobileExpand,
}: {
  id: string;
  time: string;
  content: string;
  toolCalls: ToolCallView[];
  subagents: SubagentView[];
  todos?: TodoItem[];
  toolExpanded: Set<string>;
  onToggleTool: (id: string) => void;
  subagentExpanded: Set<string>;
  onToggleSubagent: (id: string) => void;
  status?: string;
  structuredStatus?: ResultStatus;
  structuredError?: string;
  comments?: string;
  commentsExpanded?: boolean;
  onToggleComments?: () => void;
  files?: string[];
  filesExpanded?: boolean;
  onToggleFiles?: () => void;
  isStreaming?: boolean;
  sessionRunning?: boolean;
  rightPanelCollapsed: boolean;
  isMobile: boolean;
  mobileExpanded: boolean;
  onToggleMobileExpand: () => void;
}): JSX.Element {
  const contentRef = useRef<HTMLDivElement>(null);
  const statusClass = status ? `agent-status-${status}` : '';
  const normalizedStatus = status ? (normalizeStatus(status) as ResultStatus) : undefined;
  const isTerminalStatus = normalizedStatus && normalizedStatus !== 'running';
  const statusLabel = getStatusLabel(normalizedStatus);
  const showFailureStatus = normalizedStatus === 'failed' || normalizedStatus === 'error' || normalizedStatus === 'cancelled';
  const structuredStatusLabel = structuredStatus === 'failed' ? getStatusLabel(structuredStatus) : '';
  // Show inline spinner when streaming and no tool calls or subagents
  const showInlineSpinner = isStreaming && toolCalls.length === 0 && subagents.length === 0;
  // Show trailing wait spinner when message content is complete but session is still running
  // This indicates "more processing happening" even when tools are running (they have their own spinners too)
  const showTrailingWait = Boolean(content) && !isStreaming && sessionRunning;

  const hasRightContent = toolCalls.length > 0 || subagents.length > 0 || Boolean(comments) || Boolean(files?.length);
  
  // Determine if right panel should be shown
  // Desktop: always show unless collapsed (even if empty)
  // Mobile: only show when expanded AND has content (no point showing empty panel on mobile)
  let showRightPanel = false;
  if (isMobile) {
    showRightPanel = hasRightContent && mobileExpanded;
  } else {
    showRightPanel = !rightPanelCollapsed;
  }

  return (
    <div className={`message-block agent-message ${statusClass} ${isMobile ? 'mobile-layout' : ''} ${rightPanelCollapsed && !isMobile ? 'right-collapsed' : ''}`}>
      <div className="message-header">
        <span className="message-icon">◆</span>
        <span className="message-sender">AGENT</span>
        <span className="message-time">@ {time}</span>
        {content && <CopyButtons contentRef={contentRef} markdown={content} className="message-header-copy-buttons" />}
        {isMobile && hasRightContent && (
          <button 
            type="button" 
            className={`mobile-expand-button ${mobileExpanded ? 'expanded' : ''}`}
            onClick={onToggleMobileExpand}
            title={mobileExpanded ? 'Hide details' : 'Show details'}
          >
            {mobileExpanded ? '▲ Hide' : '▼ Details'} ({toolCalls.length + subagents.length})
          </button>
        )}
      </div>
      <div className="message-body">
        <div className={`message-column-left ${!showRightPanel ? 'full-width' : ''}`}>
          <div ref={contentRef} className="message-content md-container">
            {content ? (
              <>
                {renderMarkdown(content)}
                {showInlineSpinner && <InlineStreamSpinner />}
                {showTrailingWait && <TrailingWaitSpinner />}
              </>
            ) : null}
            {!content && !isTerminalStatus && !showInlineSpinner && <AgentSpinner />}
            {!content && isTerminalStatus && showFailureStatus && (
              <div className="agent-status-indicator">✗ {statusLabel || 'Stopped'}</div>
            )}
            {((structuredStatusLabel && structuredStatus === 'failed') || structuredError) && (
              <div className="agent-structured-meta">
                {structuredStatusLabel && structuredStatus === 'failed' && (
                  <div className="agent-structured-status">Status: {structuredStatusLabel}</div>
                )}
                {structuredError && (
                  <div className="agent-structured-error">Error: {structuredError}</div>
                )}
              </div>
            )}
            {todos && todos.length > 0 && (
              <TodoProgressList todos={todos} overallStatus={normalizedStatus} />
            )}
          </div>
        </div>
        {showRightPanel && (
          <div className={`message-column-right ${isMobile ? 'mobile-stacked' : ''}`}>
            {toolCalls.length > 0 && (
              <div className="tool-call-section">
                <div className="tool-call-title">Tool Calls ({toolCalls.length})</div>
                {toolCalls.map((tool, index) => (
                  <ToolCallBlock
                    key={tool.id}
                    tool={tool}
                    expanded={toolExpanded.has(tool.id)}
                    onToggle={() => onToggleTool(tool.id)}
                    isLast={index === toolCalls.length - 1}
                  />
                ))}
              </div>
            )}
            {subagents.length > 0 && (
              <div className="subagent-section">
                <div className="subagent-title">SubAgents ({subagents.length})</div>
                {subagents.map((subagent, index) => (
                  <SubagentBlock
                    key={subagent.id}
                    subagent={subagent}
                    expanded={subagentExpanded.has(subagent.id)}
                    onToggle={() => onToggleSubagent(subagent.id)}
                    isLast={index === subagents.length - 1}
                  />
                ))}
              </div>
            )}
            <ResultSection
              comments={comments}
              commentsExpanded={commentsExpanded}
              onToggleComments={onToggleComments}
              files={files}
              filesExpanded={filesExpanded}
              onToggleFiles={onToggleFiles}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function OutputBlock({
  id,
  time,
  output,
  comments,
  commentsExpanded,
  onToggleComments,
  files,
  filesExpanded,
  onToggleFiles,
  status,
  error,
  onFileAction,
  rightPanelCollapsed,
  isMobile,
  mobileExpanded,
  onToggleMobileExpand,
}: {
  id: string;
  time: string;
  output: string;
  comments?: string;
  commentsExpanded: boolean;
  onToggleComments: () => void;
  files: string[];
  filesExpanded: boolean;
  onToggleFiles: () => void;
  status: ResultStatus;
  error?: string;
  onFileAction: (filePath: string, mode: 'view' | 'download') => void;
  rightPanelCollapsed: boolean;
  isMobile: boolean;
  mobileExpanded: boolean;
  onToggleMobileExpand: () => void;
}): JSX.Element {
  const statusClass = OUTPUT_STATUS_CLASS[status] ?? '';
  const hasRightContent = Boolean(comments) || files.length > 0;
  
  // Determine if right panel should be shown
  // Desktop: always show unless collapsed (even if empty)
  // Mobile: only show when expanded AND has content
  let showRightPanel = false;
  if (isMobile) {
    showRightPanel = hasRightContent && mobileExpanded;
  } else {
    showRightPanel = !rightPanelCollapsed;
  }

  return (
    <div className={`message-block output-block ${statusClass} ${isMobile ? 'mobile-layout' : ''} ${rightPanelCollapsed && !isMobile ? 'right-collapsed' : ''}`}>
      <div className="message-header">
        <span className="message-icon">◆</span>
        <span className="message-sender">OUTPUT</span>
        <span className="message-time">@ {time}</span>
        {isMobile && hasRightContent && (
          <button 
            type="button" 
            className={`mobile-expand-button ${mobileExpanded ? 'expanded' : ''}`}
            onClick={onToggleMobileExpand}
            title={mobileExpanded ? 'Hide details' : 'Show details'}
          >
            {mobileExpanded ? '▲ Hide' : '▼ Details'}
          </button>
        )}
      </div>
      <div className="message-body">
        <div className={`message-column-left ${!showRightPanel ? 'full-width' : ''}`}>
          <div className="message-content md-container">
            {output
              ? (
                  <div className="output-part">
                    {renderMarkdown(output)}
                  </div>
                )
              : 'No output yet.'}
          </div>
        </div>
        {showRightPanel && (
          <div className={`message-column-right ${isMobile ? 'mobile-stacked' : ''}`}>
            <ResultSection
              comments={comments}
              commentsExpanded={commentsExpanded}
              onToggleComments={onToggleComments}
              files={files}
              filesExpanded={filesExpanded}
              onToggleFiles={onToggleFiles}
              onFileAction={onFileAction}
            />
          </div>
        )}
      </div>
      {error && <div className="output-error">{error}</div>}
    </div>
  );
}

type AttachedFile = {
  file: File;
  id: string;
};

// Models will be loaded from the API config endpoint
const DEFAULT_AVAILABLE_MODELS = [
  'claude-haiku-4-5-20251001',
  'claude-sonnet-4-5-20250929',
  'claude-opus-4-5-20251101',
];

function InputField({
  value,
  onChange,
  onSubmit,
  onCancel,
  isRunning,
  attachedFiles,
  onAttachFiles,
  onRemoveFile,
  model,
  onModelChange,
  availableModels,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  isRunning: boolean;
  attachedFiles: AttachedFile[];
  onAttachFiles: (files: File[]) => void;
  onRemoveFile: (id: string) => void;
  model: string;
  onModelChange: (model: string) => void;
  availableModels: string[];
}): JSX.Element {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounter = useRef(0);

  // Auto-focus textarea when not running, and refocus after running completes
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isRunning]);

  // Keep focus on the input area - refocus when clicking elsewhere in the app
  useEffect(() => {
    const handleWindowFocus = () => {
      if (textareaRef.current && document.activeElement !== textareaRef.current) {
        // Small delay to not interfere with intentional clicks
        setTimeout(() => {
          if (textareaRef.current && !document.activeElement?.closest('.input-shell')) {
            textareaRef.current.focus();
          }
        }, 100);
      }
    };
    window.addEventListener('focus', handleWindowFocus);
    return () => window.removeEventListener('focus', handleWindowFocus);
  }, []);

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current += 1;
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) {
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current = 0;
    setIsDragging(false);
    
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      onAttachFiles(files);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) {
      onAttachFiles(files);
    }
    e.target.value = '';
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') {
      // Shift+Enter = new line (let default behavior happen)
      if (e.shiftKey) {
        return;
      }
      // Enter or Ctrl+Enter or Cmd+Enter = send message
      e.preventDefault();
      if (!isRunning && value.trim()) {
        onSubmit();
      }
    }
  };

  // Auto-resize textarea based on content
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      // Reset height to auto to get the correct scrollHeight
      textarea.style.height = 'auto';
      // Set height to scrollHeight, capped at max-height via CSS
      textarea.style.height = `${textarea.scrollHeight}px`;
    }
  }, [value]);

  return (
    <div className="input-area">
      <div
        className={`input-shell ${isDragging ? 'input-dragging' : ''}`}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {isDragging && (
          <div className="input-drop-overlay">
            <div className="input-drop-content">
              <span className="input-drop-icon">📁</span>
              <span className="input-drop-text">Drop files here</span>
            </div>
          </div>
        )}

        {attachedFiles.length > 0 && (
          <div className="attached-files">
            {attachedFiles.map((item) => (
              <div key={item.id} className="attached-file">
                <span className="attached-file-icon">📄</span>
                <span className="attached-file-name" title={item.file.name}>
                  {item.file.name.length > 24
                    ? `${item.file.name.slice(0, 20)}...${item.file.name.slice(-4)}`
                    : item.file.name}
                </span>
                <span className="attached-file-size">{formatFileSize(item.file.size)}</span>
                <button
                  type="button"
                  className="attached-file-remove"
                  onClick={() => onRemoveFile(item.id)}
                  title="Remove file"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="input-main">
          <span className="input-prompt">⟩</span>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter your request... (Shift+Enter for new line)"
            className="input-textarea"
            rows={2}
          />
        </div>

        <div className="input-footer">
          <button
            type="button"
            className="filter-button"
            onClick={() => fileInputRef.current?.click()}
            title="Attach files"
          >
            {attachedFiles.length > 0 ? `[Attach (${attachedFiles.length})]` : '[Attach]'}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFileSelect}
            style={{ display: 'none' }}
          />

          <div className="input-spacer" />

          <div className="dropdown input-model-dropdown">
            <span className="dropdown-value">
              {model.replace('claude-', '').replace(/-\d{8}$/, '')}
            </span>
            <span className="dropdown-icon">▾</span>
            <div className="dropdown-list">
              {availableModels.map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`dropdown-item ${m === model ? 'active' : ''}`}
                  onClick={() => onModelChange(m)}
                >
                  {m.replace('claude-', '').replace(/-\d{8}$/, '')}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="input-actions">
        {isRunning ? (
          <button className="filter-button" type="button" onClick={onCancel} title="Cancel (Esc)">
            [Stop]
          </button>
        ) : (
          <button
            className="filter-button"
            type="button"
            onClick={onSubmit}
            disabled={!value.trim()}
            title="Send (Enter)"
          >
            [Send]
          </button>
        )}
      </div>
    </div>
  );
}

function StatusFooter({
  isRunning,
  statusLabel,
  statusClass,
  stats,
  connected,
  startTime,
}: {
  isRunning: boolean;
  statusLabel: string;
  statusClass: string;
  stats: {
    turns: number;
    tokensIn: number;
    tokensOut: number;
    cost: number;
    durationMs: number;
  };
  connected: boolean;
  startTime: string | null;
}): JSX.Element {
  const elapsedTime = useElapsedTime(startTime, isRunning);

  return (
    <div className="terminal-status">
      <div className="status-left">
        <span className={`status-connection ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? '🟢 Connected' : '🔴 Disconnected'}
        </span>
        <span className="status-divider">│</span>
        <span className={`status-state ${statusClass}`}>
          {isRunning ? (
            <>
              <StatusSpinner /> Running...{elapsedTime && ` (${elapsedTime})`}
            </>
          ) : (
            <>
              {statusLabel === 'Idle' && '● Idle'}
              {statusLabel === 'Cancelled' && '✗ Cancelled'}
              {statusLabel === 'Failed' && '✗ Failed'}
              {statusLabel !== 'Idle' && statusLabel !== 'Cancelled' && statusLabel !== 'Failed' && statusLabel}
            </>
          )}
        </span>
      </div>
      <div className="status-right">
        <span className="status-metric">Turns: <strong>{stats.turns}</strong></span>
        <span className="status-metric">Tokens: <strong>{stats.tokensIn}</strong> in / <strong>{stats.tokensOut}</strong> out</span>
        <span className="status-metric cost">${stats.cost.toFixed(4)}</span>
        <span className="status-metric">{formatDuration(stats.durationMs)}</span>
      </div>
    </div>
  );
}

// Cookie/localStorage helpers for panel preference
function getStoredPanelCollapsed(): boolean {
  try {
    const stored = localStorage.getItem('ag3ntum_right_panel_collapsed');
    return stored === 'true';
  } catch {
    return false;
  }
}

function setStoredPanelCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem('ag3ntum_right_panel_collapsed', collapsed ? 'true' : 'false');
  } catch {
    // Ignore storage errors
  }
}

// Detect mobile viewport
function useIsMobile(breakpoint: number = 768): boolean {
  const [isMobile, setIsMobile] = useState(() => 
    typeof window !== 'undefined' && window.innerWidth < breakpoint
  );

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < breakpoint);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [breakpoint]);

  return isMobile;
}

interface AppProps {
  initialSessionId?: string;
}

function App({ initialSessionId }: AppProps): JSX.Element {
  const { token, user, logout } = useAuth();
  const navigate = useNavigate();
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionResponse | null>(null);
  const [events, setEvents] = useState<TerminalEvent[]>(EMPTY_EVENTS);
  const [inputValue, setInputValue] = useState('');
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [expandedSubagents, setExpandedSubagents] = useState<Set<string>>(new Set());
  const [expandedComments, setExpandedComments] = useState<Set<string>>(new Set());
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>(DEFAULT_AVAILABLE_MODELS);
  const [selectedModel, setSelectedModel] = useState<string>(DEFAULT_AVAILABLE_MODELS[0]);
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState<boolean>(() => getStoredPanelCollapsed());
  const [mobileExpandedMessages, setMobileExpandedMessages] = useState<Set<string>>(new Set());
  const [systemEventsExpanded, setSystemEventsExpanded] = useState(false);
  const [fileExplorerVisible, setFileExplorerVisible] = useState(false);
  const [fileExplorerRefreshKey, setFileExplorerRefreshKey] = useState(0);
  const [showHiddenFiles, setShowHiddenFiles] = useState(false);
  const [stats, setStats] = useState({
    turns: 0,
    cost: 0,
    durationMs: 0,
    tokensIn: 0,
    tokensOut: 0,
    model: '',
  });
  const [runningStartTime, setRunningStartTime] = useState<string | null>(null);

  const isMobile = useIsMobile();

  const outputRef = useRef<HTMLDivElement | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const activeTurnRef = useRef(0);

  const isRunning = status === 'running';
  const statusLabel = STATUS_LABELS[status] ?? STATUS_LABELS.idle;
  const statusClass = STATUS_CLASS[status] ?? STATUS_CLASS.idle;

  useEffect(() => {
    loadConfig().then(setConfig).catch(() => setConfig(null));
  }, []);

  // Load available models from API config
  useEffect(() => {
    if (!config) {
      return;
    }
    getConfig(config.api.base_url)
      .then((apiConfig) => {
        setAvailableModels(apiConfig.models_available);
        setSelectedModel(apiConfig.default_model);
      })
      .catch((err) => {
        console.error('Failed to load API config:', err);
        // Keep defaults on error
      });
  }, [config]);

  const refreshSessions = useCallback(() => {
    if (!config || !token) {
      return;
    }

    listSessions(config.api.base_url, token)
      .then((response) => setSessions(response.sessions))
      .catch((err) => setError(`Failed to load sessions: ${err.message}`));
  }, [config, token]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  // Load session from URL on mount
  useEffect(() => {
    if (isValidSessionId(initialSessionId) && config && token && !currentSession) {
      handleSelectSession(initialSessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId, config, token]);

  useEffect(() => {
    return () => {
      if (cleanupRef.current) {
        cleanupRef.current();
      }
    };
  }, []);

  const appendEvent = useCallback(
    (event: TerminalEvent) => {
      setEvents((prev) => {
        if (event.type === 'user_message') {
          const last = prev[prev.length - 1];
          const lastText = (last?.data as { text?: unknown } | undefined)?.text;
          const nextText = (event.data as { text?: unknown } | undefined)?.text;
          if (last?.type === 'user_message' && lastText === nextText) {
            return prev;
          }
        }
        const next = [...prev, event];
        const maxLines = config?.ui.max_output_lines ?? 1000;
        if (next.length > maxLines) {
          return next.slice(-maxLines);
        }
        return next;
      });
    },
    [config]
  );

  const syncSessionEvents = useCallback(
    async (sessionId: string, sessionOverride?: SessionResponse) => {
      if (!config || !token) {
        return;
      }
      const session = sessionOverride ?? (await getSession(config.api.base_url, token, sessionId));
      const historyEvents = await getSessionEvents(config.api.base_url, token, sessionId);
      setEvents(seedSessionEvents(session, historyEvents));
      return { session, historyEvents };
    },
    [config, token]
  );

  const handleEvent = useCallback(
    (event: TerminalEvent) => {
      let enriched = event;

      if (event.type === 'conversation_turn') {
        const turnNumber = Number(event.data.turn_number ?? 0);
        activeTurnRef.current = turnNumber;
      }

      if (event.type === 'tool_start') {
        enriched = {
          ...event,
          meta: {
            ...(event.meta ?? {}),
            turn: activeTurnRef.current,
          },
        };
      }

      appendEvent(enriched);

      if (event.type === 'agent_start') {
        setStatus('running');
        setRunningStartTime(new Date().toISOString());
        setError(null);
        // Auto-collapse File Explorer when request processing starts to prevent UI blinking
        setFileExplorerVisible(false);
        const eventSessionId = String(event.data.session_id ?? '');
        setCurrentSession((prev) => ({
          id: prev?.id || eventSessionId || 'unknown',
          status: 'running',
          task: (event.data.task as string | undefined) ?? prev?.task,
          model: (event.data.model as string | undefined) ?? prev?.model,
          created_at: prev?.created_at ?? new Date().toISOString(),
          updated_at: new Date().toISOString(),
          completed_at: prev?.completed_at ?? null,
          num_turns: prev?.num_turns ?? 0,
          duration_ms: prev?.duration_ms ?? null,
          total_cost_usd: prev?.total_cost_usd ?? null,
          cancel_requested: prev?.cancel_requested ?? false,
        }));
        setStats((prev) => ({
          ...prev,
          model: String(event.data.model ?? prev.model ?? ''),
        }));
      }

      if (event.type === 'agent_complete') {
        const normalizedStatus = normalizeStatus(String(event.data.status ?? 'complete'));
        const usage = event.data.usage as {
          input_tokens?: number;
          output_tokens?: number;
          cache_creation_input_tokens?: number;
          cache_read_input_tokens?: number;
        } | undefined;
        const newTokensIn = usage
          ? (usage.input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0)
          : 0;
        const newTokensOut = usage?.output_tokens ?? 0;
        const cumulativeTurns = Number(event.data.cumulative_turns ?? event.data.num_turns ?? 0);
        const cumulativeCost = Number(event.data.cumulative_cost_usd ?? event.data.total_cost_usd ?? 0);

        setStats((prev) => ({
          ...prev,
          turns: cumulativeTurns || prev.turns + Number(event.data.num_turns ?? 0),
          durationMs: prev.durationMs + Number(event.data.duration_ms ?? 0),
          cost: event.data.total_cost_usd !== undefined
            ? cumulativeCost || Number(event.data.total_cost_usd ?? 0)
            : prev.cost,
          tokensIn: usage ? newTokensIn : prev.tokensIn,
          tokensOut: usage ? newTokensOut : prev.tokensOut,
        }));
        setStatus(normalizedStatus);
        setRunningStartTime(null);

        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: normalizedStatus,
                completed_at: new Date().toISOString(),
                num_turns: prev.num_turns + Number(event.data.num_turns ?? 0),
              }
            : null
        );
        setSessions((prev) =>
          prev.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: normalizedStatus }
              : session
          )
        );

        refreshSessions();
        if (currentSession) {
          void syncSessionEvents(currentSession.id, currentSession);
        }
      }

      if (event.type === 'cancelled') {
        setStatus('cancelled');
        // Check if session is resumable (has resume_id established)
        const resumable = Boolean(event.data?.resumable);
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'cancelled',
                completed_at: new Date().toISOString(),
                resumable,
              }
            : null
        );
        refreshSessions();
        if (currentSession) {
          void syncSessionEvents(currentSession.id, currentSession);
        }
      }

      if (event.type === 'metrics_update') {
        setStats((prev) => ({
          ...prev,
          turns: Number(event.data.turns ?? prev.turns),
          tokensIn: Number(event.data.tokens_in ?? prev.tokensIn),
          tokensOut: Number(event.data.tokens_out ?? prev.tokensOut),
          cost: event.data.total_cost_usd !== undefined ? Number(event.data.total_cost_usd) : prev.cost,
          model: String(event.data.model ?? prev.model ?? ''),
        }));
      }

      if (event.type === 'error') {
        setStatus('failed');
        setError(String(event.data.message ?? 'Unknown error'));
        // Update session status so next submit can continue rather than reset
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'failed',
                completed_at: new Date().toISOString(),
              }
            : null
        );
        setSessions((prevSessions) =>
          prevSessions.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: 'failed' }
              : session
          )
        );
        if (currentSession) {
          void syncSessionEvents(currentSession.id, currentSession);
        }
      }
    },
    [appendEvent, currentSession, refreshSessions, syncSessionEvents]
  );

  const startSSE = useCallback(
    (sessionId: string, lastSequence?: number | null) => {
      if (!config || !token) {
        return;
      }

      if (cleanupRef.current) {
        cleanupRef.current();
      }

      cleanupRef.current = connectSSE(
        config.api.base_url,
        sessionId,
        token,
        (event) => {
          setReconnecting(false);
          handleEvent(event);
        },
        (err) => {
          setReconnecting(false);
          setError(err.message);
        },
        (attempt) => {
          setReconnecting(true);
          setError(`Connection lost. Reconnecting (attempt ${attempt})...`);
        },
        lastSequence ?? null
      );
    },
    [config, token, handleEvent]
  );

  const handleSubmit = async (): Promise<void> => {
    if (!config || !token || !inputValue.trim()) {
      return;
    }

    const taskText = inputValue.trim();
    setError(null);
    setStatus('running');
    setRunningStartTime(new Date().toISOString());
    activeTurnRef.current = 0;
    const userEvent: TerminalEvent = {
      type: 'user_message',
      data: { text: taskText },
      timestamp: new Date().toISOString(),
      sequence: Date.now(),
    };

    // Check if we can continue the session:
    // - Session exists and is not running
    // - If cancelled, it must be resumable (has resume_id from agent_start)
    const canContinue = currentSession && currentSession.status !== 'running';
    const isCancelledNotResumable =
      currentSession?.status === 'cancelled' && currentSession.resumable === false;

    // If session was cancelled before agent_start, we can't resume - start fresh
    const shouldContinue = canContinue && !isCancelledNotResumable;

    if (shouldContinue && currentSession) {
      // Close old SSE connection before appending user event to prevent
      // late-arriving events from previous request appearing after the new message
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }
      appendEvent(userEvent);
      try {
        const response = await continueTask(
          config.api.base_url,
          token,
          currentSession.id,
          taskText,
          selectedModel
        );

        setCurrentSession((prev) => ({
          ...prev!,
          status: response.status,
          updated_at: new Date().toISOString(),
        }));

        setInputValue('');
        setAttachedFiles([]);
        const lastSequence = getLastServerSequence(events);
        startSSE(currentSession.id, lastSequence);
        refreshSessions();
      } catch (err) {
        setStatus('failed');
        // Provide helpful error message for non-resumable sessions
        const errorMessage = (err as Error).message;
        if (errorMessage.includes('cannot be resumed')) {
          setError(
            'Session cannot be resumed. The agent was cancelled before it could start. ' +
              'Your next message will start a new session.'
          );
          // Mark session as not resumable for future attempts
          setCurrentSession((prev) => (prev ? { ...prev, resumable: false } : null));
        } else {
          setError(`Failed to continue task: ${errorMessage}`);
        }
      }
    } else {
      setEvents([userEvent]);
      setExpandedTools(new Set());
      setExpandedSubagents(new Set());
      setExpandedComments(new Set());
      setExpandedFiles(new Set());
      setStats({
        turns: 0,
        cost: 0,
        durationMs: 0,
        tokensIn: 0,
        tokensOut: 0,
        model: '',
      });

      try {
        const response = await runTask(config.api.base_url, token, taskText, selectedModel);
        const sessionId = response.session_id;
        setCurrentSession({
          id: sessionId,
          status: response.status,
          task: taskText,
          model: selectedModel,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          completed_at: null,
          num_turns: 0,
          duration_ms: null,
          total_cost_usd: null,
          cancel_requested: false,
        });
        setInputValue('');
        setAttachedFiles([]);
        startSSE(sessionId, null);
        refreshSessions();
        // Update URL to reflect new session
        navigate(`/session/${sessionId}/`, { replace: true });
      } catch (err) {
        setStatus('failed');
        setError(`Failed to start task: ${(err as Error).message}`);
      }
    }
  };

  const handleCancel = async (): Promise<void> => {
    if (!config || !token || !currentSession) {
      return;
    }

    try {
      await cancelSession(config.api.base_url, token, currentSession.id);
      setStatus('cancelled');
    } catch (err) {
      setError(`Failed to cancel: ${(err as Error).message}`);
    }
  };

  const handleAttachFiles = useCallback((files: File[]) => {
    const newFiles: AttachedFile[] = files.map((file) => ({
      file,
      id: `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    }));
    setAttachedFiles((prev) => [...prev, ...newFiles]);
  }, []);

  const handleRemoveFile = useCallback((id: string) => {
    setAttachedFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  const handleSelectSession = async (sessionId: string): Promise<void> => {
    if (!config || !token) {
      return;
    }

    // Validate session ID format before making API calls
    if (!isValidSessionId(sessionId)) {
      setError('Invalid session ID format');
      navigate('/', { replace: true });
      return;
    }

    try {
      const session = await getSession(config.api.base_url, token, sessionId);
      setCurrentSession(session);

      const historyEvents = await getSessionEvents(config.api.base_url, token, sessionId);
      const lastSequence = getLastServerSequence(historyEvents);
      setEvents(seedSessionEvents(session, historyEvents));

      const lastCompletion = [...historyEvents].reverse().find((event) => event.type === 'agent_complete');
      if (lastCompletion) {
        const usage = (lastCompletion.data.usage ?? null) as
          | {
              input_tokens?: number;
              output_tokens?: number;
              cache_creation_input_tokens?: number;
              cache_read_input_tokens?: number;
            }
          | null;
        const tokensIn =
          (usage?.input_tokens ?? 0) +
          (usage?.cache_creation_input_tokens ?? 0) +
          (usage?.cache_read_input_tokens ?? 0);
        const tokensOut = usage?.output_tokens ?? 0;
        setStats({
          turns: Number(lastCompletion.data.num_turns ?? session.num_turns),
          cost: Number(lastCompletion.data.total_cost_usd ?? session.total_cost_usd ?? 0),
          durationMs: Number(lastCompletion.data.duration_ms ?? session.duration_ms ?? 0),
          tokensIn,
          tokensOut,
          model: String(lastCompletion.data.model ?? session.model ?? ''),
        });
      } else {
        const lastMetrics = [...historyEvents].reverse().find((event) => event.type === 'metrics_update');
        if (lastMetrics) {
          setStats((prev) => ({
            ...prev,
            turns: Number(lastMetrics.data.turns ?? prev.turns),
            tokensIn: Number(lastMetrics.data.tokens_in ?? prev.tokensIn),
            tokensOut: Number(lastMetrics.data.tokens_out ?? prev.tokensOut),
            cost: lastMetrics.data.total_cost_usd !== undefined ? Number(lastMetrics.data.total_cost_usd) : prev.cost,
            model: String(lastMetrics.data.model ?? session.model ?? prev.model ?? ''),
          }));
        }
      }

      setStatus(normalizeStatus(session.status));

      if (session.status === 'running') {
        startSSE(sessionId, lastSequence);
      }

      // Update URL to reflect selected session
      navigate(`/session/${sessionId}/`, { replace: true });
    } catch (err) {
      setError(`Failed to load session: ${(err as Error).message}`);
    }
  };

  const handleNewSession = (): void => {
    if (cleanupRef.current) {
      cleanupRef.current();
    }
    setCurrentSession(null);
    setEvents([]);
    setStatus('idle');
    setExpandedTools(new Set());
    setExpandedSubagents(new Set());
    setExpandedComments(new Set());
    setExpandedFiles(new Set());
    setAttachedFiles([]);
    setMobileExpandedMessages(new Set());
    setStats({
      turns: 0,
      cost: 0,
      durationMs: 0,
      tokensIn: 0,
      tokensOut: 0,
      model: '',
    });
    // Navigate to root URL for new session
    navigate('/', { replace: true });
  };

  const toggleRightPanel = useCallback(() => {
    setRightPanelCollapsed((prev) => {
      const next = !prev;
      setStoredPanelCollapsed(next);
      return next;
    });
  }, []);

  const toggleMobileMessageExpand = useCallback((id: string) => {
    setMobileExpandedMessages((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const conversation = useMemo<ConversationItem[]>(() => {
    const sortedEvents = [...events].sort((a, b) => {
      const timeA = a.timestamp ? new Date(a.timestamp).getTime() : 0;
      const timeB = b.timestamp ? new Date(b.timestamp).getTime() : 0;
      if (timeA !== timeB) {
        return timeA - timeB;
      }
      const seqA = a.sequence ?? 0;
      const seqB = b.sequence ?? 0;
      return seqA - seqB;
    });

    const items: ConversationItem[] = [];
    let pendingTools: ToolCallView[] = [];
    let pendingSubagents: SubagentView[] = [];
    const activeSubagentMap = new Map<string, SubagentView>();
    let pendingFiles = new Set<string>();
    let currentStreamMessage: ConversationItem | null = null;
    let streamBuffer = '';
    let lastAgentMessage: ConversationItem | null = null;
    let streamMessageSeeded = false;

    const fileToolPattern = /(write|edit|save|apply|move|copy)/i;

    const findOpenTool = (toolName: string, toolId?: string): ToolCallView | undefined => {
      // First try to match by tool_id in pendingTools (most reliable)
      if (toolId) {
        for (let i = pendingTools.length - 1; i >= 0; i -= 1) {
          const tool = pendingTools[i];
          if (tool.id === toolId) {
            return tool;
          }
        }
      }
      // Fallback to matching by tool name and status in pendingTools
      for (let i = pendingTools.length - 1; i >= 0; i -= 1) {
        const tool = pendingTools[i];
        if (tool.tool === toolName && tool.status === 'running') {
          return tool;
        }
      }
      // Also check lastAgentMessage.toolCalls (for history replay where message comes before tool_complete)
      if (lastAgentMessage?.type === 'agent_message') {
        const agentMsg = lastAgentMessage as { toolCalls: ToolCallView[] };
        if (toolId) {
          for (let i = agentMsg.toolCalls.length - 1; i >= 0; i -= 1) {
            const tool = agentMsg.toolCalls[i];
            if (tool.id === toolId) {
              return tool;
            }
          }
        }
        for (let i = agentMsg.toolCalls.length - 1; i >= 0; i -= 1) {
          const tool = agentMsg.toolCalls[i];
          if (tool.tool === toolName && tool.status === 'running') {
            return tool;
          }
        }
      }
      // Also check currentStreamMessage.toolCalls
      if (currentStreamMessage?.type === 'agent_message') {
        const streamMsg = currentStreamMessage as { toolCalls: ToolCallView[] };
        if (toolId) {
          for (let i = streamMsg.toolCalls.length - 1; i >= 0; i -= 1) {
            const tool = streamMsg.toolCalls[i];
            if (tool.id === toolId) {
              return tool;
            }
          }
        }
        for (let i = streamMsg.toolCalls.length - 1; i >= 0; i -= 1) {
          const tool = streamMsg.toolCalls[i];
          if (tool.tool === toolName && tool.status === 'running') {
            return tool;
          }
        }
      }
      return undefined;
    };

    const reuseLastAgentMessage = (): ConversationItem | null => {
      if (!lastAgentMessage) {
        return null;
      }
      if (lastAgentMessage.content || lastAgentMessage.status) {
        return null;
      }
      if (lastAgentMessage.toolCalls.length === 0 && !streamMessageSeeded) {
        return null;
      }
      return lastAgentMessage;
    };

    const flushPendingTools = (timestamp?: string) => {
      if (pendingTools.length > 0) {
        const existing = reuseLastAgentMessage();
        const toolMessage: ConversationItem = existing ?? {
          type: 'agent_message',
          id: `agent-auto-${items.length}`,
          time: formatTimestamp(timestamp),
          content: '',
          toolCalls: pendingTools,
          subagents: pendingSubagents,
        };
        if (!existing) {
          items.push(toolMessage);
        } else {
          toolMessage.toolCalls = pendingTools;
        }
        lastAgentMessage = toolMessage;
        pendingTools = [];
      }
    };

    const attachFilesToMessage = (message: ConversationItem | null) => {
      if (!message || pendingFiles.size === 0) {
        return;
      }
      const files = Array.from(pendingFiles);
      message.files = files;
      pendingFiles = new Set();
    };

    let toolIdCounter = 0;

    sortedEvents.forEach((event) => {
      switch (event.type) {
        case 'agent_start': {
          if (!currentStreamMessage && !lastAgentMessage) {
            currentStreamMessage = {
              type: 'agent_message',
              id: `agent-${items.length}`,
              time: formatTimestamp(event.timestamp),
              content: '',
              toolCalls: pendingTools,
              subagents: pendingSubagents,
            };
            items.push(currentStreamMessage);
            pendingTools = [];
            pendingSubagents = [];
            streamMessageSeeded = true;
          }
          break;
        }
        case 'user_message': {
          pendingTools = [];
          pendingSubagents = [];
          activeSubagentMap.clear();
          pendingFiles = new Set();
          currentStreamMessage = null;
          streamBuffer = '';
          lastAgentMessage = null;
          streamMessageSeeded = false;
          const content = String(event.data.text ?? '');
          items.push({
            type: 'user',
            id: `user-${items.length}`,
            time: formatTimestamp(event.timestamp),
            content,
          });
          break;
        }
        case 'thinking': {
          const thinkingTool: ToolCallView = {
            id: `think-${toolIdCounter++}`,
            tool: 'Think',
            time: formatTimestamp(event.timestamp),
            status: 'complete',
            thinking: String(event.data.text ?? ''),
          };

          // Attach thinking to the current or last agent message if one exists
          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            currentStreamMessage.toolCalls.push(thinkingTool);
          } else if (lastAgentMessage && lastAgentMessage.type === 'agent_message') {
            (lastAgentMessage as { toolCalls: ToolCallView[] }).toolCalls.push(thinkingTool);
          } else {
            // No existing message - accumulate for next message
            pendingTools.push(thinkingTool);
          }
          break;
        }
        case 'tool_start': {
          const toolName = String(event.data.tool_name ?? 'Tool');
          const toolId = String(event.data.tool_id ?? `tool-${toolIdCounter}`);
          const toolInput = event.data.tool_input as Record<string, unknown> | undefined;
          const newTool: ToolCallView = {
            id: toolId,
            tool: toolName,
            time: formatTimestamp(event.timestamp),
            status: 'running',
            input: toolInput ?? '',
          };
          toolIdCounter++;

          // Attach tool to the current or last agent message if one exists
          // This ensures tools appear under the message that invoked them
          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            currentStreamMessage.toolCalls.push(newTool);
            (currentStreamMessage as { isStreaming?: boolean }).isStreaming = false;
          } else if (lastAgentMessage && lastAgentMessage.type === 'agent_message') {
            (lastAgentMessage as { toolCalls: ToolCallView[] }).toolCalls.push(newTool);
            (lastAgentMessage as { isStreaming?: boolean }).isStreaming = false;
          } else {
            // No existing message - accumulate for next message
            pendingTools.push(newTool);
          }

          if (toolInput && fileToolPattern.test(toolName)) {
            extractFilePaths(toolInput).forEach((path) => pendingFiles.add(path));
          }
          break;
        }
        case 'tool_complete': {
          const MAX_OUTPUT_LINES = 100;
          const MAX_OUTPUT_CHARS = 10000;
          
          const toolName = String(event.data.tool_name ?? 'Tool');
          const toolId = event.data.tool_id ? String(event.data.tool_id) : undefined;
          const durationMs = Number(event.data.duration_ms ?? 0);
          const isError = Boolean(event.data.is_error);
          const result = event.data.result;
          const tool = findOpenTool(toolName, toolId);
          if (tool) {
            tool.status = isError ? 'failed' : 'complete';
            tool.durationMs = durationMs;
            if (result !== undefined && result !== null) {
              const rawOutput = typeof result === 'string' 
                ? result 
                : JSON.stringify(result, null, 2);
              
              const lines = rawOutput.split('\n');
              tool.outputLineCount = lines.length;
              
              if (lines.length > MAX_OUTPUT_LINES || rawOutput.length > MAX_OUTPUT_CHARS) {
                let truncatedOutput = lines.slice(0, MAX_OUTPUT_LINES).join('\n');
                if (truncatedOutput.length > MAX_OUTPUT_CHARS) {
                  truncatedOutput = truncatedOutput.slice(0, MAX_OUTPUT_CHARS);
                }
                const remainingLines = lines.length - MAX_OUTPUT_LINES;
                if (remainingLines > 0) {
                  truncatedOutput += `\n\n... (${remainingLines} more lines)`;
                }
                tool.output = truncatedOutput;
                tool.outputTruncated = true;
              } else {
                tool.output = rawOutput;
                tool.outputTruncated = false;
              }
            }
            if (isError) {
              tool.error = String(event.data.error ?? 'Tool failed');
            }
          }
          break;
        }
        case 'message': {
          const text = String(event.data.text ?? '');
          const fullText = typeof event.data.full_text === 'string' ? event.data.full_text : '';
          const isPartial = Boolean(event.data.is_partial);
          const eventStructuredFields = coerceStructuredFields(event.data.structured_fields);

          if (isPartial) {
            streamBuffer += text;
            if (!currentStreamMessage) {
              const existing = reuseLastAgentMessage();
              currentStreamMessage = existing ?? {
                type: 'agent_message',
                id: `agent-${items.length}`,
                time: formatTimestamp(event.timestamp),
                content: streamBuffer,
                toolCalls: pendingTools,
                subagents: pendingSubagents,
                isStreaming: true,
              };
              if (!existing) {
                items.push(currentStreamMessage);
              } else if (pendingTools.length > 0 && currentStreamMessage.type === 'agent_message') {
                currentStreamMessage.toolCalls = pendingTools;
              }
              if (currentStreamMessage.type === 'agent_message') {
                (currentStreamMessage as { isStreaming?: boolean }).isStreaming = true;
              }
              pendingTools = [];
              pendingSubagents = [];
            } else if (currentStreamMessage.type === 'agent_message') {
              currentStreamMessage.content = streamBuffer;
              (currentStreamMessage as { isStreaming?: boolean }).isStreaming = true;
            }
            break;
          }

          if (!fullText && !text && !eventStructuredFields && !streamBuffer) {
            break;
          }

          let finalText = '';
          if (fullText) {
            finalText = fullText;
          } else if (streamBuffer) {
            // Use accumulated stream buffer from partial messages
            finalText = streamBuffer;
          } else {
            // Fallback to text field (used in history events)
            finalText = text;
          }
          finalText = finalText.trim();
          streamBuffer = '';
          const structuredInfo = eventStructuredFields
            ? {
                body: finalText,
                fields: eventStructuredFields,
                status: (() => {
                  const statusRaw = typeof event.data.structured_status === 'string'
                    ? event.data.structured_status
                    : eventStructuredFields.status;
                  return statusRaw ? (normalizeStatus(statusRaw) as ResultStatus) : undefined;
                })(),
                error: (() => {
                  const errorRaw = typeof event.data.structured_error === 'string'
                    ? event.data.structured_error
                    : eventStructuredFields.error;
                  return errorRaw ?? undefined;
                })(),
              }
            : parseStructuredMessage(finalText);
          const bodyText = structuredInfo.body;

          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            currentStreamMessage.content = bodyText;
            currentStreamMessage.structuredStatus = structuredInfo.status;
            currentStreamMessage.structuredError = structuredInfo.error;
            currentStreamMessage.structuredFields = structuredInfo.fields;
            // Keep streaming indicator true - will be set false by tool_start, subagent_start, or agent_complete
            (currentStreamMessage as { isStreaming?: boolean }).isStreaming = true;
            lastAgentMessage = currentStreamMessage;
            currentStreamMessage = null;
          } else if (bodyText || pendingTools.length > 0) {
            const existing = reuseLastAgentMessage();
            const agentMessage: ConversationItem = {
              type: 'agent_message',
              id: existing?.id ?? `agent-${items.length}`,
              time: existing?.time ?? formatTimestamp(event.timestamp),
              content: bodyText,
              toolCalls: existing?.toolCalls ?? pendingTools,
              subagents: existing?.subagents ?? pendingSubagents,
              structuredStatus: structuredInfo.status,
              structuredError: structuredInfo.error,
              structuredFields: structuredInfo.fields,
              // Keep streaming indicator true - will be set false by tool_start, subagent_start, or agent_complete
              isStreaming: true,
            };
            if (existing) {
              Object.assign(existing, agentMessage);
              lastAgentMessage = existing;
            } else {
              items.push(agentMessage);
              lastAgentMessage = agentMessage;
            }
          }

          pendingTools = [];
          pendingSubagents = [];
          attachFilesToMessage(lastAgentMessage);
          break;
        }
        case 'agent_complete': {
          const statusValue = normalizeStatus(String(event.data.status ?? 'complete')) as ResultStatus;

          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            currentStreamMessage.content = streamBuffer.trim();
            (currentStreamMessage as { isStreaming?: boolean }).isStreaming = false;
            lastAgentMessage = currentStreamMessage;
            currentStreamMessage = null;
            streamBuffer = '';
          }

          if (!lastAgentMessage && (pendingTools.length > 0 || pendingSubagents.length > 0)) {
            const toolMessage: ConversationItem = {
              type: 'agent_message',
              id: `agent-${items.length}`,
              time: formatTimestamp(event.timestamp),
              content: '',
              toolCalls: pendingTools,
              subagents: pendingSubagents,
              isStreaming: false,
            };
            items.push(toolMessage);
            pendingTools = [];
            pendingSubagents = [];
            lastAgentMessage = toolMessage;
          }

          attachFilesToMessage(lastAgentMessage);

          // Mark any still-running subagents as complete (fallback for orphaned subagents)
          activeSubagentMap.forEach((subagent) => {
            if (subagent.status === 'running') {
              subagent.status = 'complete';
            }
          });

          if (lastAgentMessage && lastAgentMessage.type === 'agent_message') {
            lastAgentMessage.status = lastAgentMessage.structuredStatus ?? statusValue;
            (lastAgentMessage as { isStreaming?: boolean }).isStreaming = false;
          }
          break;
        }
        case 'error': {
          const outputText = lastAgentMessage?.content?.trim() || 'Task failed.';
          items.push({
            type: 'output',
            id: `output-${items.length}`,
            time: formatTimestamp(event.timestamp),
            output: outputText,
            comments: undefined,
            files: lastAgentMessage?.files ?? [],
            status: 'failed',
            error: String(event.data.message ?? 'Unknown error'),
          });
          break;
        }
        case 'cancelled': {
          const outputText = lastAgentMessage?.content?.trim() || 'Task cancelled.';
          items.push({
            type: 'output',
            id: `output-${items.length}`,
            time: formatTimestamp(event.timestamp),
            output: outputText,
            comments: undefined,
            files: lastAgentMessage?.files ?? [],
            status: 'cancelled',
            error: 'Task was cancelled.',
          });
          break;
        }
        case 'subagent_start': {
          const taskId = String(event.data.task_id ?? '');
          const subagentName = String(event.data.subagent_name ?? 'unknown');
          const promptPreview = String(event.data.prompt_preview ?? '');
          const subagent: SubagentView = {
            id: `subagent-${taskId}`,
            taskId,
            name: subagentName,
            time: formatTimestamp(event.timestamp),
            status: 'running',
            promptPreview,
          };
          activeSubagentMap.set(taskId, subagent);

          // Attach subagent to the current or last agent message if one exists
          // This ensures subagents appear under the message that invoked them
          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            currentStreamMessage.subagents.push(subagent);
            (currentStreamMessage as { isStreaming?: boolean }).isStreaming = false;
          } else if (lastAgentMessage && lastAgentMessage.type === 'agent_message') {
            (lastAgentMessage as { subagents: SubagentView[] }).subagents.push(subagent);
            (lastAgentMessage as { isStreaming?: boolean }).isStreaming = false;
          } else {
            // No existing message - accumulate for next message
            pendingSubagents.push(subagent);
          }
          break;
        }
        case 'subagent_message': {
          const taskId = String(event.data.task_id ?? '');
          const text = String(event.data.text ?? '');
          const isPartial = Boolean(event.data.is_partial);
          const subagent = activeSubagentMap.get(taskId);
          if (subagent) {
            if (isPartial) {
              subagent.messageBuffer = (subagent.messageBuffer ?? '') + text;
            } else if (text) {
              subagent.messageBuffer = (subagent.messageBuffer ?? '') + text;
            }
          }
          break;
        }
        case 'subagent_stop': {
          const taskId = String(event.data.task_id ?? '');
          const resultPreview = String(event.data.result_preview ?? '');
          const durationMs = Number(event.data.duration_ms ?? 0);
          const isError = Boolean(event.data.is_error);
          const subagent = activeSubagentMap.get(taskId);
          if (subagent) {
            subagent.status = isError ? 'failed' : 'complete';
            subagent.durationMs = durationMs;
            subagent.resultPreview = resultPreview;
          }
          break;
        }
        default:
          break;
      }
    });

    if (pendingTools.length > 0) {
      flushPendingTools();
    }

    return items;
  }, [events]);

  const toolStats = useMemo(() => {
    const statsMap: Record<string, number> = {};
    conversation.forEach((item) => {
      if (item.type === 'agent_message') {
        item.toolCalls.forEach((tool) => {
          const toolName = tool.tool;
          statsMap[toolName] = (statsMap[toolName] ?? 0) + 1;
        });
      }
    });
    return statsMap;
  }, [conversation]);

  const totalToolCalls = Object.values(toolStats).reduce((sum, count) => sum + count, 0);

  const subagentStats = useMemo(() => {
    const statsMap: Record<string, number> = {};
    conversation.forEach((item) => {
      if (item.type === 'agent_message') {
        item.subagents.forEach((subagent) => {
          const subagentName = subagent.name;
          statsMap[subagentName] = (statsMap[subagentName] ?? 0) + 1;
        });
      }
    });
    return statsMap;
  }, [conversation]);

  const totalSubagentCalls = Object.values(subagentStats).reduce((sum, count) => sum + count, 0);

  // Extract system events (permission denials, hook triggers, profile switches)
  const systemEvents = useMemo<SystemEventView[]>(() => {
    const sysEvents: SystemEventView[] = [];
    let eventCounter = 0;
    
    events.forEach((event) => {
      if (event.type === 'hook_triggered') {
        const decision = String(event.data.decision ?? '');
        // Only show permission denials (not allows) to keep UI clean
        if (decision === 'deny') {
          sysEvents.push({
            id: `sys-${eventCounter++}`,
            time: formatTimestamp(event.timestamp),
            eventType: 'permission_denied',
            toolName: String(event.data.tool_name ?? ''),
            decision,
            message: String(event.data.message ?? ''),
          });
        }
      } else if (event.type === 'profile_switch') {
        sysEvents.push({
          id: `sys-${eventCounter++}`,
          time: formatTimestamp(event.timestamp),
          eventType: 'profile_switch',
          profileName: String(event.data.profile_name ?? ''),
        });
      }
    });
    
    return sysEvents;
  }, [events]);

  const headerStats = useMemo(() => {
    const counts = { complete: 0, partial: 0, failed: 0 };
    conversation.forEach((item) => {
      if (item.type === 'output') {
        if (item.status === 'complete') {
          counts.complete += 1;
        } else if (item.status === 'partial') {
          counts.partial += 1;
        } else if (item.status === 'failed') {
          counts.failed += 1;
        }
      }
    });
    return counts;
  }, [conversation]);

  const outputItems = useMemo(() => conversation.filter((item) => item.type === 'output'), [conversation]);

  const todosByAgentId = useMemo(() => {
    const todosMap = new Map<
      string,
      { todos: TodoItem[]; status: ResultStatus | undefined }
    >();
    const userIndices = conversation
      .map((item, index) => (item.type === 'user' ? index : -1))
      .filter((index) => index >= 0);

    const segmentStarts = userIndices.length > 0 ? userIndices : [-1];

    segmentStarts.forEach((userIndex, segmentIndex) => {
      const start = userIndex + 1;
      const end = segmentIndex + 1 < segmentStarts.length
        ? segmentStarts[segmentIndex + 1]
        : conversation.length;
      if (start >= end) {
        return;
      }

      let todos: TodoItem[] | null = null;
      let firstTodoIndex = -1;
      const agentIndices: number[] = [];
      let lastAgentIndex = -1;

      for (let i = start; i < end; i += 1) {
        const item = conversation[i];
        if (item.type === 'agent_message') {
          agentIndices.push(i);
          lastAgentIndex = i;
          const foundTodos = extractTodos(item.toolCalls);
          if (foundTodos && foundTodos.length > 0) {
            todos = foundTodos;
            if (firstTodoIndex === -1) {
              firstTodoIndex = i;
            }
          }
        }
      }

      if (!todos || agentIndices.length === 0 || lastAgentIndex < 0) {
        return;
      }

      let terminalStatus: ResultStatus | undefined;
      const lastSegmentItem = conversation[end - 1];
      if (lastSegmentItem?.type === 'output') {
        terminalStatus = lastSegmentItem.status;
      } else if (lastAgentIndex >= 0) {
        const lastAgent = conversation[lastAgentIndex];
        if (lastAgent.type === 'agent_message') {
          terminalStatus = lastAgent.status as ResultStatus | undefined;
        }
      }
      if (!terminalStatus && end === conversation.length && status !== 'running') {
        terminalStatus = normalizeStatus(status) as ResultStatus;
      }

      const targetIndex = lastAgentIndex;
      if (targetIndex < firstTodoIndex) {
        return;
      }
      const agentItem = conversation[targetIndex];
      if (agentItem.type === 'agent_message') {
        todosMap.set(agentItem.id, {
          todos,
          status: terminalStatus,
        });
      }
    });

    return todosMap;
  }, [conversation, status]);


  // Auto-scroll effect - using useLayoutEffect to run synchronously after DOM updates
  useLayoutEffect(() => {
    if (!outputRef.current || !config?.ui.auto_scroll) {
      return;
    }
    // Scroll to bottom after conversation is rendered
    outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [conversation, config]);

  const sessionDuration = formatDuration(stats.durationMs);
  const sessionIdLabel = currentSession?.id ?? 'new';

  const sessionItems = useMemo(() => {
    return sessions.map((session) => (
      <button
        key={session.id}
        className="session-item"
        onClick={() => handleSelectSession(session.id)}
        type="button"
      >
        <div className="session-item-row">
          <span className="session-id">{session.id.slice(0, 8)}...</span>
          <span className={`session-status ${session.status}`}>{session.status}</span>
        </div>
        <div className="session-task">{session.task || 'No task'}</div>
      </button>
    ));
  }, [sessions]);

  const handleFileAction = async (filePath: string, mode: 'view' | 'download') => {
    if (!config || !token || !currentSession) {
      return;
    }
    if (!isSafeRelativePath(filePath)) {
      setError('Refusing to open unsafe file path.');
      return;
    }

    try {
      const response = await fetch(
        `${config.api.base_url}/api/v1/sessions/${currentSession.id}/files?path=${encodeURIComponent(filePath)}`,
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (!response.ok) {
        throw new Error(`Failed to fetch file: ${response.status}`);
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const filename = filePath.split('/').pop() || 'result-file';

      if (mode === 'view') {
        window.open(url, '_blank', 'noopener,noreferrer');
      } else {
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.click();
      }

      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(`Failed to load file: ${(err as Error).message}`);
    }
  };

  const toggleTool = (id: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleSubagent = (id: string) => {
    setExpandedSubagents((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleComments = (id: string) => {
    setExpandedComments((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleFiles = (id: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const expandAllSections = () => {
    const allToolIds = conversation.flatMap((item) =>
      item.type === 'agent_message' ? item.toolCalls.map((tool) => tool.id) : []
    );
    const allSubagentIds = conversation.flatMap((item) =>
      item.type === 'agent_message' ? item.subagents.map((s) => s.id) : []
    );
    const allOutputIds = outputItems.map((item) => item.id);
    const allAgentMessageIds = conversation
      .filter((item) => item.type === 'agent_message')
      .map((item) => item.id);
    setExpandedTools(new Set(allToolIds));
    setExpandedSubagents(new Set(allSubagentIds));
    setExpandedComments(new Set([...allOutputIds, ...allAgentMessageIds]));
    setExpandedFiles(new Set([...allAgentMessageIds, ...allOutputIds]));
  };

  const collapseAllSections = () => {
    setExpandedTools(new Set());
    setExpandedSubagents(new Set());
    setExpandedComments(new Set());
    setExpandedFiles(new Set());
  };

  const toggleAllSections = () => {
    if (expandedTools.size > 0 || expandedSubagents.size > 0 || expandedComments.size > 0 || expandedFiles.size > 0) {
      collapseAllSections();
    } else {
      expandAllSections();
    }
  };

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isRunning) {
        handleCancel();
      }
      if (event.key === '/' && event.ctrlKey) {
        event.preventDefault();
        toggleAllSections();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [handleCancel, isRunning, toggleAllSections]);

  return (
    <div className="terminal-app">
      <header className="terminal-header">
        <div className="header-top">
          <div className="header-title">
            <span className="header-icon">◆</span>
            <span className="header-label">Ag3ntum</span>
            <span className="header-divider">│</span>
            <span className="header-meta">user: {user?.username || 'unknown'}</span>
            <button
              className="logout-button"
              type="button"
              onClick={logout}
              title="Sign out"
            >
              ⏻
            </button>
          </div>
        </div>
        <div className="header-stats">
          <span>Messages: <strong>{conversation.length}</strong></span>
          <span>Duration: <strong>{sessionDuration}</strong></span>
          <span>Tools: <strong>{totalToolCalls}</strong></span>
          <span className="header-status">
            <span className="status-complete">✓ {headerStats.complete}</span>
            <span className="status-partial">◐ {headerStats.partial}</span>
            <span className="status-failed">✗ {headerStats.failed}</span>
          </span>
        </div>
        <div className="header-filters">
          <div className="session-selector">
            <span className="filter-label">Sessions:</span>
            <span className="session-current-id">{sessionIdLabel}</span>
            <button className="session-new-button" type="button" onClick={handleNewSession}>
              + New
            </button>
            <div className="dropdown session-dropdown">
              <span className="dropdown-value">[...select]</span>
              <span className="dropdown-icon">▾</span>
              <div className="dropdown-list">
                {sessionItems}
              </div>
            </div>
          </div>
          <div className="filter-actions">
            {!fileExplorerVisible && (
              <>
                <button className="filter-button" type="button" onClick={expandAllSections}>
                  [expand all]
                </button>
                <button className="filter-button" type="button" onClick={collapseAllSections}>
                  [collapse all]
                </button>
              </>
            )}
            {currentSession && (
              <button
                className={`filter-button ${fileExplorerVisible ? 'active' : ''}`}
                type="button"
                onClick={() => {
                  const willBeVisible = !fileExplorerVisible;
                  setFileExplorerVisible(willBeVisible);
                  // Increment refresh key when opening to force reload of file list
                  if (willBeVisible) {
                    setFileExplorerRefreshKey((k) => k + 1);
                  }
                }}
              >
                [File Explorer]
              </button>
            )}
          </div>
        </div>
      </header>

      <main className={`terminal-body ${rightPanelCollapsed ? 'panel-collapsed' : ''}`}>
        {/* Panel toggle - vertical gutter or edge tab */}
        {!isMobile && (
          <div 
            className={`panel-toggle-edge ${rightPanelCollapsed ? 'collapsed' : ''}`}
            onClick={toggleRightPanel}
            title={rightPanelCollapsed ? 'Show details panel' : 'Hide details panel'}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleRightPanel(); }}
          >
            <span className="panel-toggle-icon">
              {rightPanelCollapsed ? '◂' : '▸'}
            </span>
          </div>
        )}
        <div ref={outputRef} className="terminal-output">
          {conversation.length === 0 ? (
            <div className="terminal-empty">Enter a task below to begin.</div>
          ) : (
            <>
              {conversation.map((item, index) => {
                if (item.type === 'user') {
                  return (
                    <MessageBlock
                      key={item.id}
                      sender="USER"
                      time={item.time}
                      content={item.content}
                      rightPanelCollapsed={rightPanelCollapsed}
                      isMobile={isMobile}
                    />
                  );
                }
                if (item.type === 'agent_message') {
                  const isLastAgentMessage = conversation
                    .slice(index + 1)
                    .every((i) => i.type !== 'agent_message');
                  const messageStatus = item.status ?? (isLastAgentMessage && status !== 'running' ? status : undefined);
                  const todoPayload = todosByAgentId.get(item.id);
                  const todos = todoPayload?.todos ?? null;
                  // Only show streaming on the last agent message when overall status is running
                  const showStreaming = item.isStreaming && isLastAgentMessage && status === 'running';
                  return (
                    <AgentMessageBlock
                      key={item.id}
                      id={item.id}
                      time={item.time}
                      content={item.content}
                      toolCalls={item.toolCalls}
                      subagents={item.subagents}
                      todos={todos ?? undefined}
                      toolExpanded={expandedTools}
                      onToggleTool={toggleTool}
                      subagentExpanded={expandedSubagents}
                      onToggleSubagent={toggleSubagent}
                      status={(todoPayload?.status ?? messageStatus) as ResultStatus | undefined}
                      structuredStatus={item.structuredStatus}
                      structuredError={item.structuredError}
                      comments={item.comments}
                      commentsExpanded={expandedComments.has(item.id)}
                      onToggleComments={() => toggleComments(item.id)}
                      files={item.files}
                      filesExpanded={expandedFiles.has(item.id)}
                      onToggleFiles={() => toggleFiles(item.id)}
                      isStreaming={showStreaming}
                      sessionRunning={isLastAgentMessage && status === 'running'}
                      rightPanelCollapsed={rightPanelCollapsed}
                      isMobile={isMobile}
                      mobileExpanded={mobileExpandedMessages.has(item.id)}
                      onToggleMobileExpand={() => toggleMobileMessageExpand(item.id)}
                    />
                  );
                }
                if (item.type === 'output') {
                  return (
                    <OutputBlock
                      key={item.id}
                      id={item.id}
                      time={item.time}
                      output={item.output}
                      comments={item.comments}
                      commentsExpanded={expandedComments.has(item.id)}
                      onToggleComments={() => toggleComments(item.id)}
                      files={item.files}
                      filesExpanded={expandedFiles.has(item.id)}
                      onToggleFiles={() => toggleFiles(item.id)}
                      status={item.status}
                      error={item.error}
                      onFileAction={handleFileAction}
                      rightPanelCollapsed={rightPanelCollapsed}
                      isMobile={isMobile}
                      mobileExpanded={mobileExpandedMessages.has(item.id)}
                      onToggleMobileExpand={() => toggleMobileMessageExpand(item.id)}
                    />
                  );
                }
                return null;
              })}
            </>
          )}
        </div>
        {/* File Explorer Overlay */}
        {fileExplorerVisible && currentSession && config && token && (
          <div className="file-explorer-overlay">
            <div className="file-explorer-overlay-header">
              <span className="file-explorer-overlay-title">File Explorer</span>
              <label className="file-explorer-hidden-toggle">
                <input
                  type="checkbox"
                  checked={showHiddenFiles}
                  onChange={(e) => setShowHiddenFiles(e.target.checked)}
                />
                <span>Hidden</span>
              </label>
              <button
                type="button"
                className="file-explorer-overlay-close"
                onClick={() => setFileExplorerVisible(false)}
                title="Close file explorer"
              >
                [close]
              </button>
            </div>
            <FileExplorer
              key={fileExplorerRefreshKey}
              sessionId={currentSession.id}
              baseUrl={config.api.base_url}
              token={token}
              showHiddenFiles={showHiddenFiles}
              onError={(err) => setError(err)}
            />
          </div>
        )}
      </main>

      <div className="terminal-footer">
        <div className="usage-bar-row">
          <FooterCopyButtons conversation={conversation} outputRef={outputRef} />
        </div>
        <div className="usage-bar-row">
          <div className="tool-usage-bar">
            <span className="tool-usage-label">Tool Usage ({totalToolCalls} calls):</span>
            {Object.keys(toolStats).map((tool) => (
              <ToolTag key={tool} type={tool} count={toolStats[tool]} />
            ))}
          </div>
          {totalSubagentCalls > 0 && (
            <div className="subagent-usage-bar">
              <span className="subagent-usage-label">SubAgents ({totalSubagentCalls} calls):</span>
              {Object.keys(subagentStats).map((name) => (
                <SubagentTag key={name} name={name} count={subagentStats[name]} />
              ))}
            </div>
          )}
          <SystemEventsToggle 
            count={systemEvents.length}
            deniedCount={systemEvents.filter(e => e.eventType === 'permission_denied').length}
            onClick={() => setSystemEventsExpanded(true)}
          />
        </div>
        {systemEventsExpanded && systemEvents.length > 0 && (
          <SystemEventsPanel 
            events={systemEvents} 
            onClose={() => setSystemEventsExpanded(false)}
          />
        )}
        <div className="input-wrapper">
          <div className="input-section">
            <InputField
              value={inputValue}
              onChange={setInputValue}
              onSubmit={handleSubmit}
              onCancel={handleCancel}
              isRunning={isRunning}
              attachedFiles={attachedFiles}
              onAttachFiles={handleAttachFiles}
              onRemoveFile={handleRemoveFile}
              model={selectedModel}
              onModelChange={setSelectedModel}
              availableModels={availableModels}
            />
            <div className={`input-message ${error ? (reconnecting ? 'warning' : 'error') : ''}`}>
              {error || '\u00A0'}
            </div>
          </div>
        </div>
        <StatusFooter
          isRunning={isRunning}
          statusLabel={statusLabel}
          statusClass={statusClass}
          stats={stats}
          connected={Boolean(token) && !reconnecting}
          startTime={runningStartTime}
        />
      </div>
    </div>
  );
}

export default function AppWithAuth({ initialSessionId }: AppProps = {}) {
  return (
    <AuthProvider>
      <ProtectedRoute>
        <App initialSessionId={initialSessionId} />
      </ProtectedRoute>
    </AuthProvider>
  );
}
