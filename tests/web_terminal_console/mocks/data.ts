import type {
  ConfigResponse,
  DirectoryListing,
  FileContentResponse,
  FileInfo,
  ResultResponse,
  SessionListResponse,
  SessionResponse,
  SkillInfo,
  SkillsListResponse,
  SSEEvent,
  TaskStartedResponse,
  TokenResponse,
  User,
} from '../../../src/web_terminal_client/src/types';

// =============================================================================
// Factory Functions for Mock Data
// =============================================================================

export function createMockUser(overrides: Partial<User> = {}): User {
  return {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    role: 'user',
    created_at: '2024-01-15T10:00:00Z',
    ...overrides,
  };
}

export function createMockTokenResponse(overrides: Partial<TokenResponse> = {}): TokenResponse {
  return {
    access_token: 'mock-jwt-token-xyz',
    token_type: 'Bearer',
    user_id: 'user-123',
    expires_in: 3600,
    ...overrides,
  };
}

export function createMockSession(overrides: Partial<SessionResponse> = {}): SessionResponse {
  return {
    id: '20240115_143052_a1b2c3d4',
    status: 'complete',
    task: 'Test task description',
    model: 'claude-3-sonnet',
    created_at: '2024-01-15T14:30:52Z',
    updated_at: '2024-01-15T14:35:00Z',
    completed_at: '2024-01-15T14:35:00Z',
    num_turns: 5,
    duration_ms: 248000,
    total_cost_usd: 0.0125,
    cancel_requested: false,
    resumable: true,
    ...overrides,
  };
}

export function createMockSessionList(count: number = 3): SessionListResponse {
  const sessions: SessionResponse[] = [];
  for (let i = 0; i < count; i++) {
    sessions.push(
      createMockSession({
        id: `2024011${5 + i}_14305${2 + i}_${String(i).padStart(8, 'a')}`,
        task: `Task ${i + 1}`,
        num_turns: i + 1,
      })
    );
  }
  return { sessions, total: count };
}

export function createMockTaskStarted(overrides: Partial<TaskStartedResponse> = {}): TaskStartedResponse {
  return {
    session_id: '20240115_143052_a1b2c3d4',
    status: 'running',
    message: 'Task started successfully',
    resumed_from: null,
    ...overrides,
  };
}

export function createMockResult(overrides: Partial<ResultResponse> = {}): ResultResponse {
  return {
    session_id: '20240115_143052_a1b2c3d4',
    status: 'complete',
    error: '',
    comments: 'Task completed successfully',
    output: 'Task output content',
    result_files: ['output.txt', 'results.json'],
    metrics: {
      duration_ms: 248000,
      num_turns: 5,
      total_cost_usd: 0.0125,
      model: 'claude-3-sonnet',
      usage: {
        input_tokens: 1500,
        output_tokens: 800,
        cache_creation_input_tokens: 0,
        cache_read_input_tokens: 100,
      },
    },
    ...overrides,
  };
}

export function createMockConfig(): ConfigResponse {
  return {
    models_available: ['claude-3-sonnet', 'claude-3-opus', 'claude-3-haiku'],
    default_model: 'claude-3-sonnet',
  };
}

export function createMockSkill(overrides: Partial<SkillInfo> = {}): SkillInfo {
  return {
    id: 'skill-1',
    name: 'Code Review',
    description: 'Reviews code for quality and best practices',
    ...overrides,
  };
}

export function createMockSkillsList(count: number = 3): SkillsListResponse {
  const skills: SkillInfo[] = [];
  for (let i = 0; i < count; i++) {
    skills.push(
      createMockSkill({
        id: `skill-${i + 1}`,
        name: `Skill ${i + 1}`,
        description: `Description for skill ${i + 1}`,
      })
    );
  }
  return { skills };
}

export function createMockFileInfo(overrides: Partial<FileInfo> = {}): FileInfo {
  return {
    name: 'test-file.txt',
    path: 'test-file.txt',
    is_directory: false,
    size: 1024,
    created_at: '2024-01-15T10:00:00Z',
    modified_at: '2024-01-15T12:00:00Z',
    mime_type: 'text/plain',
    is_hidden: false,
    is_viewable: true,
    children: null,
    ...overrides,
  };
}

export function createMockDirectoryListing(overrides: Partial<DirectoryListing> = {}): DirectoryListing {
  return {
    path: '',
    files: [
      createMockFileInfo({ name: 'file1.txt', path: 'file1.txt' }),
      createMockFileInfo({ name: 'file2.js', path: 'file2.js', mime_type: 'application/javascript' }),
      createMockFileInfo({
        name: 'folder',
        path: 'folder',
        is_directory: true,
        mime_type: null,
        is_viewable: false,
      }),
    ],
    total_count: 3,
    truncated: false,
    ...overrides,
  };
}

export function createMockFileContent(overrides: Partial<FileContentResponse> = {}): FileContentResponse {
  return {
    path: 'test-file.txt',
    name: 'test-file.txt',
    mime_type: 'text/plain',
    size: 1024,
    content: 'This is the file content.\nLine 2.\nLine 3.',
    is_binary: false,
    is_truncated: false,
    error: null,
    ...overrides,
  };
}

export function createMockSSEEvent(
  type: SSEEvent['type'],
  data: Record<string, unknown> = {},
  sequence: number = 1
): SSEEvent {
  return {
    type,
    data,
    timestamp: new Date().toISOString(),
    sequence,
  };
}

// =============================================================================
// Predefined Event Sequences for Testing
// =============================================================================

export const MOCK_EVENTS = {
  agentStart: createMockSSEEvent('agent_start', { session_id: '20240115_143052_a1b2c3d4' }, 1),

  userMessage: createMockSSEEvent('user_message', { text: 'Hello, agent!' }, 2),

  toolStart: createMockSSEEvent('tool_start', {
    tool_id: 'tool-1',
    tool_name: 'Read',
    input: { file_path: 'test.txt' },
  }, 3),

  toolComplete: createMockSSEEvent('tool_complete', {
    tool_id: 'tool-1',
    tool_name: 'Read',
    output: 'File contents here',
    duration_ms: 150,
  }, 4),

  thinking: createMockSSEEvent('thinking', { text: 'Analyzing the request...' }, 5),

  message: createMockSSEEvent('message', { text: 'Here is my response.' }, 6),

  agentComplete: createMockSSEEvent('agent_complete', {
    status: 'complete',
    output: 'Task completed',
  }, 7),

  error: createMockSSEEvent('error', { message: 'An error occurred', code: 'E001' }, 8),

  cancelled: createMockSSEEvent('cancelled', { reason: 'User requested cancellation' }, 9),

  heartbeat: createMockSSEEvent('heartbeat', {
    session_status: 'running',
    server_time: new Date().toISOString(),
    redis_ok: true,
    last_sequence: 10,
  }, 10),
};

// Valid session IDs for testing
export const VALID_SESSION_IDS = [
  '20240115_143052_a1b2c3d4',
  '20240116_000000_00000000',
  '20241231_235959_ffffffff',
];

// Invalid session IDs for testing
export const INVALID_SESSION_IDS = [
  '',
  'invalid',
  '20240115_143052_a1b2c3d', // too short
  '20240115_143052_a1b2c3d4e', // too long
  '20240115_143052_g1b2c3d4', // invalid hex
  '../../../etc/passwd',
  '<script>alert("xss")</script>',
  '20240115-143052-a1b2c3d4', // wrong delimiter
];
