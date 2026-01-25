/**
 * Output block component
 *
 * Renders output messages with optional comments, files, and error states.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import type { ResultStatus } from '../../types/conversation';
import { OUTPUT_STATUS_CLASS } from '../../constants';
import { renderMarkdown } from '../../MarkdownRenderer';
import { ResultSection } from './ResultSection';

export interface OutputBlockProps {
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
  onShowInExplorer?: (filePath: string) => void;
  rightPanelCollapsed: boolean;
  isMobile: boolean;
  mobileExpanded: boolean;
  onToggleMobileExpand: () => void;
}

export function OutputBlock({
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
  onShowInExplorer,
  rightPanelCollapsed,
  isMobile,
  mobileExpanded,
  onToggleMobileExpand,
}: OutputBlockProps): JSX.Element {
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
              onShowInExplorer={onShowInExplorer}
            />
          </div>
        )}
      </div>
      {error && <div className="output-error">{error}</div>}
    </div>
  );
}
