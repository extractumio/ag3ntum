/**
 * Right panel details component
 *
 * Shows tool calls, subagents, and files for selected message.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import type { ConversationItem, ToolCallView, SubagentView } from '../../types/conversation';
import { ToolCallBlock } from './ToolCallBlock';
import { SubagentBlock } from './SubagentBlock';
import { ResultSection } from './ResultSection';

export interface RightPanelDetailsProps {
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
}

export function RightPanelDetails({
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
}: RightPanelDetailsProps): JSX.Element {
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
