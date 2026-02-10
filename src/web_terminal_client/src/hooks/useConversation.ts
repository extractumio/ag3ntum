/**
 * useConversation hook
 *
 * Transforms raw SSE events into structured ConversationItem[] for rendering.
 * Extracted from App.tsx to reduce the component's complexity.
 */

import { useMemo } from 'react';
import type { TerminalEvent } from '../types';
import type {
  ConversationItem,
  SubagentView,
  ToolCallView,
} from '../types/conversation';
import {
  extractFilePaths,
  formatTimestamp,
  normalizeStatus,
  parseStructuredMessage,
} from '../utils';

/**
 * Builds a ConversationItem[] from raw SSE events.
 *
 * The logic handles:
 * - Streaming partial messages
 * - Tool call lifecycle (start/input_ready/complete)
 * - Subagent lifecycle (start/message/stop)
 * - Thinking blocks (streaming and complete)
 * - AskUserQuestion buffering (displayed at end of streaming)
 * - System events (queue_started)
 * - Agent completion with status propagation
 */
export function useConversation(events: TerminalEvent[]): ConversationItem[] {
  return useMemo<ConversationItem[]>(() => {
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

          if (!fullText && !text && !streamBuffer) {
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

          // Parse dual status fields from event
          const messageStatus = typeof event.data.message_status === 'string'
            ? (normalizeStatus(event.data.message_status) as import('../types/conversation').ResultStatus)
            : undefined;
          const messageErrorMessage = typeof event.data.message_error_message === 'string'
            ? event.data.message_error_message
            : undefined;
          const requestStatus = typeof event.data.request_status === 'string'
            ? (normalizeStatus(event.data.request_status) as import('../types/conversation').ResultStatus)
            : undefined;
          const requestErrorMessage = typeof event.data.request_error_message === 'string'
            ? event.data.request_error_message
            : undefined;

          const bodyText = parsedMessage.body;

          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            currentStreamMessage.content = bodyText;
            currentStreamMessage.messageStatus = messageStatus;
            currentStreamMessage.messageErrorMessage = messageErrorMessage;
            currentStreamMessage.requestStatus = requestStatus;
            currentStreamMessage.requestErrorMessage = requestErrorMessage;
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
              messageStatus,
              messageErrorMessage,
              requestStatus,
              requestErrorMessage,
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
          const statusValue = normalizeStatus(String(event.data.status ?? 'complete')) as import('../types/conversation').ResultStatus;

          if (currentStreamMessage && currentStreamMessage.type === 'agent_message') {
            // Parse the stream buffer to strip structured header
            const parsedStream = parseStructuredMessage(streamBuffer.trim());
            currentStreamMessage.content = parsedStream.body;
            if (parsedStream.status) {
              currentStreamMessage.requestStatus = parsedStream.status;
            }
            if (parsedStream.error) {
              currentStreamMessage.requestErrorMessage = parsedStream.error;
            }
            // Set messageStatus if not already set (agent completed successfully = complete)
            if (!currentStreamMessage.messageStatus) {
              currentStreamMessage.messageStatus = statusValue;
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
              messageStatus: statusValue,
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
            lastAgentMessage.status = lastAgentMessage.requestStatus ?? statusValue;
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
          if (subagent && text) {
            subagent.messageBuffer = (subagent.messageBuffer ?? '') + text;
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
}
