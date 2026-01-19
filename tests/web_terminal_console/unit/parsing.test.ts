import { describe, expect, it } from 'vitest';

// =============================================================================
// Utility Functions (extracted from App.tsx for testing)
// =============================================================================

type ResultStatus = 'complete' | 'partial' | 'failed' | 'running' | 'cancelled';

type StructuredMessage = {
  body: string;
  fields: Record<string, string>;
  status?: ResultStatus;
  error?: string;
};

function normalizeStatus(value: string): string {
  const statusValue = value.toLowerCase();
  if (statusValue === 'completed' || statusValue === 'complete') {
    return 'complete';
  }
  if (statusValue === 'failed' || statusValue === 'error') {
    return 'failed';
  }
  if (statusValue === 'cancelled' || statusValue === 'canceled') {
    return 'cancelled';
  }
  if (statusValue === 'running') {
    return 'running';
  }
  if (statusValue === 'partial') {
    return 'partial';
  }
  return statusValue || 'idle';
}

function parseHeaderBlock(lines: string[], startIdx: number, endIdx: number): Record<string, string> {
  const fields: Record<string, string> = {};
  lines.slice(startIdx + 1, endIdx).forEach((line) => {
    if (!line.trim()) {
      return;
    }
    const separatorIndex = line.indexOf(':');
    if (separatorIndex === -1) {
      return;
    }
    const key = line.slice(0, separatorIndex).trim().toLowerCase();
    const value = line.slice(separatorIndex + 1).trim();
    if (key) {
      fields[key] = value;
    }
  });
  return fields;
}

function findTrailingHeader(lines: string[]): [number, number] {
  let endIdx = -1;
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (lines[i].trim() === '---') {
      endIdx = i;
      break;
    }
  }
  if (endIdx === -1) {
    return [-1, -1];
  }

  let startIdx = -1;
  for (let i = endIdx - 1; i >= 0; i -= 1) {
    if (lines[i].trim() === '---') {
      startIdx = i;
      break;
    }
  }
  if (startIdx === -1) {
    return [-1, -1];
  }

  let hasField = false;
  for (let i = startIdx + 1; i < endIdx; i += 1) {
    const stripped = lines[i].trim();
    if (stripped && stripped.includes(':')) {
      hasField = true;
      break;
    }
  }
  if (!hasField) {
    return [-1, -1];
  }

  return [startIdx, endIdx];
}

function parseStructuredMessage(text: string): StructuredMessage {
  if (!text) {
    return { body: text, fields: {} };
  }

  let payload = text;
  const isFenced = payload.trim().startsWith('```');
  if (isFenced) {
    const fenceEnd = payload.indexOf('\n');
    if (fenceEnd !== -1) {
      payload = payload.slice(fenceEnd + 1);
    }
  }

  const lines = payload.split('\n');

  // Try to find header at the START of the message
  if (lines.length >= 3 && lines[0]?.trim() === '---') {
    let endIndex = -1;
    for (let i = 1; i < lines.length; i += 1) {
      if (lines[i].trim() === '---') {
        endIndex = i;
        break;
      }
    }
    if (endIndex !== -1) {
      const fields = parseHeaderBlock(lines, 0, endIndex);
      if (Object.keys(fields).length > 0) {
        let bodyStartIndex = endIndex + 1;
        if (isFenced) {
          while (bodyStartIndex < lines.length && lines[bodyStartIndex].trim() === '') {
            bodyStartIndex += 1;
          }
          if (lines[bodyStartIndex]?.trim().startsWith('```')) {
            bodyStartIndex += 1;
          }
        }
        let body = lines.slice(bodyStartIndex).join('\n');
        if (body.startsWith('\n')) {
          body = body.slice(1);
        }
        const statusRaw = fields.status;
        const status = statusRaw ? (normalizeStatus(statusRaw) as ResultStatus) : undefined;
        const error = fields.error ?? undefined;
        return { body, fields, status, error };
      }
    }
  }

  // Try to find header at the END of the message
  const [startIdx, endIdx] = findTrailingHeader(lines);
  if (startIdx !== -1 && endIdx !== -1) {
    const fields = parseHeaderBlock(lines, startIdx, endIdx);
    if (Object.keys(fields).length > 0) {
      let bodyLines = lines.slice(0, startIdx);
      while (bodyLines.length > 0 && !bodyLines[bodyLines.length - 1].trim()) {
        bodyLines.pop();
      }
      const body = bodyLines.join('\n');
      const statusRaw = fields.status;
      const status = statusRaw ? (normalizeStatus(statusRaw) as ResultStatus) : undefined;
      const error = fields.error ?? undefined;
      return { body, fields, status, error };
    }
  }

  return { body: text, fields: {} };
}

function pythonReprToJson(input: string): string {
  const SQ_PLACEHOLDER = '\x00SQ\x00';
  return input
    .replace(/\\'/g, SQ_PLACEHOLDER)
    .replace(/"/g, '\\"')
    .replace(/'/g, '"')
    .replace(new RegExp(SQ_PLACEHOLDER, 'g'), "'")
    .replace(/\bTrue\b/g, 'true')
    .replace(/\bFalse\b/g, 'false')
    .replace(/\bNone\b/g, 'null');
}

type TodoItem = {
  content: string;
  status: string;
  activeForm?: string;
};

type ToolCallView = {
  id: string;
  tool: string;
  input?: unknown;
};

function extractTodos(toolCalls: ToolCallView[]): TodoItem[] | null {
  const todoTool = [...toolCalls].reverse().find((tool) => tool.tool === 'TodoWrite' && tool.input);
  if (!todoTool) {
    return null;
  }

  let input: unknown = todoTool.input;
  if (typeof input === 'string') {
    try {
      input = JSON.parse(input);
    } catch {
      return null;
    }
  }

  if (!input || typeof input !== 'object') {
    return null;
  }

  const rawTodos = (input as { todos?: unknown }).todos;
  if (!Array.isArray(rawTodos)) {
    return null;
  }

  return rawTodos
    .map((todo) => {
      if (!todo || typeof todo !== 'object') {
        return null;
      }
      const item = todo as { content?: unknown; status?: unknown; activeForm?: unknown };
      if (typeof item.content !== 'string' || typeof item.status !== 'string') {
        return null;
      }
      return {
        content: item.content,
        status: item.status,
        activeForm: typeof item.activeForm === 'string' ? item.activeForm : undefined,
      };
    })
    .filter((item): item is TodoItem => Boolean(item));
}

function extractSubagentPreview(rawText: string): string {
  if (!rawText) return '';

  const trimmed = rawText.trim();
  if (trimmed.startsWith('[') && trimmed.includes("'type': 'text'")) {
    const textMatch = trimmed.match(/'text':\s*'([^']*)/);
    if (textMatch && textMatch[1]) {
      return textMatch[1];
    }
  }

  if (trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed) && parsed.length > 0 && parsed[0].type === 'text' && parsed[0].text) {
        return parsed[0].text;
      }
    } catch {
      // Not valid JSON, use as-is
    }
  }

  const firstLine = rawText.split('\n')[0];
  return firstLine;
}

// =============================================================================
// Tests
// =============================================================================

describe('parseHeaderBlock', () => {
  it('parses simple key-value pairs', () => {
    const lines = ['---', 'Status: complete', 'Error: none', '---'];
    const result = parseHeaderBlock(lines, 0, 3);

    expect(result).toEqual({
      status: 'complete',
      error: 'none',
    });
  });

  it('normalizes keys to lowercase', () => {
    const lines = ['---', 'STATUS: value', 'Error: msg', 'CamelCase: test', '---'];
    const result = parseHeaderBlock(lines, 0, 4);

    expect(result).toEqual({
      status: 'value',
      error: 'msg',
      camelcase: 'test',
    });
  });

  it('handles values with colons', () => {
    const lines = ['---', 'url: http://example.com:8080/path', '---'];
    const result = parseHeaderBlock(lines, 0, 2);

    expect(result).toEqual({
      url: 'http://example.com:8080/path',
    });
  });

  it('skips empty lines', () => {
    const lines = ['---', 'key1: value1', '', 'key2: value2', '---'];
    const result = parseHeaderBlock(lines, 0, 4);

    expect(result).toEqual({
      key1: 'value1',
      key2: 'value2',
    });
  });

  it('skips lines without colon', () => {
    const lines = ['---', 'key1: value1', 'no separator here', 'key2: value2', '---'];
    const result = parseHeaderBlock(lines, 0, 4);

    expect(result).toEqual({
      key1: 'value1',
      key2: 'value2',
    });
  });

  it('trims whitespace from keys and values', () => {
    const lines = ['---', '  key  :  value  ', '---'];
    const result = parseHeaderBlock(lines, 0, 2);

    expect(result).toEqual({
      key: 'value',
    });
  });
});

describe('findTrailingHeader', () => {
  it('finds trailing header at end of lines', () => {
    const lines = ['Body content', '---', 'Status: complete', '---'];
    const [start, end] = findTrailingHeader(lines);

    expect(start).toBe(1);
    expect(end).toBe(3);
  });

  it('returns [-1, -1] when no header found', () => {
    const lines = ['No header', 'Just content'];
    const [start, end] = findTrailingHeader(lines);

    expect(start).toBe(-1);
    expect(end).toBe(-1);
  });

  it('returns [-1, -1] when only one delimiter', () => {
    const lines = ['Content', '---', 'More content'];
    const [start, end] = findTrailingHeader(lines);

    expect(start).toBe(-1);
    expect(end).toBe(-1);
  });

  it('returns [-1, -1] when no key-value pairs in header', () => {
    const lines = ['Content', '---', 'No colon here', '---'];
    const [start, end] = findTrailingHeader(lines);

    expect(start).toBe(-1);
    expect(end).toBe(-1);
  });

  it('handles multiple potential headers (finds last)', () => {
    const lines = ['---', 'First: header', '---', 'Body', '---', 'Second: header', '---'];
    const [start, end] = findTrailingHeader(lines);

    expect(start).toBe(4);
    expect(end).toBe(6);
  });
});

describe('parseStructuredMessage', () => {
  describe('header at start', () => {
    it('parses message with header at start', () => {
      const text = `---
Status: complete
Error: none
---
This is the body content.`;

      const result = parseStructuredMessage(text);

      expect(result.status).toBe('complete');
      expect(result.fields.status).toBe('complete');
      expect(result.fields.error).toBe('none');
      expect(result.body).toBe('This is the body content.');
    });

    it('extracts status and error fields', () => {
      const text = `---
Status: failed
Error: Connection timeout
---
Body text`;

      const result = parseStructuredMessage(text);

      expect(result.status).toBe('failed');
      expect(result.error).toBe('Connection timeout');
    });
  });

  describe('header at end', () => {
    it('parses message with header at end', () => {
      const text = `This is the body content.

---
Status: complete
---`;

      const result = parseStructuredMessage(text);

      expect(result.status).toBe('complete');
      expect(result.body).toBe('This is the body content.');
    });
  });

  describe('no header', () => {
    it('returns body unchanged when no header', () => {
      const text = 'Just plain text without any headers.';
      const result = parseStructuredMessage(text);

      expect(result.body).toBe(text);
      expect(result.fields).toEqual({});
      expect(result.status).toBeUndefined();
    });

    it('handles empty string', () => {
      const result = parseStructuredMessage('');

      expect(result.body).toBe('');
      expect(result.fields).toEqual({});
    });
  });

  describe('fenced code blocks', () => {
    it('handles fenced content', () => {
      const text = '```\n---\nStatus: complete\n---\nBody\n```';
      const result = parseStructuredMessage(text);

      // Should parse the header
      expect(result.status).toBe('complete');
    });
  });
});

describe('pythonReprToJson', () => {
  it('converts single quotes to double quotes', () => {
    const input = "{'key': 'value'}";
    const result = pythonReprToJson(input);

    expect(result).toBe('{"key": "value"}');
  });

  it('converts True/False/None to JSON equivalents', () => {
    const input = "{'active': True, 'deleted': False, 'data': None}";
    const result = pythonReprToJson(input);

    expect(result).toBe('{"active": true, "deleted": false, "data": null}');
  });

  it('escapes existing double quotes', () => {
    const input = "{'message': 'He said \"hello\"'}";
    const result = pythonReprToJson(input);

    expect(result).toBe('{"message": "He said \\"hello\\""}');
  });

  it('handles escaped single quotes', () => {
    const input = "{'text': 'It\\'s working'}";
    const result = pythonReprToJson(input);

    expect(result).toBe('{"text": "It\'s working"}');
  });

  it('handles nested structures', () => {
    const input = "{'outer': {'inner': 'value'}}";
    const result = pythonReprToJson(input);

    expect(result).toBe('{"outer": {"inner": "value"}}');
  });

  it('handles arrays', () => {
    const input = "['a', 'b', 'c']";
    const result = pythonReprToJson(input);

    expect(result).toBe('["a", "b", "c"]');
  });

  it('converts valid Python repr to parseable JSON', () => {
    const input = "{'name': 'test', 'count': 42, 'active': True}";
    const result = pythonReprToJson(input);

    expect(() => JSON.parse(result)).not.toThrow();
    expect(JSON.parse(result)).toEqual({
      name: 'test',
      count: 42,
      active: true,
    });
  });
});

describe('extractTodos', () => {
  it('extracts todos from TodoWrite tool call', () => {
    const toolCalls: ToolCallView[] = [
      {
        id: '1',
        tool: 'TodoWrite',
        input: {
          todos: [
            { content: 'Task 1', status: 'completed', activeForm: 'Doing Task 1' },
            { content: 'Task 2', status: 'pending', activeForm: 'Doing Task 2' },
          ],
        },
      },
    ];

    const result = extractTodos(toolCalls);

    expect(result).toHaveLength(2);
    expect(result![0]).toEqual({
      content: 'Task 1',
      status: 'completed',
      activeForm: 'Doing Task 1',
    });
  });

  it('handles JSON string input', () => {
    const toolCalls: ToolCallView[] = [
      {
        id: '1',
        tool: 'TodoWrite',
        input: JSON.stringify({
          todos: [{ content: 'Task 1', status: 'pending' }],
        }),
      },
    ];

    const result = extractTodos(toolCalls);

    expect(result).toHaveLength(1);
    expect(result![0].content).toBe('Task 1');
  });

  it('returns null when no TodoWrite tool', () => {
    const toolCalls: ToolCallView[] = [
      { id: '1', tool: 'Read', input: { file_path: 'test.txt' } },
      { id: '2', tool: 'Bash', input: { command: 'ls' } },
    ];

    const result = extractTodos(toolCalls);
    expect(result).toBeNull();
  });

  it('returns null when TodoWrite has no input', () => {
    const toolCalls: ToolCallView[] = [{ id: '1', tool: 'TodoWrite' }];

    const result = extractTodos(toolCalls);
    expect(result).toBeNull();
  });

  it('returns null when todos is not an array', () => {
    const toolCalls: ToolCallView[] = [
      { id: '1', tool: 'TodoWrite', input: { todos: 'not an array' } },
    ];

    const result = extractTodos(toolCalls);
    expect(result).toBeNull();
  });

  it('uses the last TodoWrite call', () => {
    const toolCalls: ToolCallView[] = [
      {
        id: '1',
        tool: 'TodoWrite',
        input: { todos: [{ content: 'Old Task', status: 'pending' }] },
      },
      {
        id: '2',
        tool: 'TodoWrite',
        input: { todos: [{ content: 'New Task', status: 'completed' }] },
      },
    ];

    const result = extractTodos(toolCalls);

    expect(result).toHaveLength(1);
    expect(result![0].content).toBe('New Task');
  });

  it('filters out invalid todo items', () => {
    const toolCalls: ToolCallView[] = [
      {
        id: '1',
        tool: 'TodoWrite',
        input: {
          todos: [
            { content: 'Valid', status: 'pending' },
            { content: 123, status: 'pending' }, // invalid content type
            { content: 'Missing status' }, // missing status
            null, // null item
          ],
        },
      },
    ];

    const result = extractTodos(toolCalls);

    expect(result).toHaveLength(1);
    expect(result![0].content).toBe('Valid');
  });

  it('handles activeForm being optional', () => {
    const toolCalls: ToolCallView[] = [
      {
        id: '1',
        tool: 'TodoWrite',
        input: {
          todos: [
            { content: 'With form', status: 'pending', activeForm: 'Doing it' },
            { content: 'Without form', status: 'pending' },
          ],
        },
      },
    ];

    const result = extractTodos(toolCalls);

    expect(result).toHaveLength(2);
    expect(result![0].activeForm).toBe('Doing it');
    expect(result![1].activeForm).toBeUndefined();
  });
});

describe('extractSubagentPreview', () => {
  it('returns empty string for empty input', () => {
    expect(extractSubagentPreview('')).toBe('');
  });

  it('extracts text from Python repr format', () => {
    const input = "[{'type': 'text', 'text': 'This is the preview text'}]";
    const result = extractSubagentPreview(input);

    expect(result).toBe('This is the preview text');
  });

  it('extracts text from JSON format', () => {
    const input = '[{"type": "text", "text": "JSON preview text"}]';
    const result = extractSubagentPreview(input);

    expect(result).toBe('JSON preview text');
  });

  it('returns first line for plain text', () => {
    const input = 'First line\nSecond line\nThird line';
    const result = extractSubagentPreview(input);

    expect(result).toBe('First line');
  });

  it('handles invalid JSON gracefully', () => {
    const input = '[invalid json';
    const result = extractSubagentPreview(input);

    expect(result).toBe('[invalid json');
  });
});
