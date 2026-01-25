/**
 * Tool call block component
 *
 * Renders a tool call with its input, output, and status.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import type { ToolCallView } from '../../types/conversation';
import { formatDuration, formatToolInput } from '../../utils';
import { PulsingCircleSpinner } from '../spinners';
import { ToolTag } from './tags';
import { CollapsibleOutput } from './CollapsibleOutput';

export interface ToolCallBlockProps {
  tool: ToolCallView;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
}

export function ToolCallBlock({
  tool,
  expanded,
  onToggle,
  isLast,
}: ToolCallBlockProps): JSX.Element {
  const hasContent = Boolean(tool.thinking || tool.input || tool.output || tool.error);
  const treeChar = isLast ? '└──' : '├──';
  const isRunning = tool.status === 'running';
  const isThinkingTool = tool.tool === 'Think';

  // Status icon: pulsing circle while running, checkmark/cross when done
  // For Think tool, use brain emoji when complete
  const statusIcon =
    tool.status === 'complete' ? (isThinkingTool ? '🧠' : '✓') :
    tool.status === 'failed' ? '✗' : null;

  const statusClass =
    tool.status === 'complete' ? 'tool-status-success' :
    tool.status === 'failed' ? 'tool-status-error' :
    isRunning ? 'tool-status-running' : '';

  // Tool-specific input preview (only while running)
  const getRunningPreview = (): string | null => {
    if (!isRunning) return null;

    // Special handling for Think tool - show thinking preview
    // Backend already sends last 300 chars, show last 60 for header
    if (isThinkingTool && tool.thinking) {
      const preview = tool.thinking.slice(-60).replace(/\n/g, ' ');
      return preview + '...';
    }

    if (!tool.input) return null;

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
        {isRunning && (
          <span className={`tool-status-icon ${statusClass}`}><PulsingCircleSpinner /></span>
        )}
        {statusIcon && (
          <span className={`tool-status-icon ${statusClass}`}>{statusIcon}</span>
        )}
        {hasContent && !isRunning && !statusIcon && <span className="tool-toggle">{expanded ? '▼' : '▶'}</span>}
        {hasContent && (isRunning || statusIcon) && <span className="tool-toggle">{expanded ? '▼' : '▶'}</span>}
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
            <div className={`tool-thinking ${isRunning ? 'tool-thinking-streaming' : ''}`}>
              {isRunning ? (
                // Streaming thinking - backend sends last 300 chars every ~1s
                <>💭 Thinking: {tool.thinking}...</>
              ) : (
                // Complete thinking - show full content (collapsible via CollapsibleOutput)
                <CollapsibleOutput output={`💭 ${tool.thinking}`} />
              )}
            </div>
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
