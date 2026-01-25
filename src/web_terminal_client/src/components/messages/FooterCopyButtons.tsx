/**
 * Footer copy buttons component
 *
 * Provides copy functionality for entire conversation content.
 * Extracted from App.tsx for better modularity.
 */

import React, { useState } from 'react';
import type { ConversationItem } from '../../types/conversation';
import { copyAsRichText, copyAsMarkdown } from '../../utils';
import { CopyIconSvg, CheckIconSvg } from '../icons';

/**
 * Generate markdown representation of conversation items
 */
export function generateConversationMarkdown(conversation: ConversationItem[]): string {
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

export interface FooterCopyButtonsProps {
  conversation: ConversationItem[];
  outputRef: React.RefObject<HTMLDivElement | null>;
}

export function FooterCopyButtons({
  conversation,
  outputRef,
}: FooterCopyButtonsProps): JSX.Element {
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
