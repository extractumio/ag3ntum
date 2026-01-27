/**
 * Agent message block component
 *
 * Renders agent responses with tool calls, subagents, todos, and result sections.
 * Extracted from App.tsx for better modularity.
 */

import React, { useRef } from 'react';
import type { ToolCallView, SubagentView, TodoItem, ResultStatus } from '../../types/conversation';
import {
  stripResumeContext,
  normalizeStatus,
  getStatusLabel,
  isMeaningfulError
} from '../../utils';
import { renderMarkdown } from '../../MarkdownRenderer';
import { CopyButtons } from '../common';
import { AgentSpinner, InlineStreamSpinner, TrailingWaitSpinner } from '../spinners';
import { ToolCallBlock } from './ToolCallBlock';
import { SubagentBlock } from './SubagentBlock';
import { AskUserQuestionBlock } from './AskUserQuestionBlock';
import { TodoProgressList } from './TodoProgressList';
import { ResultSection } from './ResultSection';

export interface AgentMessageBlockProps {
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
  /** Computed message status based on tool call outcomes */
  messageStatus?: ResultStatus;
  /** Error message from failed tools in this message */
  messageErrorMessage?: string;
  /** Agent-provided status of the overall user request */
  requestStatus?: ResultStatus;
  /** Agent-provided error message if request cannot be completed */
  requestErrorMessage?: string;
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
  onSubmitAnswer?: (answer: string) => void;
}

export function AgentMessageBlock({
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
  messageStatus,
  messageErrorMessage,
  requestStatus: _requestStatus,
  requestErrorMessage,
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
  onSubmitAnswer,
}: AgentMessageBlockProps): JSX.Element {
  const contentRef = useRef<HTMLDivElement>(null);
  // Strip resume-context tags (LLM-only content, not for display)
  const displayContent = stripResumeContext(content);
  const statusClass = status ? `agent-status-${status}` : '';
  const normalizedStatus = status ? (normalizeStatus(status) as ResultStatus) : undefined;
  const isTerminalStatus = normalizedStatus && normalizedStatus !== 'running';
  const statusLabel = getStatusLabel(normalizedStatus);
  const showFailureStatus = normalizedStatus === 'failed' || normalizedStatus === 'error' || normalizedStatus === 'cancelled';

  // Show inline spinner when streaming and no tool calls or subagents
  const showInlineSpinner = isStreaming && toolCalls.length === 0 && subagents.length === 0;
  // Show trailing wait spinner when message content is complete but session is still running
  // This indicates "more processing happening" even when tools are running (they have their own spinners too)
  const showTrailingWait = Boolean(displayContent) && !isStreaming && sessionRunning;

  // Separate AskUserQuestion tools (render inline) from other tools (render in right panel)
  const isAskUserQuestion = (tool: ToolCallView) =>
    tool.tool === 'AskUserQuestion' || tool.tool === 'mcp__ag3ntum__AskUserQuestion';
  const askUserQuestionTools = toolCalls.filter(isAskUserQuestion);
  const otherToolCalls = toolCalls.filter(t => !isAskUserQuestion(t));

  const hasOtherRightContent = otherToolCalls.length > 0 || subagents.length > 0 || Boolean(comments) || Boolean(files?.length);

  // Desktop: always show unless collapsed; Mobile: only show when expanded AND has content
  const showRightPanel = isMobile ? (hasOtherRightContent && mobileExpanded) : !rightPanelCollapsed;

  // Determine icon status class based on message status (computed from tool outcomes)
  const getIconStatusClass = (): string => {
    if (messageStatus === 'complete') return 'icon-status-complete';
    if (messageStatus === 'partial') return 'icon-status-partial';
    if (messageStatus === 'failed' || messageStatus === 'cancelled') return 'icon-status-failed';
    return '';
  };

  return (
    <div className={`message-block agent-message ${statusClass} ${isMobile ? 'mobile-layout' : ''} ${rightPanelCollapsed && !isMobile ? 'right-collapsed' : ''}`}>
      <div className="message-header">
        <span className={`message-icon ${getIconStatusClass()}`}>◆</span>
        <span className="message-sender">AGENT</span>
        <span className="message-time">@ {time}</span>
        {/* Message stats badges */}
        {(otherToolCalls.length > 0 || subagents.length > 0 || (files && files.length > 0)) && (
          <div className="message-stats">
            {otherToolCalls.length > 0 && (
              <span className="message-stat-badge stat-tools">
                <span className="stat-icon">◉</span>
                <span className="stat-label">Tools</span>
                <span className="stat-count">×{otherToolCalls.length}</span>
              </span>
            )}
            {subagents.length > 0 && (
              <span className="message-stat-badge stat-subagents">
                <span className="stat-icon">◉</span>
                <span className="stat-label">SubAgents</span>
                <span className="stat-count">×{subagents.length}</span>
              </span>
            )}
            {files && files.length > 0 && (
              <span className="message-stat-badge stat-files">
                <span className="stat-icon">◉</span>
                <span className="stat-label">Files</span>
                <span className="stat-count">×{files.length}</span>
              </span>
            )}
          </div>
        )}
        {displayContent && <CopyButtons contentRef={contentRef} markdown={displayContent} className="message-header-copy-buttons" />}
        {isMobile && hasOtherRightContent && (
          <button
            type="button"
            className={`mobile-expand-button ${mobileExpanded ? 'expanded' : ''}`}
            onClick={onToggleMobileExpand}
            title={mobileExpanded ? 'Hide details' : 'Show details'}
          >
            {mobileExpanded ? '▲ Hide' : '▼ Details'} ({otherToolCalls.length + subagents.length})
          </button>
        )}
      </div>
      <div className="message-body">
        <div className={`message-column-left ${!showRightPanel ? 'full-width' : ''}`}>
          <div ref={contentRef} className="message-content md-container">
            {displayContent ? (
              <>
                {renderMarkdown(displayContent)}
                {showInlineSpinner && <InlineStreamSpinner />}
                {showTrailingWait && <TrailingWaitSpinner />}
              </>
            ) : null}
            {!displayContent && !isTerminalStatus && !showInlineSpinner && askUserQuestionTools.length === 0 && <AgentSpinner />}
            {!displayContent && isTerminalStatus && showFailureStatus && (
              <div className="agent-status-indicator">✗ {statusLabel || 'Stopped'}</div>
            )}
            {/* Error messages only - status is shown via icon color */}
            {(isMeaningfulError(messageErrorMessage) || isMeaningfulError(requestErrorMessage)) && (
              <div className="agent-status-meta">
                {isMeaningfulError(messageErrorMessage) && (
                  <div className="agent-status-error message-error">
                    <span className="error-label">Tool Error:</span>
                    <span className="error-value">{messageErrorMessage}</span>
                  </div>
                )}
                {isMeaningfulError(requestErrorMessage) && (
                  <div className="agent-status-error request-error">
                    <span className="error-label">Request Error:</span>
                    <span className="error-value">{requestErrorMessage}</span>
                  </div>
                )}
              </div>
            )}
            {/* Render AskUserQuestion inline in message content */}
            {askUserQuestionTools.map((tool) => (
              <AskUserQuestionBlock
                key={tool.id}
                tool={tool}
                onSubmitAnswer={onSubmitAnswer || (() => {})}
                isLast={true}
                inline={true}
                sessionStatus={status}
              />
            ))}
            {todos && todos.length > 0 && (
              <TodoProgressList todos={todos} overallStatus={normalizedStatus} />
            )}
          </div>
        </div>
        {showRightPanel && (
          <div className={`message-column-right ${isMobile ? 'mobile-stacked' : ''}`}>
            {otherToolCalls.length > 0 && (
              <div className="tool-call-section">
                <div className="tool-call-title">Tool Calls ({otherToolCalls.length})</div>
                {otherToolCalls.map((tool, index) => (
                  <ToolCallBlock
                    key={tool.id}
                    tool={tool}
                    expanded={toolExpanded.has(tool.id)}
                    onToggle={() => onToggleTool(tool.id)}
                    isLast={index === otherToolCalls.length - 1}
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
