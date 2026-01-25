/**
 * Message block component for user messages
 *
 * Renders user messages with collapsible large content support.
 * Extracted from App.tsx for better modularity.
 */

import React, { useRef, useState, useMemo } from 'react';
import {
  USER_MESSAGE_AUTO_COLLAPSE_THRESHOLD,
  USER_MESSAGE_COLLAPSED_LINES
} from '../../constants';
import { stripResumeContext } from '../../utils';
import { CopyButtons } from '../common';

export interface MessageBlockProps {
  sender: string;
  time: string;
  content: string;
  rightPanelCollapsed: boolean;
  isMobile: boolean;
  isLarge?: boolean;
  sizeDisplay?: string;
  processedText?: string;
}

export function MessageBlock({
  sender,
  time,
  content,
  rightPanelCollapsed,
  isMobile,
  isLarge,
  sizeDisplay,
  processedText,
}: MessageBlockProps): JSX.Element {
  const contentRef = useRef<HTMLDivElement>(null);
  const [isExpanded, setIsExpanded] = useState(false);

  // Match the layout of agent messages
  const showRightPanel = isMobile ? false : !rightPanelCollapsed;

  // Strip resume-context tags (LLM-only content, not for display)
  const fullContent = stripResumeContext(content);

  // For large messages, truncate to first N lines when collapsed
  // Collapse if: backend flagged as large OR content exceeds line threshold
  const lines = fullContent.split('\n');
  const totalLines = lines.length;
  const needsCollapse = (isLarge || totalLines > USER_MESSAGE_AUTO_COLLAPSE_THRESHOLD) && totalLines > USER_MESSAGE_COLLAPSED_LINES;

  // Compute size display if not provided by backend but message is large
  const computedSizeDisplay = useMemo(() => {
    if (sizeDisplay) return sizeDisplay;
    if (!needsCollapse) return undefined;
    const bytes = new Blob([fullContent]).size;
    if (bytes >= 1024 * 1024) {
      return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    } else if (bytes >= 1024) {
      return `${Math.round(bytes / 1024)}KB`;
    }
    return `${bytes}B`;
  }, [sizeDisplay, needsCollapse, fullContent]);

  const displayContent = useMemo(() => {
    if (!needsCollapse || isExpanded) {
      return fullContent;
    }
    return lines.slice(0, USER_MESSAGE_COLLAPSED_LINES).join('\n');
  }, [fullContent, lines, needsCollapse, isExpanded]);

  return (
    <div className={`message-block user-message ${isMobile ? 'mobile-layout' : ''} ${rightPanelCollapsed && !isMobile ? 'right-collapsed' : ''}`}>
      <div className="message-header">
        <span className="message-icon">⟩</span>
        <span className="message-sender">{sender}</span>
        <span className="message-time">@ {time}</span>
        {computedSizeDisplay && (
          <span className="message-size-badge">({computedSizeDisplay})</span>
        )}
        <CopyButtons contentRef={contentRef} markdown={fullContent} className="message-header-copy-buttons" />
      </div>
      <div className="message-body">
        <div className={`message-column-left ${!showRightPanel ? 'full-width' : ''}`}>
          <div className="collapsible-output">
            <div ref={contentRef} className="message-content">{displayContent}</div>
            {needsCollapse && (
              <button
                className="output-expand-toggle"
                onClick={() => setIsExpanded(!isExpanded)}
                type="button"
              >
                {isExpanded
                  ? '▲ Collapse'
                  : `▼ Expand All (${totalLines} lines)`
                }
              </button>
            )}
          </div>
          {isLarge && processedText && (
            <div className="large-input-notice">
              📁 Sent to agent as: <code>{processedText}</code>
            </div>
          )}
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
