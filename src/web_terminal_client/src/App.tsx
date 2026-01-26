import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import YAML from 'yaml';

import {
  cancelSession,
  continueTask,
  deleteSession,
  downloadFile,
  getConfig,
  getFileContent,
  getFileDownloadUrl,
  getSession,
  getSessionEvents,
  getSkillsCached,
  invalidateSessionsCache,
  listSessionsCached,
  runTask,
  uploadFiles,
} from './api';
import { AuthProvider, useAuth } from './AuthContext';
import { loadConfig } from './config';
import {
  BLOCKED_HOTKEY_CODES,
  COLLAPSED_LINE_COUNT,
  EMPTY_EVENTS,
  OUTPUT_STATUS_CLASS,
  SPINNER_FRAMES,
  STATUS_CLASS,
  STATUS_LABELS,
  TOOL_COLOR_CLASS,
  TOOL_SYMBOL,
  USER_MESSAGE_AUTO_COLLAPSE_THRESHOLD,
  USER_MESSAGE_COLLAPSED_LINES,
} from './constants';
import { FileExplorer } from './FileExplorer';
import {
  AgentMessageContext,
  FileViewerModal,
  toFileViewerData,
  type FileViewerData,
} from './FileViewer';
import { renderMarkdownElements } from './MarkdownRenderer';
import { ProtectedRoute } from './ProtectedRoute';
import { connectSSE, connectUserEventsSSE, type UserEvent } from './sse';
import type { AppConfig, SessionListResponse, SessionResponse, SkillInfo, TerminalEvent } from './types';
import type {
  AskUserQuestionInput,
  AskUserQuestionOption,
  ConversationItem,
  ResultStatus,
  StructuredMessage,
  SubagentView,
  SystemEventView,
  TodoItem,
  ToolCallView,
} from './types/conversation';
import {
  AgentSpinner,
  CheckIconSvg,
  CopyButtons,
  CopyIconSvg,
  DownloadIcon,
  EyeIcon,
  FolderIcon,
  InlineStreamSpinner,
  Popup,
  PulsingCircleSpinner,
  QueueIndicator,
  SessionListTab,
  StatusSpinner,
  TrailingWaitSpinner,
  useSessionBadges,
  useToast,
} from './components';

// Terminal statuses that should not be overwritten by stale server data or events.
// This set is used throughout session state management to prevent race conditions
// where the server hasn't persisted a terminal status yet but the frontend already knows.
const TERMINAL_STATUSES = new Set(['complete', 'completed', 'partial', 'failed', 'cancelled', 'canceled']);
import { useElapsedTime, useSpinnerFrame } from './hooks';
import {
  blockAltKeyHotkeys,
  coerceStructuredFields,
  copyAsMarkdown,
  copyAsRichText,
  extractFilePaths,
  extractSubagentPreview,
  extractTodos,
  findTrailingHeader,
  formatCost,
  formatDuration,
  formatOutputAsYaml,
  formatTimestamp,
  formatToolInput,
  formatToolName,
  getLastServerSequence,
  getStatusLabel,
  isMeaningfulError,
  isSafeRelativePath,
  isValidSessionId,
  normalizeStatus,
  parseHeaderBlock,
  parseStructuredMessage,
  pythonReprToJson,
  seedSessionEvents,
  stripResumeContext,
  truncateSessionTitle,
} from './utils';

// Wrapper for shared markdown renderer - uses 'md' class prefix for agent messages
function renderMarkdown(text: string): JSX.Element[] {
  // Replace trailing colon with period for cleaner message display
  const processedText = text.replace(/:\s*$/, '.');
  return renderMarkdownElements(processedText, 'md');
}

// Collapsible output component with first N lines visible
function CollapsibleOutput({
  output,
  className
}: {
  output: string;
  className?: string;
}): JSX.Element {
  const [isExpanded, setIsExpanded] = useState(false);

  const { formatted, isYaml } = useMemo(() => formatOutputAsYaml(output), [output]);
  const lines = useMemo(() => formatted.split('\n'), [formatted]);
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

function SkillTag({
  id,
  name,
  description,
  onClick
}: {
  id: string;
  name: string;
  description: string;
  onClick: () => void;
}): JSX.Element {
  return (
    <span
      className="skill-tag"
      onClick={onClick}
      title={description || `Run /${id}`}
    >
      <span className="skill-symbol">⚡</span>
      <span className="skill-name">{name}</span>
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
        className={`copy-icon-btn ${copiedRich ? 'copied' : ''}`}
        onClick={handleCopyRich}
        title="Copy entire conversation as rich text (with formatting)"
      >
        {copiedRich ? <CheckIconSvg /> : <CopyIconSvg />}
        <span className="copy-icon-label">R</span>
      </button>
      <button
        type="button"
        className={`copy-icon-btn ${copiedMd ? 'copied' : ''}`}
        onClick={handleCopyMd}
        title="Copy entire conversation as markdown"
      >
        {copiedMd ? <CheckIconSvg /> : <CopyIconSvg />}
        <span className="copy-icon-label">M</span>
      </button>
    </div>
  );
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

  return (
    <div className={`todo-progress${isDone ? ' todo-progress-done' : ''}`}>
      {todos.map((todo, index) => {
        const status = todo.status?.toLowerCase?.() ?? 'pending';
        const isActive = status === 'in_progress' && isRunning;
        const isCompleted = status === 'completed';
        const label = isActive && todo.activeForm ? todo.activeForm : todo.content;
        // Show cancel icon for in_progress items when session was cancelled/failed
        const showCancel = (isCancelled || isFailed) && status === 'in_progress';
        const bullet = showCancel
          ? '✗'
          : isCompleted
            ? '✓'
            : '•';

        return (
          <div
            key={`${todo.content}-${index}`}
            className={`todo-item todo-${status}${showCancel ? ' todo-cancelled' : ''}`}
          >
            {isActive ? (
              <PulsingCircleSpinner />
            ) : (
              <span className="todo-bullet">
                {bullet}
              </span>
            )}
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
  isLarge,
  sizeDisplay,
  processedText,
}: {
  sender: string;
  time: string;
  content: string;
  rightPanelCollapsed: boolean;
  isMobile: boolean;
  isLarge?: boolean;
  sizeDisplay?: string;
  processedText?: string;
}): JSX.Element {
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

// Interactive component for AskUserQuestion tool calls
// When inline=true, renders in message content area without tool panel styling
// Human-in-the-loop: The tool may "complete" but still need user input
function AskUserQuestionBlock({
  tool,
  onSubmitAnswer,
  isLast,
  inline = false,
  sessionStatus,
}: {
  tool: ToolCallView;
  onSubmitAnswer: (answer: string) => void;
  isLast: boolean;
  inline?: boolean;
  sessionStatus?: string;
}): JSX.Element {
  const isRunning = tool.status === 'running';
  const [selectedOptions, setSelectedOptions] = useState<Record<number, Set<string>>>({});
  const [hasAnswered, setHasAnswered] = useState(false);
  const [additionalComments, setAdditionalComments] = useState('');

  // Parse the input to get questions
  // Handle both direct input and JSON string format from MCP tools
  // Note: tool.input may be '' (empty string) if tool_input was undefined in the event
  const rawInput = tool.input;
  let input: AskUserQuestionInput | Record<string, unknown> | undefined;

  // Handle case where input is a string (could be empty or JSON stringified)
  if (typeof rawInput === 'string') {
    if (rawInput.trim() === '') {
      input = undefined;
    } else {
      // Try to parse JSON string
      try {
        input = JSON.parse(rawInput);
      } catch {
        input = undefined;
      }
    }
  } else {
    input = rawInput as AskUserQuestionInput | Record<string, unknown> | undefined;
  }

  let questions: AskUserQuestionInput['questions'] = [];

  if (input?.questions) {
    if (Array.isArray(input.questions)) {
      questions = input.questions;
    } else if (typeof input.questions === 'string') {
      // MCP tools may send questions as JSON string - parse it
      try {
        const parsed = JSON.parse(input.questions);
        if (Array.isArray(parsed)) {
          questions = parsed;
        }
      } catch {
        // Ignore parse errors - questions may be incomplete during streaming
      }
    }
  }

  // Human-in-the-loop: Tool may complete but session is waiting for input
  // In this case, we still want to allow user interaction
  const isWaitingForInput = sessionStatus === 'waiting_for_input' ||
    (tool.status === 'complete' && !tool.output?.includes('User has answered'));

  // Show as interactive if tool is running OR waiting for human input
  const isInteractive = tool.status === 'running' || (isWaitingForInput && !hasAnswered);

  // Status icon (only show in non-inline mode) - no emojis, use text symbols
  // Running state uses PulsingCircleSpinner component instead
  const statusIcon =
    isWaitingForInput ? '...' :
    tool.status === 'complete' ? '[ok]' :
    tool.status === 'failed' ? '[x]' : null;

  const statusClass =
    isWaitingForInput ? 'tool-status-waiting' :
    tool.status === 'complete' ? 'tool-status-success' :
    tool.status === 'failed' ? 'tool-status-error' :
    tool.status === 'running' ? 'tool-status-running' : '';

  const handleOptionClick = (questionIdx: number, optionLabel: string, multiSelect: boolean) => {
    if (!isInteractive) return;

    setSelectedOptions(prev => {
      const current = prev[questionIdx] || new Set<string>();
      const newSet = new Set(current);

      if (multiSelect) {
        if (newSet.has(optionLabel)) {
          newSet.delete(optionLabel);
        } else {
          newSet.add(optionLabel);
        }
      } else {
        // Single select - clear and set
        newSet.clear();
        newSet.add(optionLabel);
      }

      return { ...prev, [questionIdx]: newSet };
    });
  };

  const handleSubmit = () => {
    if (!isInteractive) return;

    // Build answer from selected options
    const answers: string[] = [];
    questions.forEach((q, idx) => {
      const selected = selectedOptions[idx];
      if (selected && selected.size > 0) {
        answers.push(Array.from(selected).join(', '));
      }
    });

    if (answers.length > 0) {
      setHasAnswered(true);
      // Include additional comments if provided
      const fullAnswer = additionalComments.trim()
        ? `${answers.join('\n')}\n\nAdditional Comments: ${additionalComments.trim()}`
        : answers.join('\n');
      onSubmitAnswer(fullAnswer);
    }
  };

  const hasSelection = Object.values(selectedOptions).some(s => s && s.size > 0);

  // Inline rendering for message content area
  if (inline) {
    // If no questions parsed, show a loading/debug state
    if (questions.length === 0) {
      return (
        <div className={`ask-user-question-inline ${statusClass}`}>
          <div className="ask-waiting-banner">
            <span className="ask-waiting-icon">[...]</span>
            Loading questions...
          </div>
          {tool.input && (
            <div className="ask-question-answer">
              <span className="ask-answer-label">Debug input:</span>
              <span className="ask-answer-value">{typeof tool.input === 'string' ? tool.input : JSON.stringify(tool.input)}</span>
            </div>
          )}
        </div>
      );
    }

    return (
      <div className={`ask-user-question-inline ${statusClass}`}>
        {isWaitingForInput && !hasAnswered && (
          <div className="ask-waiting-banner">
            <span className="ask-waiting-icon">[...]</span>
            The session is paused until you answer. 
          </div>
        )}

        {questions.map((q, qIdx) => (
          <div key={qIdx} className="ask-question-block">
            {q.header && <div className="ask-question-header">{q.header}</div>}
            <div className="ask-question-text">{q.question}</div>
            <div className="ask-question-options">
              {q.options.map((opt, optIdx) => {
                const isSelected = selectedOptions[qIdx]?.has(opt.label) || false;
                const isDisabled = !isInteractive;
                return (
                  <button
                    key={optIdx}
                    className={`ask-option-btn ${isSelected ? 'selected' : ''} ${isDisabled ? 'disabled' : ''}`}
                    onClick={() => handleOptionClick(qIdx, opt.label, q.multiSelect || false)}
                    disabled={isDisabled}
                    title={opt.description || opt.label}
                  >
                    <span className="ask-option-indicator"></span>
                    <span className="ask-option-label">{opt.label}</span>
                    {opt.description && (
                      <span className="ask-option-description">{opt.description}</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        {isInteractive && (
          <>
            <div className="ask-additional-comments">
              <div className="ask-additional-comments-label">Additional Comments</div>
              <textarea
                className="ask-additional-comments-textarea"
                placeholder="Optional: Add any additional context or comments..."
                value={additionalComments}
                onChange={(e) => setAdditionalComments(e.target.value)}
                onKeyDown={blockAltKeyHotkeys}
                rows={3}
              />
            </div>
            <div className="ask-question-actions">
              <button
                className={`ask-submit-btn ${!hasSelection ? 'disabled' : ''}`}
                onClick={handleSubmit}
                disabled={!hasSelection}
              >
                [ Submit Answer ]
              </button>
              <span className="ask-hint">Select an option above to submit your answer</span>
            </div>
          </>
        )}

        {hasAnswered && (
          <div className="ask-question-submitted">
            <span className="ask-submitted-icon">[ok]</span> Answer submitted - resuming session...
          </div>
        )}

        {tool.output && !isWaitingForInput && (
          <div className="ask-question-answer">
            <span className="ask-answer-label">Answer:</span>
            <span className="ask-answer-value">{tool.output}</span>
          </div>
        )}
      </div>
    );
  }

  // Tool panel rendering (original style)
  const treeChar = isLast ? '└──' : '├──';
  return (
    <div className={`tool-call ask-user-question ${statusClass}`}>
      <div className="tool-call-header">
        <span className="tool-tree">{treeChar}</span>
        {isRunning && (
          <span className={`tool-status-icon ${statusClass}`}><PulsingCircleSpinner /></span>
        )}
        {statusIcon && (
          <span className={`tool-status-icon ${statusClass}`}>{statusIcon}</span>
        )}
        <span className="ask-question-icon">[?]</span>
        <span className="tool-name">AskUserQuestion</span>
        <span className="tool-time">@ {tool.time}</span>
        {!isInteractive && tool.durationMs !== undefined && (
          <span className="tool-duration">({formatDuration(tool.durationMs)})</span>
        )}
      </div>

      <div className="ask-user-question-body">
        {isWaitingForInput && !hasAnswered && (
          <div className="ask-waiting-banner">
            <span className="ask-waiting-icon">[...]</span>
            Waiting for your response - the session is paused until you answer
          </div>
        )}

        {questions.map((q, qIdx) => (
          <div key={qIdx} className="ask-question-block">
            {q.header && <div className="ask-question-header">{q.header}</div>}
            <div className="ask-question-text">{q.question}</div>
            <div className="ask-question-options">
              {q.options.map((opt, optIdx) => {
                const isSelected = selectedOptions[qIdx]?.has(opt.label) || false;
                const isDisabled = !isInteractive;
                return (
                  <button
                    key={optIdx}
                    className={`ask-option-btn ${isSelected ? 'selected' : ''} ${isDisabled ? 'disabled' : ''}`}
                    onClick={() => handleOptionClick(qIdx, opt.label, q.multiSelect || false)}
                    disabled={isDisabled}
                    title={opt.description || opt.label}
                  >
                    <span className="ask-option-indicator"></span>
                    <span className="ask-option-label">{opt.label}</span>
                    {opt.description && (
                      <span className="ask-option-description">{opt.description}</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        {isInteractive && (
          <>
            <div className="ask-additional-comments">
              <div className="ask-additional-comments-label">Additional Comments</div>
              <textarea
                className="ask-additional-comments-textarea"
                placeholder="Optional: Add any additional context or comments..."
                value={additionalComments}
                onChange={(e) => setAdditionalComments(e.target.value)}
                onKeyDown={blockAltKeyHotkeys}
                rows={3}
              />
            </div>
            <div className="ask-question-actions">
              <button
                className={`ask-submit-btn ${!hasSelection ? 'disabled' : ''}`}
                onClick={handleSubmit}
                disabled={!hasSelection}
              >
                [ Submit Answer ]
              </button>
              <span className="ask-hint">Select an option above to submit your answer</span>
            </div>
          </>
        )}

        {hasAnswered && (
          <div className="ask-question-submitted">
            <span className="ask-submitted-icon">[ok]</span> Answer submitted - resuming session...
          </div>
        )}

        {tool.output && !isWaitingForInput && (
          <div className="ask-question-answer">
            <span className="ask-answer-label">Answer:</span>
            <span className="ask-answer-value">{tool.output}</span>
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
            {event.eventType === 'queue_started' && (
              <>
                <span className="system-event-badge-info">▶ STARTED</span>
                <span className="system-event-message">{event.message}</span>
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
  onShowInExplorer,
}: {
  comments?: string;
  commentsExpanded?: boolean;
  onToggleComments?: () => void;
  files?: string[];
  filesExpanded?: boolean;
  onToggleFiles?: () => void;
  onFileAction?: (filePath: string, mode: 'view' | 'download') => void;
  onShowInExplorer?: (filePath: string) => void;
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
                  <div className="result-file-actions">
                    {onFileAction && (
                      <>
                        <button
                          type="button"
                          className="result-file-action"
                          onClick={() => onFileAction(file, 'view')}
                          title="View file"
                        >
                          <EyeIcon />
                        </button>
                        <button
                          type="button"
                          className="result-file-action"
                          onClick={() => onFileAction(file, 'download')}
                          title="Download file"
                        >
                          <DownloadIcon />
                        </button>
                      </>
                    )}
                    {onShowInExplorer && (
                      <button
                        type="button"
                        className="result-file-action"
                        onClick={() => onShowInExplorer(file)}
                        title="Show in File Explorer"
                      >
                        <FolderIcon />
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Right panel details component - shows tool calls, subagents, and files for selected message
function RightPanelDetails({
  message,
  toolExpanded,
  onToggleTool,
  subagentExpanded,
  onToggleSubagent,
  commentsExpanded,
  onToggleComments,
  filesExpanded,
  onToggleFiles,
  onFileAction,
  onShowInExplorer,
  onExpandAll,
  onCollapseAll,
}: {
  message: ConversationItem | null;
  toolExpanded: Set<string>;
  onToggleTool: (id: string) => void;
  subagentExpanded: Set<string>;
  onToggleSubagent: (id: string) => void;
  commentsExpanded: boolean;
  onToggleComments: () => void;
  filesExpanded: boolean;
  onToggleFiles: () => void;
  onFileAction?: (file: string, action: 'view' | 'download') => void;
  onShowInExplorer?: (filePath: string) => void;
  onExpandAll: () => void;
  onCollapseAll: () => void;
}): JSX.Element {
  if (!message) {
    return (
      <div className="right-panel-empty">
        Select a message to view details
      </div>
    );
  }

  // Extract data based on message type
  if (message.type === 'user') {
    return (
      <div className="right-panel-empty">
        No details available for user messages
      </div>
    );
  }

  // Type narrowing for agent_message and output types
  const toolCalls = message.type === 'agent_message' ? message.toolCalls : [];
  const subagents = message.type === 'agent_message' ? message.subagents : [];
  const comments = message.comments;
  const files = message.files;

  const hasContent = toolCalls.length > 0 || subagents.length > 0 || Boolean(comments) || Boolean(files?.length);

  if (!hasContent) {
    return (
      <div className="right-panel-empty">
        No tool calls or files for this message
      </div>
    );
  }

  return (
    <div className="right-panel-details">
      {/* Expand/Collapse All Buttons */}
      <div className="right-panel-actions">
        <button className="filter-button" type="button" onClick={onExpandAll} title="Expand all sections (Alt+[)">
          [expand all]
        </button>
        <button className="filter-button" type="button" onClick={onCollapseAll} title="Collapse all sections (Alt+])">
          [collapse all]
        </button>
      </div>

      {/* Tool Calls */}
      {toolCalls.length > 0 && (
        <div className="tool-call-section">
          <div className="section-title">Tool Calls ({toolCalls.length})</div>
          {toolCalls.map((tool: ToolCallView, idx: number) => (
            <ToolCallBlock
              key={tool.id}
              tool={tool}
              expanded={toolExpanded.has(tool.id)}
              onToggle={() => onToggleTool(tool.id)}
              isLast={idx === toolCalls.length - 1}
            />
          ))}
        </div>
      )}

      {/* SubAgents */}
      {subagents.length > 0 && (
        <div className="subagent-section">
          <div className="section-title">SubAgents ({subagents.length})</div>
          {subagents.map((sub: SubagentView, idx: number) => (
            <SubagentBlock
              key={sub.id}
              subagent={sub}
              expanded={subagentExpanded.has(sub.id)}
              onToggle={() => onToggleSubagent(sub.id)}
              isLast={idx === subagents.length - 1}
            />
          ))}
        </div>
      )}

      {/* Result Section - Comments and Files */}
      {(Boolean(comments) || Boolean(files?.length)) && (
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
  onSubmitAnswer,
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
  onSubmitAnswer?: (answer: string) => void;
}): JSX.Element {
  const contentRef = useRef<HTMLDivElement>(null);
  // Strip resume-context tags (LLM-only content, not for display)
  const displayContent = stripResumeContext(content);
  const statusClass = status ? `agent-status-${status}` : '';
  const normalizedStatus = status ? (normalizeStatus(status) as ResultStatus) : undefined;
  const isTerminalStatus = normalizedStatus && normalizedStatus !== 'running';
  const statusLabel = getStatusLabel(normalizedStatus);
  const showFailureStatus = normalizedStatus === 'failed' || normalizedStatus === 'cancelled';
  const structuredStatusLabel = structuredStatus === 'failed' ? getStatusLabel(structuredStatus) : '';
  // Show inline spinner when streaming and no tool calls or subagents
  const showInlineSpinner = isStreaming && toolCalls.length === 0 && subagents.length === 0;
  // Show trailing wait spinner when message content is complete but session is still running
  // This indicates "more processing happening" even when tools are running (they have their own spinners too)
  const showTrailingWait = Boolean(displayContent) && !isStreaming && sessionRunning;

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

  // Separate AskUserQuestion tools from other tools - they render inline in message
  // Handle both native SDK tool name and MCP tool name
  const askUserQuestionTools = toolCalls.filter(t =>
    t.tool === 'AskUserQuestion' || t.tool === 'mcp__ag3ntum__AskUserQuestion'
  );
  const otherToolCalls = toolCalls.filter(t =>
    t.tool !== 'AskUserQuestion' && t.tool !== 'mcp__ag3ntum__AskUserQuestion'
  );

  const hasOtherRightContent = otherToolCalls.length > 0 || subagents.length > 0 || Boolean(comments) || Boolean(files?.length);

  return (
    <div className={`message-block agent-message ${statusClass} ${isMobile ? 'mobile-layout' : ''} ${rightPanelCollapsed && !isMobile ? 'right-collapsed' : ''}`}>
      <div className="message-header">
        <span className="message-icon">◆</span>
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
            {((structuredStatusLabel && structuredStatus === 'failed') || isMeaningfulError(structuredError)) && (
              <div className="agent-structured-meta">
                {structuredStatusLabel && structuredStatus === 'failed' && (
                  <div className="agent-structured-status">Status: {structuredStatusLabel}</div>
                )}
                {isMeaningfulError(structuredError) && (
                  <div className="agent-structured-error">Error: {structuredError}</div>
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
  onShowInExplorer,
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
  onShowInExplorer?: (filePath: string) => void;
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
              onShowInExplorer={onShowInExplorer}
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

// Models are loaded dynamically from agent.yaml via the API config endpoint

/**
 * Format a model name for display in the dropdown.
 *
 * Transforms model identifiers into user-friendly names:
 * - Removes 'claude-' prefix
 * - Removes date suffix (e.g., '-20250929')
 * - Replaces ':mode=thinking' suffix with ' [thinking]' indicator
 *
 * Examples:
 * - 'claude-sonnet-4-5-20250929' -> 'sonnet-4-5'
 * - 'claude-sonnet-4-5-20250929:mode=thinking' -> 'sonnet-4-5 [thinking]'
 * - 'claude-haiku-4-5-20251001:mode=thinking' -> 'haiku-4-5 [thinking]'
 */
function formatModelName(model: string): string {
  let displayName = model;

  // Check if thinking mode is enabled
  const isThinking = model.endsWith(':mode=thinking');
  if (isThinking) {
    displayName = displayName.replace(':mode=thinking', '');
  }

  // Remove 'claude-' prefix
  displayName = displayName.replace(/^claude-/, '');

  // Remove date suffix (8-digit date at end)
  displayName = displayName.replace(/-\d{8}$/, '');

  // Add thinking indicator if applicable
  if (isThinking) {
    displayName += ' [thinking]';
  }

  return displayName;
}

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

    // Check for text/plain first (filename from file explorer drag)
    const textData = e.dataTransfer.getData('text/plain');
    if (textData && !e.dataTransfer.files.length) {
      // Insert the filename at cursor position or append to value
      const textarea = textareaRef.current;
      if (textarea) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const newValue = value.slice(0, start) + textData + value.slice(end);
        onChange(newValue);
        // Set cursor position after inserted text
        setTimeout(() => {
          textarea.selectionStart = textarea.selectionEnd = start + textData.length;
          textarea.focus();
        }, 0);
      } else {
        onChange(value + (value && !value.endsWith(' ') ? ' ' : '') + textData);
      }
      return;
    }

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
    // Block Alt+key hotkeys from inserting special characters
    if (blockAltKeyHotkeys(e)) return;

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
              {formatModelName(model)}
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
                  {formatModelName(m)}
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
  isQueued,
  queuePosition,
  isAutoResume,
  statusLabel,
  statusClass,
  stats,
  connectionState,
  startTime,
}: {
  isRunning: boolean;
  isQueued: boolean;
  queuePosition: number | null;
  isAutoResume: boolean;
  statusLabel: string;
  statusClass: string;
  stats: {
    turns: number;
    tokensIn: number;
    tokensOut: number;
    cost: number;
    durationMs: number;
  };
  connectionState: 'connected' | 'reconnecting' | 'polling' | 'degraded' | 'disconnected';
  startTime: string | null;
}): JSX.Element {
  const elapsedTime = useElapsedTime(startTime, isRunning);

  const connectionDisplay = {
    connected: { icon: '●', label: 'Connected', className: 'connected' },
    reconnecting: { icon: '●', label: 'Reconnecting...', className: 'reconnecting' },
    polling: { icon: '●', label: 'Connected (polling)', className: 'polling' },
    degraded: { icon: '●', label: 'Connection issues...', className: 'degraded' },
    disconnected: { icon: '●', label: 'Disconnected', className: 'disconnected' },
  }[connectionState];

  return (
    <div className="terminal-status">
      <div className="status-left">
        <span className={`status-connection ${connectionDisplay.className}`}>
          {connectionDisplay.icon} {connectionDisplay.label}
        </span>
        <span className="status-divider">│</span>
        <span className={`status-state ${statusClass}`}>
          {isQueued ? (
            <QueueIndicator position={queuePosition ?? 0} isAutoResume={isAutoResume} />
          ) : isRunning ? (
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

// localStorage helpers for model preference
function getStoredSelectedModel(): string | null {
  try {
    return localStorage.getItem('ag3ntum_selected_model');
  } catch {
    return null;
  }
}

function setStoredSelectedModel(model: string): void {
  try {
    localStorage.setItem('ag3ntum_selected_model', model);
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
  // Session error popup state (for prominent error display with navigation)
  const [sessionErrorPopup, setSessionErrorPopup] = useState<{
    message: string;
    sessionId: string;
  } | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [connectionState, setConnectionState] = useState<'connected' | 'reconnecting' | 'polling' | 'degraded'>('connected');
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [expandedSubagents, setExpandedSubagents] = useState<Set<string>>(new Set());
  const [expandedComments, setExpandedComments] = useState<Set<string>>(new Set());
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState<boolean>(() => getStoredPanelCollapsed());
  const [mobileExpandedMessages, setMobileExpandedMessages] = useState<Set<string>>(new Set());
  const [systemEventsExpanded, setSystemEventsExpanded] = useState(false);
  const [fileExplorerVisible, setFileExplorerVisible] = useState(false);
  // New layout state
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [rightPanelMode, setRightPanelMode] = useState<'details' | 'explorer' | 'sessions'>('details');
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false);
  // Session badges for non-current session status changes
  const { badges: sessionBadges, addBadge: addSessionBadge, clearBadge: clearSessionBadge, badgeCounts: sessionBadgeCounts } = useSessionBadges(currentSession?.id ?? null);
  // Resizable panel state
  const [rightPanelWidth, setRightPanelWidth] = useState<number>(() => {
    const stored = localStorage.getItem('rightPanelWidth');
    return stored ? parseInt(stored, 10) : 400;
  });
  const [isDraggingDivider, setIsDraggingDivider] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const [fileExplorerRefreshKey, setFileExplorerRefreshKey] = useState(0);
  const [fileExplorerModalOpen, setFileExplorerModalOpen] = useState(false);
  const [showHiddenFiles, setShowHiddenFiles] = useState(false);
  const [navigateToPath, setNavigateToPath] = useState<string | null>(null);
  const [stats, setStats] = useState({
    turns: 0,
    cost: 0,
    durationMs: 0,
    tokensIn: 0,
    tokensOut: 0,
    model: '',
  });
  const [runningStartTime, setRunningStartTime] = useState<string | null>(null);
  const [loadedSkills, setLoadedSkills] = useState<SkillInfo[]>([]);
  // File viewer modal state
  const [viewerFile, setViewerFile] = useState<FileViewerData | null>(null);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerImageUrl, setViewerImageUrl] = useState<string | undefined>(undefined);

  // Security alert state (for sensitive data detection notifications)
  const [securityAlert, setSecurityAlert] = useState<{
    message: string;
    typeLabels: string[];
    filesWithSecrets: number;
    totalSecrets: number;
  } | null>(null);

  // Toast notifications
  const toast = useToast();

  const isMobile = useIsMobile();

  const outputRef = useRef<HTMLDivElement | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const activeTurnRef = useRef(0);
  const lastStableRightPanelMessageRef = useRef<ConversationItem | null>(null);

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
        // Check for stored model preference
        const storedModel = getStoredSelectedModel();
        if (storedModel && apiConfig.models_available.includes(storedModel)) {
          // Use stored model if it's still available
          setSelectedModel(storedModel);
        } else {
          // Fall back to default model
          setSelectedModel(apiConfig.default_model);
        }
      })
      .catch((err) => {
        console.error('Failed to load API config:', err);
      });
  }, [config]);

  // Load available skills
  useEffect(() => {
    if (!config || !token) {
      return;
    }
    getSkillsCached(config.api.base_url, token)
      .then((response: { skills: SkillInfo[] }) => {
        setLoadedSkills(response.skills);
      })
      .catch((err: Error) => {
        console.error('Failed to load skills:', err);
      });
  }, [config, token]);

  const refreshSessions = useCallback(() => {
    if (!config || !token) {
      return;
    }

    listSessionsCached(config.api.base_url, token)
      .then((response: SessionListResponse) => {
        // Merge server sessions with local state using defensive merging:
        // 1. Terminal statuses are "sticky" - can't be reverted to non-terminal
        // 2. Newer local timestamps take precedence over stale server data
        setSessions((prevSessions) => {
          const localSessionMap = new Map<string, SessionResponse>();
          for (const session of prevSessions) {
            localSessionMap.set(session.id, session);
          }

          return response.sessions.map((serverSession) => {
            const localSession = localSessionMap.get(serverSession.id);
            if (!localSession) {
              // New session from server, use server data
              return serverSession;
            }

            // RULE 1: If local is terminal and server is non-terminal, keep local
            // (Server hasn't caught up to the terminal event yet)
            if (TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverSession.status)) {
              return localSession;
            }

            // RULE 2: If local has newer timestamp and is terminal, keep local
            // (Local state was updated more recently and reached a final state)
            const localTime = new Date(localSession.updated_at).getTime();
            const serverTime = new Date(serverSession.updated_at).getTime();
            if (localTime > serverTime && TERMINAL_STATUSES.has(localSession.status)) {
              return localSession;
            }

            // Otherwise, use server data (it's fresher or both are terminal)
            return serverSession;
          });
        });
      })
      .catch((err: Error) => setError(`Failed to load sessions: ${err.message}`));
  }, [config, token]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  // Sync sessions[] from currentSession when currentSession has terminal status but sessions[] doesn't
  // This is a ONE-WAY sync: currentSession → sessions[], never the reverse.
  //
  // Why one-way only?
  // - When user clicks "Continue" on a completed session, handleSubmit sets currentSession to 'running'
  // - If we synced FROM sessions[] TO currentSession, we'd overwrite the intentional 'running' back to 'completed'
  // - Terminal events always come through Session SSE which updates BOTH currentSession and sessions[]
  // - So there's no legitimate case where sessions[] has newer terminal info than currentSession
  useEffect(() => {
    if (!currentSession) return;

    const matchingSession = sessions.find((s) => s.id === currentSession.id);
    if (!matchingSession) return;

    // Only sync if currentSession is terminal and sessions[] is not
    // This handles: User Events SSE sent stale 'running' status for current session
    if (
      matchingSession.status !== currentSession.status &&
      TERMINAL_STATUSES.has(currentSession.status) &&
      !TERMINAL_STATUSES.has(matchingSession.status)
    ) {
      setSessions((prev) =>
        prev.map((s) => (s.id === currentSession.id ? { ...s, status: currentSession.status } : s))
      );
    }
  }, [currentSession, sessions]);

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

  // User-level SSE subscription for cross-session updates (badges, status changes)
  const userEventsCleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!config || !token) {
      return;
    }

    // Clean up any existing subscription
    if (userEventsCleanupRef.current) {
      userEventsCleanupRef.current();
    }

    const cleanup = connectUserEventsSSE({
      baseUrl: config.api.base_url,
      token,
      onEvent: (event: UserEvent) => {
        // Handle specific session status change events
        if (event.type === 'session_status_change') {
          const change = event.data as {
            id: string;
            old_status: string;
            new_status: string;
            queue_position?: number;
          } | undefined;

          if (change) {
            // Update the session in our list, with terminal status protection
            setSessions((prevSessions) =>
              prevSessions.map((session) => {
                if (session.id !== change.id) return session;

                // CRITICAL: Don't overwrite terminal status with non-terminal status
                // This prevents race conditions where server sends stale status updates
                if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(change.new_status)) {
                  return session;
                }

                return { ...session, status: change.new_status, queue_position: change.queue_position ?? null };
              })
            );

            // Add badge for status changes on non-current sessions
            if (change.id !== currentSession?.id) {
              if (change.new_status === 'complete' || change.new_status === 'completed') {
                addSessionBadge(change.id, 'completed');
              } else if (change.new_status === 'failed') {
                addSessionBadge(change.id, 'failed');
              } else if (change.new_status === 'waiting_for_input') {
                addSessionBadge(change.id, 'waiting');
              }
            }
          }
        }

        // Handle bulk session list updates
        if (event.type === 'session_list_update') {
          // Update sessions list with new data
          const sessionList = event.data?.sessions as Array<{
            id: string;
            status: string;
            queue_position?: number;
            is_auto_resume?: boolean;
          }> | undefined;

          if (sessionList) {
            // Update session statuses in our local list, with terminal status protection
            setSessions((prevSessions) =>
              prevSessions.map((session) => {
                const updated = sessionList.find((s) => s.id === session.id);
                if (!updated) return session;

                // CRITICAL: Don't overwrite terminal status with non-terminal status
                // This prevents race conditions where server sends stale status updates
                if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(updated.status)) {
                  return session;
                }

                // Check if status changed for badge purposes
                if (session.status !== updated.status && session.id !== currentSession?.id) {
                  // Add badge based on new status
                  if (updated.status === 'complete' || updated.status === 'completed') {
                    addSessionBadge(session.id, 'completed');
                  } else if (updated.status === 'failed') {
                    addSessionBadge(session.id, 'failed');
                  } else if (updated.status === 'waiting_for_input') {
                    addSessionBadge(session.id, 'waiting');
                  }
                }
                return {
                  ...session,
                  status: updated.status,
                  queue_position: updated.queue_position ?? null,
                  is_auto_resume: updated.is_auto_resume ?? false,
                };
              })
            );
          }
        }
      },
      onError: (error: Error) => {
        console.warn('[UserEventsSSE] Error:', error.message);
      },
    });

    userEventsCleanupRef.current = cleanup;

    return () => {
      if (userEventsCleanupRef.current) {
        userEventsCleanupRef.current();
        userEventsCleanupRef.current = null;
      }
    };
  }, [config, token, currentSession?.id, addSessionBadge]);

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
          tokensIn: usage ? prev.tokensIn + newTokensIn : prev.tokensIn,
          tokensOut: usage ? prev.tokensOut + newTokensOut : prev.tokensOut,
        }));
        setStatus(normalizedStatus);
        setRunningStartTime(null);

        const completedAt = new Date().toISOString();
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: normalizedStatus,
                completed_at: completedAt,
                updated_at: completedAt,
                num_turns: prev.num_turns + Number(event.data.num_turns ?? 0),
              }
            : null
        );
        // Update sessions list with timestamps so elapsed timer stops immediately
        setSessions((prev) =>
          prev.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: normalizedStatus, completed_at: completedAt, updated_at: completedAt }
              : session
          )
        );

        // Invalidate cache and refresh to sync with server
        invalidateSessionsCache();
        refreshSessions();
      }

      if (event.type === 'cancelled') {
        setStatus('cancelled');
        setRunningStartTime(null);
        // Check if session is resumable (has resume_id established)
        const resumable = Boolean(event.data?.resumable);
        const cancelledAt = new Date().toISOString();
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'cancelled',
                completed_at: cancelledAt,
                updated_at: cancelledAt,
                resumable,
              }
            : null
        );
        // Update sessions list with timestamps so elapsed timer stops immediately
        setSessions((prev) =>
          prev.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: 'cancelled', completed_at: cancelledAt, updated_at: cancelledAt }
              : session
          )
        );
        // Invalidate cache and refresh to sync with server
        invalidateSessionsCache();
        refreshSessions();
      }

      // Handle queue_started event - task started after being queued
      if (event.type === 'queue_started') {
        // Update status from 'queued' to 'running'
        setStatus('running');
        setRunningStartTime(new Date().toISOString());
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'running',
                queue_position: null,
                queued_at: null,
              }
            : null
        );
        // Update session in list
        setSessions((prevSessions) =>
          prevSessions.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: 'running', queue_position: null }
              : session
          )
        );
      }

      // Handle queue_position_update event - position changed while waiting in queue
      if (event.type === 'queue_position_update') {
        const newPosition = event.data?.position as number | undefined;
        if (newPosition !== undefined) {
          // Update current session's queue position
          setCurrentSession((prev) =>
            prev
              ? {
                  ...prev,
                  queue_position: newPosition,
                }
              : null
          );
          // Update session in list
          setSessions((prevSessions) =>
            prevSessions.map((session) =>
              session.id === currentSession?.id
                ? { ...session, queue_position: newPosition }
                : session
            )
          );
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
        setRunningStartTime(null);
        setError(String(event.data.message ?? 'Unknown error'));
        const nowIso = new Date().toISOString();
        // Update session status so next submit can continue rather than reset
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'failed',
                completed_at: nowIso,
                updated_at: nowIso,
              }
            : null
        );
        // Update sessions list with timestamps so elapsed timer stops
        setSessions((prevSessions) =>
          prevSessions.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: 'failed', completed_at: nowIso, updated_at: nowIso }
              : session
          )
        );
        // Invalidate cache and refresh to sync with server
        invalidateSessionsCache();
        refreshSessions();
      }

      // Handle security alert events (sensitive data detected and redacted)
      if (event.type === 'security_alert') {
        const alertData = event.data as {
          message?: string;
          type_labels?: string[];
          files_with_secrets?: number;
          total_secrets?: number;
        };
        setSecurityAlert({
          message: alertData.message ?? 'Sensitive data was detected and redacted.',
          typeLabels: alertData.type_labels ?? [],
          filesWithSecrets: alertData.files_with_secrets ?? 0,
          totalSecrets: alertData.total_secrets ?? 0,
        });
      }
    },
    [appendEvent, currentSession, refreshSessions]
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
          // Don't show error message - the status indicator shows reconnecting state
          // Only show error after many failed attempts (handled by connection state change)
          if (attempt > 3) {
            setError(`Reconnecting (attempt ${attempt})...`);
          }
        },
        lastSequence ?? null,
        // Heartbeat callback - can detect session completion from heartbeat
        (heartbeatData) => {
          if (heartbeatData.session_status &&
              ['completed', 'failed', 'cancelled'].includes(heartbeatData.session_status)) {
            // Session ended - invalidate cache and refresh to get final state
            invalidateSessionsCache();
            refreshSessions();
          }
        },
        // Connection state change callback
        (state) => {
          const previousState = connectionState;
          setConnectionState(state);
          if (state === 'connected') {
            setReconnecting(false);
            setError(null);
            // RESYNC ON RECONNECT: When transitioning to connected from degraded/reconnecting/polling,
            // refresh sessions to ensure we have the latest state (terminal status protection applies)
            if (previousState === 'reconnecting' || previousState === 'polling' || previousState === 'degraded') {
              invalidateSessionsCache();
              refreshSessions();
            }
          } else if (state === 'polling') {
            // Polling mode still works - just a different transport
            setError(null);
          } else if (state === 'degraded') {
            // Only show error text for truly degraded state
            setError('Connection unstable');
          } else if (state === 'reconnecting') {
            // Clear error - status indicator shows reconnecting state
            setError(null);
          }
        }
      );
    },
    [config, token, handleEvent, refreshSessions]
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

    // Helper to format file size for display
    const formatFileSizeForContext = (bytes: number): string => {
      if (bytes < 1024) return `${bytes}B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    };

    // Helper to upload files to a session and return file context string
    const uploadAndBuildContext = async (sessionId: string): Promise<string> => {
      if (attachedFiles.length === 0) return '';

      try {
        const result = await uploadFiles(
          config.api.base_url,
          token,
          sessionId,
          attachedFiles.map((f) => f.file)
        );

        let fileContext = '';
        if (result.uploaded.length > 0) {
          fileContext =
            '\n\n<uploaded-files>\nThe following files have been uploaded to the workspace:\n' +
            result.uploaded
              .map((f) => `- ${f.path} (${formatFileSizeForContext(f.size)}, ${f.mime_type})`)
              .join('\n') +
            '\n</uploaded-files>';
        }

        // Report errors but continue
        if (result.errors.length > 0) {
          setError(`Some files failed to upload: ${result.errors.join(', ')}`);
        }

        return fileContext;
      } catch (err) {
        // Log error but don't block the task
        console.error('File upload failed:', err);
        setError(`File upload failed: ${(err as Error).message}`);
        return '';
      }
    };

    // Build preliminary file context (describes files before upload completes)
    // Uses YAML format for structured metadata that the UI can render nicely
    const buildPreliminaryFileContext = (): string => {
      if (attachedFiles.length === 0) return '';

      // Security: Sanitize filename to prevent injection attacks
      // - Remove control characters and non-printable chars
      // - Remove path traversal attempts
      // - Truncate to reasonable length
      // - Escape special characters for YAML
      const sanitizeFilename = (name: string): string => {
        const MAX_FILENAME_LENGTH = 255;

        let sanitized = name
          // Remove null bytes and control characters (0x00-0x1F, 0x7F)
          .replace(/[\x00-\x1F\x7F]/g, '')
          // Remove path traversal sequences
          .replace(/\.\.\//g, '')
          .replace(/\.\.\\/g, '')
          // Remove leading/trailing dots and spaces (Windows issues)
          .replace(/^[\s.]+|[\s.]+$/g, '')
          // Replace multiple spaces/dots with single
          .replace(/\s+/g, ' ')
          // Remove characters that could break YAML or cause issues
          .replace(/[<>:"|?*\x00-\x1F]/g, '_');

        // Truncate if too long (preserve extension)
        if (sanitized.length > MAX_FILENAME_LENGTH) {
          const lastDot = sanitized.lastIndexOf('.');
          if (lastDot > 0 && sanitized.length - lastDot <= 10) {
            // Preserve extension (up to 10 chars)
            const ext = sanitized.slice(lastDot);
            const nameWithoutExt = sanitized.slice(0, MAX_FILENAME_LENGTH - ext.length - 3);
            sanitized = nameWithoutExt + '...' + ext;
          } else {
            sanitized = sanitized.slice(0, MAX_FILENAME_LENGTH - 3) + '...';
          }
        }

        // If completely empty after sanitization, use placeholder
        return sanitized || 'unnamed_file';
      };

      // Helper to get file extension (sanitized)
      const getExtension = (filename: string): string => {
        const lastDot = filename.lastIndexOf('.');
        if (lastDot <= 0) return '';
        // Limit extension to alphanumeric, max 10 chars
        const ext = filename.slice(lastDot + 1).toLowerCase();
        return ext.replace(/[^a-z0-9]/g, '').slice(0, 10);
      };

      // Security: Sanitize MIME type
      const sanitizeMimeType = (mime: string): string => {
        // MIME types should only contain: a-z, 0-9, /, -, +, .
        const sanitized = mime.toLowerCase().replace(/[^a-z0-9/\-+.]/g, '');
        // Limit length
        return sanitized.slice(0, 100);
      };

      // Build YAML entries for each file
      const fileEntries = attachedFiles.map((f) => {
        const file = f.file;
        const safeName = sanitizeFilename(file.name);
        const ext = getExtension(file.name);
        // Format last modified as ISO string if available
        const lastModified = file.lastModified
          ? new Date(file.lastModified).toISOString()
          : null;

        // Build YAML block for this file
        // Escape quotes and backslashes for YAML string safety
        const yamlEscape = (s: string) => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');

        let yaml = `- name: "${yamlEscape(safeName)}"`;
        yaml += `\n  size: ${Math.max(0, Math.floor(file.size))}`; // Ensure non-negative integer
        yaml += `\n  size_formatted: "${formatFileSizeForContext(file.size)}"`;
        if (file.type) {
          yaml += `\n  mime_type: "${yamlEscape(sanitizeMimeType(file.type))}"`;
        }
        if (ext) {
          yaml += `\n  extension: "${ext}"`;
        }
        if (lastModified) {
          yaml += `\n  last_modified: "${lastModified}"`;
        }
        return yaml;
      }).join('\n');

      return (
        '\n\n<attached-files>\nfiles:\n' + fileEntries + '\n</attached-files>'
      );
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
      // CONTINUE EXISTING SESSION
      // For continuing sessions, upload files first so they're available immediately

      // Close old SSE connection before appending user event
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }

      try {
        // Upload files first (if any) so they're available when agent starts
        const fileContext = await uploadAndBuildContext(currentSession.id);
        const fullTaskText = taskText + fileContext;

        const userEvent: TerminalEvent = {
          type: 'user_message',
          data: { text: fullTaskText },
          timestamp: new Date().toISOString(),
          sequence: Date.now(),
        };
        appendEvent(userEvent);

        const response = await continueTask(
          config.api.base_url,
          token,
          currentSession.id,
          fullTaskText,
          selectedModel
        );

        setCurrentSession((prev) => ({
          ...prev!,
          status: response.status,
          updated_at: new Date().toISOString(),
        }));

        // CRITICAL: Also update sessions[] directly so the session panel shows 'running'
        // We can't rely on refreshSessions() because terminal status protection would
        // keep the old 'completed' status if the server hasn't updated yet.
        setSessions((prev) =>
          prev.map((s) =>
            s.id === currentSession.id
              ? { ...s, status: response.status, updated_at: new Date().toISOString() }
              : s
          )
        );

        setInputValue('');
        setAttachedFiles([]);
        const lastSequence = getLastServerSequence(events);
        startSSE(currentSession.id, lastSequence);
        // Invalidate cache since session status changed
        invalidateSessionsCache();
        refreshSessions();
      } catch (err) {
        setStatus('failed');
        const errorMessage = (err as Error).message;
        if (errorMessage.includes('cannot be resumed')) {
          setError(
            'Session cannot be resumed. The agent was cancelled before it could start. ' +
              'Your next message will start a new session.'
          );
          setCurrentSession((prev) => (prev ? { ...prev, resumable: false } : null));
        } else {
          setError(`Failed to continue task: ${errorMessage}`);
        }
      }
    } else {
      // START NEW SESSION
      // For new sessions, include preliminary file context in task,
      // then upload files immediately after session is created

      const preliminaryFileContext = buildPreliminaryFileContext();
      const fullTaskText = taskText + preliminaryFileContext;

      const userEvent: TerminalEvent = {
        type: 'user_message',
        data: { text: fullTaskText },
        timestamp: new Date().toISOString(),
        sequence: Date.now(),
      };

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
        const response = await runTask(config.api.base_url, token, fullTaskText, selectedModel);
        const sessionId = response.session_id;

        // Upload files to the new session (agent will see them in workspace)
        if (attachedFiles.length > 0) {
          // Upload in background - don't block SSE start
          uploadFiles(
            config.api.base_url,
            token,
            sessionId,
            attachedFiles.map((f) => f.file)
          ).catch((err) => {
            console.error('File upload failed:', err);
            setError(`File upload failed: ${(err as Error).message}`);
          });
        }

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
          queue_position: response.queue_position ?? null,
          queued_at: response.status === 'queued' ? new Date().toISOString() : null,
          is_auto_resume: false,
        });
        setInputValue('');
        setAttachedFiles([]);
        startSSE(sessionId, null);
        // Invalidate cache since new session was created
        invalidateSessionsCache();
        refreshSessions();
        navigate(`/session/${sessionId}/`, { replace: true });
      } catch (err) {
        setStatus('failed');
        setError(`Failed to start task: ${(err as Error).message}`);
      }
    }
  };

  // Handler for AskUserQuestion tool responses (human-in-the-loop)
  // Submits answer to the database, then resumes the session
  const handleSubmitAnswer = useCallback(async (answer: string): Promise<void> => {
    if (!config || !token || !currentSession || !answer.trim()) {
      return;
    }

    const answerText = answer.trim();

    try {
      // Step 1: Submit answer to the API endpoint (stores in database)
      const response = await fetch(
        `${config.api.base_url}/api/v1/sessions/${currentSession.id}/answer`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify({
            question_id: 'latest',
            answer: answerText,
          }),
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const result = await response.json();

      // Step 2: If the session can be resumed, resume it automatically
      if (result.can_resume) {
        // Add an event showing the user's answer
        const answerEvent: TerminalEvent = {
          type: 'user_message',
          data: { text: `[Answer submitted: ${answerText}]` },
          timestamp: new Date().toISOString(),
          sequence: Date.now(),
        };
        appendEvent(answerEvent);

        // Resume the session to continue agent execution
        setStatus('running');
        setRunningStartTime(new Date().toISOString());

        try {
          const resumeResponse = await continueTask(
            config.api.base_url,
            token,
            currentSession.id,
            // Send a simple resume message - the answer is already in context
            'Continue with the user\'s answer.',
            selectedModel
          );

          setCurrentSession((prev) => ({
            ...prev!,
            status: resumeResponse.status,
            updated_at: new Date().toISOString(),
          }));

          // Start SSE to receive agent's continued response
          const lastSequence = getLastServerSequence(events);
          startSSE(currentSession.id, lastSequence);
        } catch (resumeErr) {
          setStatus('failed');
          setError(`Failed to resume session: ${(resumeErr as Error).message}`);
        }
      }
    } catch (err) {
      setError(`Failed to submit answer: ${(err as Error).message}`);
    }
  }, [config, token, currentSession, selectedModel, events, appendEvent, startSSE]);

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

  const handleModelChange = useCallback((model: string) => {
    setSelectedModel(model);
    setStoredSelectedModel(model);
  }, []);

  /**
   * Scroll output to bottom. If distance > 1000px, jump instantly.
   * When forceInstant is true, scrolls multiple times to catch lazy-loaded content.
   */
  const scrollOutputToBottom = useCallback((forceInstant = false) => {
    const doScroll = () => {
      if (!outputRef.current) return;
      const { scrollTop, scrollHeight, clientHeight } = outputRef.current;
      const distanceToBottom = scrollHeight - scrollTop - clientHeight;

      // Jump instantly if distance > 1000px or forceInstant is true
      if (forceInstant || distanceToBottom > 1000) {
        outputRef.current.scrollTop = scrollHeight;
      } else {
        outputRef.current.scrollTo({
          top: scrollHeight,
          behavior: 'smooth',
        });
      }
    };

    // First scroll immediately after next frame
    requestAnimationFrame(doScroll);

    // When loading a session, scroll multiple times to catch lazy-loaded content
    // (images, embedded documents, syntax highlighting, etc.)
    if (forceInstant) {
      setTimeout(doScroll, 50);   // After initial render
      setTimeout(doScroll, 150);  // After most content loads
      setTimeout(doScroll, 400);  // After images/heavy content
    }
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

      // Scroll to bottom after loading session (instant jump for long conversations)
      scrollOutputToBottom(true);

      // Sum up stats from ALL agent_complete events (for multi-turn/resumed sessions)
      const completionEvents = historyEvents.filter((event) => event.type === 'agent_complete');
      if (completionEvents.length > 0) {
        let totalTokensIn = 0;
        let totalTokensOut = 0;
        let totalDurationMs = 0;
        completionEvents.forEach((event) => {
          const usage = (event.data.usage ?? null) as
            | {
                input_tokens?: number;
                output_tokens?: number;
                cache_creation_input_tokens?: number;
                cache_read_input_tokens?: number;
              }
            | null;
          if (usage) {
            totalTokensIn +=
              (usage.input_tokens ?? 0) +
              (usage.cache_creation_input_tokens ?? 0) +
              (usage.cache_read_input_tokens ?? 0);
            totalTokensOut += usage.output_tokens ?? 0;
          }
          totalDurationMs += Number(event.data.duration_ms ?? 0);
        });

        // Use the last completion event for cumulative turns/cost (backend provides these)
        const lastCompletion = completionEvents[completionEvents.length - 1];
        const cumulativeTurns = Number(lastCompletion.data.cumulative_turns ?? 0);
        const cumulativeCost = Number(lastCompletion.data.cumulative_cost_usd ?? 0);

        setStats({
          turns: cumulativeTurns || Number(lastCompletion.data.num_turns ?? session.num_turns),
          cost: cumulativeCost || Number(lastCompletion.data.total_cost_usd ?? session.total_cost_usd ?? 0),
          durationMs: totalDurationMs || Number(session.duration_ms ?? 0),
          tokensIn: totalTokensIn,
          tokensOut: totalTokensOut,
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

      // Start SSE for running or queued sessions (to receive queue_started event)
      if (session.status === 'running' || session.status === 'queued') {
        startSSE(sessionId, lastSequence);
      }

      // Update URL to reflect selected session
      navigate(`/session/${sessionId}/`, { replace: true });
    } catch (err) {
      // Parse error message - API returns JSON like {"detail":"Session not found: xyz"}
      let errorMessage = (err as Error).message;
      try {
        const parsed = JSON.parse(errorMessage);
        if (parsed.detail) {
          errorMessage = parsed.detail;
        }
      } catch {
        // Not JSON, use as-is
      }

      // Show prominent error popup
      setSessionErrorPopup({
        message: errorMessage,
        sessionId,
      });
    }
  };

  /**
   * Handle session error popup close - navigate to home.
   */
  const handleSessionErrorClose = useCallback(() => {
    setSessionErrorPopup(null);
    setCurrentSession(null);
    setEvents(EMPTY_EVENTS);
    setStatus('idle');
    navigate('/', { replace: true });
  }, [navigate]);

  /**
   * Handle session deletion from SessionListTab.
   */
  const handleDeleteSession = useCallback(async (sessionId: string): Promise<void> => {
    if (!config || !token) {
      return;
    }

    try {
      await deleteSession(config.api.base_url, token, sessionId);

      // If deleting current session, clear it
      if (currentSession?.id === sessionId) {
        setCurrentSession(null);
        setEvents([]);
        setStatus('idle');
        navigate('/', { replace: true });
      }

      // Refresh sessions list
      invalidateSessionsCache();
      refreshSessions();

      // Show success notification
      toast.success(`Session ${sessionId} deleted`);
    } catch (err) {
      toast.error(`Failed to delete session: ${(err as Error).message}`);
      throw err; // Re-throw so the modal can handle the error state
    }
  }, [config, token, currentSession, navigate, refreshSessions]);

  const handleNewSession = useCallback((): void => {
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
  }, [navigate]);

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

  // Divider drag handlers for resizable panels
  const handleDividerMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDraggingDivider(true);
  }, []);

  useEffect(() => {
    if (!isDraggingDivider) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!mainRef.current) return;
      const mainRect = mainRef.current.getBoundingClientRect();
      const newWidth = mainRect.right - e.clientX;
      // Clamp between min (250px) and max (70% of main width)
      const minWidth = 250;
      const maxWidth = mainRect.width * 0.7;
      const clampedWidth = Math.max(minWidth, Math.min(maxWidth, newWidth));
      setRightPanelWidth(clampedWidth);
    };

    const handleMouseUp = () => {
      setIsDraggingDivider(false);
      // Persist to localStorage
      localStorage.setItem('rightPanelWidth', String(rightPanelWidth));
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDraggingDivider, rightPanelWidth]);

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

    // Buffer for AskUserQuestion tools - displayed at end of streaming (flushed on agent_complete)
    let bufferedAskUserQuestions: ToolCallView[] = [];

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
          const userItem: ConversationItem = {
            type: 'user',
            id: `user-${items.length}`,
            time: formatTimestamp(event.timestamp),
            content,
          };
          // Add large input metadata if present
          if (event.data.is_large) {
            (userItem as { isLarge: boolean }).isLarge = true;
            if (event.data.size_display) {
              (userItem as { sizeDisplay: string }).sizeDisplay = String(event.data.size_display);
            }
            if (event.data.size_bytes) {
              (userItem as { sizeBytes: number }).sizeBytes = Number(event.data.size_bytes);
            }
            if (event.data.processed_text) {
              (userItem as { processedText: string }).processedText = String(event.data.processed_text);
            }
          }
          items.push(userItem);
          break;
        }
        case 'thinking': {
          const thinkingText = String(event.data.text ?? '');
          const isPartial = Boolean(event.data.is_partial);

          // Find existing thinking tool to update (for streaming)
          let existingThinkingTool: ToolCallView | undefined;
          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            existingThinkingTool = currentStreamMessage.toolCalls.find(
              (t) => t.tool === 'Think' && t.status === 'running'
            );
          }

          if (existingThinkingTool) {
            // Replace with new preview text (backend sends last 300 chars every ~1 second)
            existingThinkingTool.thinking = thinkingText;
            if (!isPartial) {
              // Thinking complete - mark as complete
              existingThinkingTool.status = 'complete';
            }
          } else if (isPartial) {
            // Start new streaming thinking
            const thinkingTool: ToolCallView = {
              id: `think-${toolIdCounter++}`,
              tool: 'Think',
              time: formatTimestamp(event.timestamp),
              status: 'running', // Running while streaming
              thinking: thinkingText,
            };

            // Attach to current or last agent message
            if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
              currentStreamMessage.toolCalls.push(thinkingTool);
            } else if (lastAgentMessage && lastAgentMessage.type === 'agent_message') {
              (lastAgentMessage as { toolCalls: ToolCallView[] }).toolCalls.push(thinkingTool);
            } else {
              pendingTools.push(thinkingTool);
            }
          } else {
            // Non-streaming complete thinking (from ThinkingBlock in AssistantMessage)
            const thinkingTool: ToolCallView = {
              id: `think-${toolIdCounter++}`,
              tool: 'Think',
              time: formatTimestamp(event.timestamp),
              status: 'complete',
              thinking: thinkingText,
            };

            if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
              currentStreamMessage.toolCalls.push(thinkingTool);
            } else if (lastAgentMessage && lastAgentMessage.type === 'agent_message') {
              (lastAgentMessage as { toolCalls: ToolCallView[] }).toolCalls.push(thinkingTool);
            } else {
              pendingTools.push(thinkingTool);
            }
          }
          break;
        }
        case 'tool_start': {
          const toolName = String(event.data.tool_name ?? 'Tool');
          const toolId = String(event.data.tool_id ?? `tool-${toolIdCounter}`);
          // Handle tool_input that may come as string (JSON) or object
          let toolInput: Record<string, unknown> | string | undefined = event.data.tool_input;
          if (typeof toolInput === 'string' && toolInput.trim().startsWith('{')) {
            try {
              toolInput = JSON.parse(toolInput);
            } catch {
              // Keep as string if parse fails
            }
          }
          const newTool: ToolCallView = {
            id: toolId,
            tool: toolName,
            time: formatTimestamp(event.timestamp),
            status: 'running',
            input: toolInput ?? '',
          };
          toolIdCounter++;

          // Buffer AskUserQuestion tools to display at end of streaming
          if (toolName === 'AskUserQuestion' || toolName === 'mcp__ag3ntum__AskUserQuestion') {
            bufferedAskUserQuestions.push(newTool);
          } else {
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
          }

          if (toolInput && fileToolPattern.test(toolName)) {
            extractFilePaths(toolInput).forEach((path) => pendingFiles.add(path));
          }
          break;
        }
        case 'tool_input_ready': {
          // Update tool with complete input (arrives after streaming completes)
          const toolName = String(event.data.tool_name ?? 'Tool');
          const toolId = event.data.tool_id ? String(event.data.tool_id) : undefined;
          const toolInput = event.data.tool_input as Record<string, unknown> | undefined;

          // Check buffered AskUserQuestion tools first
          if (toolName === 'AskUserQuestion' || toolName === 'mcp__ag3ntum__AskUserQuestion') {
            const bufferedTool = bufferedAskUserQuestions.find(t => t.id === toolId);
            if (bufferedTool && toolInput) {
              bufferedTool.input = toolInput;
            }
          } else {
            const tool = findOpenTool(toolName, toolId);
            if (tool && toolInput) {
              tool.input = toolInput;
            }
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

          // Check buffered AskUserQuestion tools first
          let tool: ToolCallView | undefined;
          if (toolName === 'AskUserQuestion' || toolName === 'mcp__ag3ntum__AskUserQuestion') {
            tool = bufferedAskUserQuestions.find(t => t.id === toolId);
          } else {
            tool = findOpenTool(toolName, toolId);
          }

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
            // Strip structured header from streaming content if it's complete
            const streamingBody = parseStructuredMessage(streamBuffer).body;
            if (!currentStreamMessage) {
              const existing = reuseLastAgentMessage();
              currentStreamMessage = existing ?? {
                type: 'agent_message',
                id: `agent-${items.length}`,
                time: formatTimestamp(event.timestamp),
                content: streamingBody,
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
              currentStreamMessage.content = streamingBody;
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
          // Always parse to strip the structured header from the body
          const parsedMessage = parseStructuredMessage(finalText);
          const structuredInfo = eventStructuredFields
            ? {
                body: parsedMessage.body, // Use parsed body (header stripped)
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
            : parsedMessage;
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
            // Parse the stream buffer to strip structured header
            const parsedStream = parseStructuredMessage(streamBuffer.trim());
            currentStreamMessage.content = parsedStream.body;
            if (parsedStream.status) {
              currentStreamMessage.structuredStatus = parsedStream.status;
            }
            if (parsedStream.error) {
              currentStreamMessage.structuredError = parsedStream.error;
            }
            if (Object.keys(parsedStream.fields).length > 0) {
              currentStreamMessage.structuredFields = parsedStream.fields;
            }
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

          // Flush buffered AskUserQuestion tools at end of streaming
          if (bufferedAskUserQuestions.length > 0) {
            if (lastAgentMessage && lastAgentMessage.type === 'agent_message') {
              // Append to existing agent message
              const targetMessage = lastAgentMessage as { toolCalls: ToolCallView[] };
              bufferedAskUserQuestions.forEach(tool => {
                targetMessage.toolCalls.push(tool);
              });
            } else {
              // Create a new message for the buffered tools
              const askMessage: ConversationItem = {
                type: 'agent_message',
                id: `agent-ask-${items.length}`,
                time: formatTimestamp(event.timestamp),
                content: '',
                toolCalls: bufferedAskUserQuestions,
                subagents: [],
                isStreaming: false,
              };
              items.push(askMessage);
            }
            bufferedAskUserQuestions = [];
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
        case 'queue_started': {
          // Task started after being queued - add a system event notification
          const wasAutoResume = Boolean(event.data?.was_auto_resume);
          items.push({
            type: 'system_event',
            id: `system-${items.length}`,
            time: formatTimestamp(event.timestamp),
            eventType: 'queue_started',
            message: wasAutoResume ? 'Auto-resumed task started' : 'Queued task started',
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

    // Note: Buffered AskUserQuestion tools are flushed in the agent_complete case handler.
    // We do NOT flush here during streaming to prevent flickering.
    // For history replay, agent_complete is already in events so the case handler will flush.

    return items;
  }, [events]);

  // Auto-select the latest agent message for the right panel
  // Only update when streaming is complete to reduce blinking and unnecessary re-renders
  useEffect(() => {
    // Find the last agent_message or output with content
    const messagesWithDetails = conversation.filter(
      (item) => item.type === 'agent_message' || item.type === 'output'
    );

    if (messagesWithDetails.length > 0) {
      const lastMessage = messagesWithDetails[messagesWithDetails.length - 1];

      // Check if the last message is still streaming - skip update if so
      const isLastMessageStreaming = lastMessage.type === 'agent_message' && lastMessage.isStreaming;

      // Auto-select the latest message when:
      // 1. Nothing is selected yet
      // 2. The selected message no longer exists
      // 3. Session is running AND streaming is complete (to follow the active message without flickering)
      const selectedExists = conversation.find((item) => item.id === selectedMessageId);
      const shouldAutoSelect = !selectedMessageId || !selectedExists || (status === 'running' && !isLastMessageStreaming);

      // Only update state if the ID actually changes to avoid unnecessary re-renders
      if (shouldAutoSelect && lastMessage.id !== selectedMessageId) {
        setSelectedMessageId(lastMessage.id);
      }
    }
  }, [conversation, selectedMessageId, status]);

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

  // Memoize the selected message for the right panel to avoid re-renders during streaming
  // Only update when streaming is complete or tool calls/subagents change
  const selectedMessageForRightPanel = useMemo(() => {
    if (!selectedMessageId) {
      lastStableRightPanelMessageRef.current = null;
      return null;
    }
    const message = conversation.find((item) => item.id === selectedMessageId);
    if (!message) {
      // Keep the last stable message if the selected one doesn't exist yet
      return lastStableRightPanelMessageRef.current;
    }

    // For agent messages that are streaming, only update when there's meaningful content
    if (message.type === 'agent_message' && message.isStreaming) {
      const hasToolsOrSubagents = message.toolCalls.length > 0 || message.subagents.length > 0;

      // If streaming with no tools/subagents, keep the last stable message
      if (!hasToolsOrSubagents) {
        // If the last stable message was for a different ID, show empty state for new message
        if (lastStableRightPanelMessageRef.current?.id !== selectedMessageId) {
          return null;
        }
        return lastStableRightPanelMessageRef.current;
      }
    }

    // Update the stable reference and return the message
    lastStableRightPanelMessageRef.current = message;
    return message;
  }, [conversation, selectedMessageId]);

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
      } else if (event.type === 'queue_started') {
        const wasAutoResume = Boolean(event.data?.was_auto_resume);
        sysEvents.push({
          id: `sys-${eventCounter++}`,
          time: formatTimestamp(event.timestamp),
          eventType: 'queue_started',
          message: wasAutoResume ? 'Auto-resumed task started' : 'Queued task started',
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
          <span className={`session-status ${session.status}`}>
            {session.status === 'queued' && session.queue_position != null
              ? `queued #${session.queue_position}`
              : session.status}
          </span>
        </div>
        <div className="session-task">{truncateSessionTitle(session.task)}</div>
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

    if (mode === 'view') {
      // Open in FileViewerModal
      try {
        setViewerLoading(true);
        setViewerFile(null);
        setViewerImageUrl(undefined);
        const response = await getFileContent(config.api.base_url, token, currentSession.id, filePath);
        const fileData = toFileViewerData(response);
        setViewerFile(fileData);
        // If it's an image, set the image URL for the viewer
        if (fileData.mimeType.startsWith('image/')) {
          setViewerImageUrl(getFileDownloadUrl(config.api.base_url, token, currentSession.id, filePath));
        }
      } catch (err) {
        setError(`Failed to load file: ${(err as Error).message}`);
      } finally {
        setViewerLoading(false);
      }
    } else {
      // Download mode - download the file with authentication
      try {
        await downloadFile(config.api.base_url, token, currentSession.id, filePath);
      } catch (err) {
        console.error('Download failed:', err);
      }
    }
  };

  const handleCloseViewer = useCallback(() => {
    setViewerFile(null);
    setViewerImageUrl(undefined);
  }, []);

  const handleViewerDownload = useCallback(async () => {
    if (!config || !token || !currentSession || !viewerFile?.path) {
      return;
    }
    try {
      await downloadFile(config.api.base_url, token, currentSession.id, viewerFile.path);
    } catch (err) {
      console.error('Download failed:', err);
    }
  }, [config, token, currentSession, viewerFile?.path]);

  const handleShowInExplorer = useCallback((filePath: string) => {
    // Open file explorer and navigate to the file
    setRightPanelMode('explorer');
    setFileExplorerVisible(true);
    setNavigateToPath(filePath);
  }, []);

  const handleNavigateComplete = useCallback(() => {
    setNavigateToPath(null);
  }, []);

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

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      // ESC key handling - close overlays first, then cancel running session
      if (event.key === 'Escape') {
        // Skip if a modal inside file explorer is open (let modal handle ESC)
        if (fileExplorerModalOpen) {
          return;
        }
        // Close file explorer overlay first if open
        if (fileExplorerVisible) {
          event.preventDefault();
          setFileExplorerVisible(false);
          return;
        }
        // Then cancel running session if active
        if (isRunning) {
          handleCancel();
          return;
        }
      }
      // All Alt+ hotkeys use stopImmediatePropagation() to prevent special characters
      // from being inserted into focused input fields (macOS Option key produces characters)

      // Alt + [: Expand all sections
      if (event.code === 'BracketLeft' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        expandAllSections();
        return;
      }
      // Alt + ]: Collapse all sections
      if (event.code === 'BracketRight' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        collapseAllSections();
        return;
      }
      // Alt + N: New session (macOS Option+N produces ñ)
      if (event.code === 'KeyN' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        handleNewSession();
        return;
      }
      // Alt + E: Switch to File Explorer tab
      if (event.code === 'KeyE' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setRightPanelMode('explorer');
        if (!fileExplorerVisible) {
          setFileExplorerVisible(true);
          setFileExplorerRefreshKey((k) => k + 1);
        }
        return;
      }
      // Alt + D: Switch to Details tab
      if (event.code === 'KeyD' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setRightPanelMode('details');
        return;
      }
    };
    window.addEventListener('keydown', handleKey, true); // capture phase to prevent input from receiving special chars
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [handleCancel, isRunning, expandAllSections, collapseAllSections, handleNewSession, fileExplorerVisible, fileExplorerModalOpen, setRightPanelMode]);

  return (
    <div className="terminal-app">
      <header className="terminal-header">
        <div className="header-top">
          <div className="header-title">
            <span className="header-icon">◆</span>
            <span className="header-label"><a href="/" className="primary-text">AG3NTUM</a></span>
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
            <button className="session-new-button" type="button" onClick={handleNewSession} title="New session (Alt+N)">
              [ +New ]
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
            {/* Expand/Collapse All and File Explorer buttons moved to Details panel */}
          </div>
        </div>
      </header>

      <main ref={mainRef} className={`terminal-main ${isDraggingDivider ? 'resizing' : ''}`}>
        {/* Left column - conversation and input */}
        <div className="terminal-left">
          <div ref={outputRef} className="terminal-output">
            {conversation.length === 0 ? (
              <div className="terminal-empty">Enter a task below to begin.</div>
            ) : (
              <AgentMessageContext.Provider
                value={
                  currentSession && config && token
                    ? {
                        sessionId: currentSession.id,
                        baseUrl: config.api.base_url,
                        token,
                        onShowInExplorer: handleShowInExplorer,
                      }
                    : null
                }
              >
                {conversation.map((item, index) => {
                  const isSelected = selectedMessageId === item.id;
                  if (item.type === 'user') {
                    return (
                      <MessageBlock
                        key={item.id}
                        sender="USER"
                        time={item.time}
                        content={item.content}
                        rightPanelCollapsed={rightPanelCollapsed}
                        isMobile={isMobile}
                        isLarge={item.isLarge}
                        sizeDisplay={item.sizeDisplay}
                        processedText={item.processedText}
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
                      <div
                        key={item.id}
                        className={`message-block-wrapper selectable ${isSelected ? 'selected' : ''}`}
                        onClick={() => {
                          setSelectedMessageId(item.id);
                          setRightPanelMode('details');
                          if (isMobile) setMobilePanelOpen(true);
                        }}
                      >
                        <AgentMessageBlock
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
                          onSubmitAnswer={handleSubmitAnswer}
                        />
                      </div>
                    );
                  }
                  if (item.type === 'output') {
                    return (
                      <div
                        key={item.id}
                        className={`message-block-wrapper selectable ${isSelected ? 'selected' : ''}`}
                        onClick={() => {
                          setSelectedMessageId(item.id);
                          setRightPanelMode('details');
                          if (isMobile) setMobilePanelOpen(true);
                        }}
                      >
                        <OutputBlock
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
                          onShowInExplorer={handleShowInExplorer}
                          rightPanelCollapsed={rightPanelCollapsed}
                          isMobile={isMobile}
                          mobileExpanded={mobileExpandedMessages.has(item.id)}
                          onToggleMobileExpand={() => toggleMobileMessageExpand(item.id)}
                        />
                      </div>
                    );
                  }
                  return null;
                })}
              </AgentMessageContext.Provider>
            )}
          </div>

          {/* Footer/Input area - inside left column */}
          <div className="terminal-footer">
        <div className="usage-bar-row">
          <FooterCopyButtons conversation={conversation} outputRef={outputRef} />
        </div>
        {loadedSkills.length > 0 && (
          <div className="usage-bar-row">
            <span className="usage-bar-label">Skills ({loadedSkills.length}):</span>
            {loadedSkills.map((skill) => (
              <SkillTag
                key={skill.id}
                id={skill.id}
                name={skill.name}
                description={skill.description}
                onClick={() => setInputValue(`/${skill.id} `)}
              />
            ))}
          </div>
        )}
        <div className="usage-bar-row">
          <span className="usage-bar-label">Tools:</span>
          {Object.keys(toolStats).map((tool) => (
            <ToolTag key={tool} type={tool} count={toolStats[tool]} />
          ))}
          {totalSubagentCalls > 0 && (
            <>
              <span className="usage-bar-separator">|</span>
              <span className="usage-bar-label">Agents:</span>
              {Object.keys(subagentStats).map((name) => (
                <SubagentTag key={name} name={name} count={subagentStats[name]} />
              ))}
            </>
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
              onModelChange={handleModelChange}
              availableModels={availableModels}
            />
            <div className={`input-message ${error ? (reconnecting || connectionState === 'polling' ? 'warning' : 'error') : ''}`}>
              {error || '\u00A0'}
            </div>
          </div>
        </div>
        <StatusFooter
          isRunning={isRunning}
          isQueued={status === 'queued'}
          queuePosition={currentSession?.queue_position ?? null}
          isAutoResume={currentSession?.is_auto_resume ?? false}
          statusLabel={statusLabel}
          statusClass={statusClass}
          stats={stats}
          connectionState={!token ? 'disconnected' : connectionState}
          startTime={runningStartTime}
        />
          </div>
        </div>

        {/* Draggable divider */}
        {!isMobile && (
          <div
            className={`terminal-divider ${isDraggingDivider ? 'dragging' : ''}`}
            onMouseDown={handleDividerMouseDown}
          />
        )}

        {/* Right column - details panel or file explorer */}
        <div
          className={`terminal-right ${isMobile && mobilePanelOpen ? 'mobile-open' : ''}`}
          style={!isMobile ? { width: rightPanelWidth, flex: 'none' } : undefined}
        >
          <div className="right-panel-header">
            <button
              type="button"
              className={`right-panel-tab ${rightPanelMode === 'details' ? 'active' : ''}`}
              onClick={() => setRightPanelMode('details')}
              title="Details (Alt+D)"
            >
              Details
            </button>
            <button
              type="button"
              className={`right-panel-tab ${rightPanelMode === 'sessions' ? 'active' : ''}`}
              onClick={() => setRightPanelMode('sessions')}
              title="Sessions (Alt+S)"
            >
              Sessions
              {sessionBadgeCounts.total > 0 && (
                <span className="right-panel-tab-badge">
                  {sessionBadgeCounts.completed > 0 && (
                    <span className="badge-count completed">{sessionBadgeCounts.completed}</span>
                  )}
                  {sessionBadgeCounts.failed > 0 && (
                    <span className="badge-count failed">{sessionBadgeCounts.failed}</span>
                  )}
                  {sessionBadgeCounts.waiting > 0 && (
                    <span className="badge-count waiting">{sessionBadgeCounts.waiting}</span>
                  )}
                </span>
              )}
            </button>
            <button
              type="button"
              className={`right-panel-tab ${rightPanelMode === 'explorer' ? 'active' : ''}`}
              onClick={() => {
                setRightPanelMode('explorer');
                if (!fileExplorerVisible) {
                  setFileExplorerVisible(true);
                  setFileExplorerRefreshKey((k) => k + 1);
                }
              }}
              title="File Explorer (Alt+E)"
            >
              Files
            </button>
            {isMobile && (
              <button
                type="button"
                className="right-panel-tab right-panel-close"
                onClick={() => setMobilePanelOpen(false)}
              >
                ✕
              </button>
            )}
          </div>
          <div className="right-panel-content">
            {rightPanelMode === 'details' ? (
                <RightPanelDetails
                  message={selectedMessageForRightPanel}
                  toolExpanded={expandedTools}
                  onToggleTool={toggleTool}
                  subagentExpanded={expandedSubagents}
                  onToggleSubagent={toggleSubagent}
                  commentsExpanded={selectedMessageId ? expandedComments.has(selectedMessageId) : false}
                  onToggleComments={() => selectedMessageId && toggleComments(selectedMessageId)}
                  filesExpanded={selectedMessageId ? expandedFiles.has(selectedMessageId) : false}
                  onToggleFiles={() => selectedMessageId && toggleFiles(selectedMessageId)}
                  onFileAction={handleFileAction}
                  onShowInExplorer={handleShowInExplorer}
                  onExpandAll={expandAllSections}
                  onCollapseAll={collapseAllSections}
                />
              ) : rightPanelMode === 'sessions' ? (
                <SessionListTab
                  sessions={sessions}
                  currentSessionId={currentSession?.id ?? null}
                  onSelectSession={handleSelectSession}
                  badges={sessionBadges}
                  onClearBadge={clearSessionBadge}
                  onDeleteSession={handleDeleteSession}
                  currentRunStartTime={runningStartTime}
                />
              ) : currentSession && config && token ? (
              <>
                <div className="file-explorer-options">
                  <label className="file-explorer-hidden-toggle">
                    <input
                      type="checkbox"
                      checked={showHiddenFiles}
                      onChange={(e) => setShowHiddenFiles(e.target.checked)}
                    />
                    <span>Show hidden files</span>
                  </label>
                </div>
                <FileExplorer
                  key={fileExplorerRefreshKey}
                  sessionId={currentSession.id}
                  baseUrl={config.api.base_url}
                  token={token}
                  showHiddenFiles={showHiddenFiles}
                  onError={(err) => setError(err)}
                  onModalStateChange={setFileExplorerModalOpen}
                  navigateTo={navigateToPath}
                  onNavigateComplete={handleNavigateComplete}
                  onFileNameInsert={(filename) => {
                    setInputValue((prev) => {
                      const needsSpace = prev.length > 0 && !prev.endsWith(' ');
                      return prev + (needsSpace ? ' ' : '') + filename;
                    });
                  }}
                />
              </>
            ) : (
              <div className="right-panel-empty">
                No session selected
              </div>
            )}
          </div>
        </div>

        {/* File Viewer Modal */}
        {(viewerFile || viewerLoading) && (
          <FileViewerModal
            file={viewerFile}
            isLoading={viewerLoading}
            onClose={handleCloseViewer}
            onDownload={handleViewerDownload}
            imageUrl={viewerImageUrl}
          />
        )}

        {/* Security Alert Toast - Requires manual dismissal */}
        {securityAlert && (
          <div className="security-alert-toast" role="alert" aria-live="assertive">
            <div className="security-alert-header">
              <span className="security-alert-icon">⚠️</span>
              <h3 className="security-alert-title">Sensitive Data Detected</h3>
            </div>
            <p className="security-alert-message">{securityAlert.message}</p>
            <div className="security-alert-details">
              <div className="security-alert-detail-row">
                <span className="security-alert-detail-label">Files affected:</span>
                <span className="security-alert-detail-value">{securityAlert.filesWithSecrets}</span>
              </div>
              <div className="security-alert-detail-row">
                <span className="security-alert-detail-label">Items redacted:</span>
                <span className="security-alert-detail-value">{securityAlert.totalSecrets}</span>
              </div>
              {securityAlert.typeLabels.length > 0 && (
                <div className="security-alert-types">
                  {securityAlert.typeLabels.map((label, idx) => (
                    <span key={idx} className="security-alert-type-badge">{label}</span>
                  ))}
                </div>
              )}
            </div>
            <div className="security-alert-actions">
              <button
                type="button"
                className="security-alert-dismiss"
                onClick={() => setSecurityAlert(null)}
              >
                I Understand
              </button>
            </div>
          </div>
        )}

        {/* Session error popup (e.g., session not found) */}
        <Popup
          isOpen={sessionErrorPopup !== null}
          type="error"
          title="Session Not Found"
          message="The session you're looking for doesn't exist or has been deleted."
          details={sessionErrorPopup?.sessionId}
          onClose={handleSessionErrorClose}
          actions={[
            {
              label: 'Go to Home',
              onClick: handleSessionErrorClose,
              variant: 'primary',
            },
          ]}
        />
      </main>

      {/* Mobile panel backdrop */}
      <div
        className={`mobile-panel-backdrop ${isMobile && mobilePanelOpen ? 'visible' : ''}`}
        onClick={() => setMobilePanelOpen(false)}
      />

      {/* Mobile panel toggle button */}
      <button
        type="button"
        className="mobile-panel-toggle"
        onClick={() => setMobilePanelOpen(true)}
        title="Open details panel"
      >
        ☰
      </button>
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
