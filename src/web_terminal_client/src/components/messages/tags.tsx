/**
 * Tag components for tools, subagents, and skills
 *
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import { TOOL_COLOR_CLASS, TOOL_SYMBOL } from '../../constants';
import { formatToolName } from '../../utils';

export interface ToolTagProps {
  type: string;
  count?: number;
  showSymbol?: boolean;
}

export function ToolTag({ type, count, showSymbol = true }: ToolTagProps): JSX.Element {
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

export interface SubagentTagProps {
  name: string;
  count?: number;
}

export function SubagentTag({ name, count }: SubagentTagProps): JSX.Element {
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

export interface SkillTagProps {
  id: string;
  name: string;
  description: string;
  onClick: () => void;
}

export function SkillTag({
  id,
  name,
  description,
  onClick
}: SkillTagProps): JSX.Element {
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
