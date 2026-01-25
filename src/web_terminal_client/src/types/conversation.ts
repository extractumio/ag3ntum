/**
 * Conversation and message type definitions
 *
 * Extracted from App.tsx for better modularity.
 */

export type ResultStatus = 'complete' | 'partial' | 'failed' | 'running' | 'cancelled';

export type ConversationItem =
  | {
      type: 'user';
      id: string;
      time: string;
      content: string;
      isLarge?: boolean;
      sizeDisplay?: string;
      sizeBytes?: number;
      processedText?: string;
    }
  | {
      type: 'agent_message';
      id: string;
      time: string;
      content: string;
      toolCalls: ToolCallView[];
      subagents: SubagentView[];
      status?: ResultStatus;
      comments?: string;
      files?: string[];
      structuredStatus?: ResultStatus;
      structuredError?: string;
      structuredFields?: Record<string, string>;
      isStreaming?: boolean;
    }
  | {
      type: 'output';
      id: string;
      time: string;
      output: string;
      comments?: string;
      files: string[];
      status: ResultStatus;
      error?: string;
    };

export type ToolCallView = {
  id: string;
  tool: string;
  time: string;
  status: 'running' | 'complete' | 'failed';
  durationMs?: number;
  input?: unknown;
  output?: string;
  outputTruncated?: boolean;
  outputLineCount?: number;
  thinking?: string;
  error?: string;
  suggestion?: string;
};

export type AskUserQuestionOption = {
  label: string;
  description?: string;
};

export type AskUserQuestionInput = {
  questions: Array<{
    question: string;
    header?: string;
    options: AskUserQuestionOption[];
    multiSelect?: boolean;
  }>;
};

export type SystemEventView = {
  id: string;
  time: string;
  eventType: 'permission_denied' | 'hook_triggered' | 'profile_switch';
  toolName?: string;
  decision?: string;
  message?: string;
  profileName?: string;
};

export type SubagentView = {
  id: string;
  taskId: string;
  name: string;
  time: string;
  status: 'running' | 'complete' | 'failed';
  durationMs?: number;
  promptPreview?: string;
  resultPreview?: string;
  messageBuffer?: string;
};

export type TodoItem = {
  content: string;
  status: string;
  activeForm?: string;
};

export type StructuredMessage = {
  body: string;
  fields: Record<string, string>;
  status?: ResultStatus;
  error?: string;
};
