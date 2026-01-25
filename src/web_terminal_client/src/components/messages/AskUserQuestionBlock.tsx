/**
 * AskUserQuestion block component
 *
 * Interactive component for AskUserQuestion tool calls.
 * When inline=true, renders in message content area without tool panel styling.
 * Human-in-the-loop: The tool may "complete" but still need user input.
 * Extracted from App.tsx for better modularity.
 */

import React, { useState } from 'react';
import type { ToolCallView, AskUserQuestionInput } from '../../types/conversation';
import { formatDuration, blockAltKeyHotkeys } from '../../utils';
import { PulsingCircleSpinner } from '../spinners';

export interface AskUserQuestionBlockProps {
  tool: ToolCallView;
  onSubmitAnswer: (answer: string) => void;
  isLast: boolean;
  inline?: boolean;
  sessionStatus?: string;
}

export function AskUserQuestionBlock({
  tool,
  onSubmitAnswer,
  isLast,
  inline = false,
  sessionStatus,
}: AskUserQuestionBlockProps): JSX.Element {
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
