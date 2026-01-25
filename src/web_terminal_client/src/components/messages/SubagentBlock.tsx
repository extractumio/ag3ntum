/**
 * Subagent block component
 *
 * Renders a subagent call with its prompt, messages, and result.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import type { SubagentView } from '../../types/conversation';
import { formatDuration, extractSubagentPreview } from '../../utils';
import { PulsingCircleSpinner } from '../spinners';
import { CollapsibleOutput } from './CollapsibleOutput';

export interface SubagentBlockProps {
  subagent: SubagentView;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
}

export function SubagentBlock({
  subagent,
  expanded,
  onToggle,
  isLast,
}: SubagentBlockProps): JSX.Element {
  const hasContent = Boolean(subagent.promptPreview || subagent.resultPreview || subagent.messageBuffer);
  const treeChar = isLast ? '└──' : '├──';
  const isRunning = subagent.status === 'running';
  const rawPreview = subagent.resultPreview || subagent.messageBuffer || subagent.promptPreview || '';
  const previewText = extractSubagentPreview(rawPreview);

  // Status icon: pulsing circle while running, checkmark/cross when done
  const statusIcon =
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
        {isRunning && (
          <span className={`subagent-status-icon ${statusClass}`}><PulsingCircleSpinner /></span>
        )}
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
