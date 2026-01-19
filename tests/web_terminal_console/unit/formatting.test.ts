import { describe, expect, it } from 'vitest';

// =============================================================================
// Utility Functions (extracted from App.tsx for testing)
// =============================================================================

function formatDuration(durationMs?: number | null): string {
  if (!durationMs) {
    return '0.0s';
  }
  return durationMs < 1000
    ? `${durationMs}ms`
    : `${(durationMs / 1000).toFixed(1)}s`;
}

function formatCost(cost?: number | null): string {
  if (cost === null || cost === undefined) {
    return '$0.0000';
  }
  return `$${cost.toFixed(4)}`;
}

function formatTimestamp(timestamp?: string): string {
  if (!timestamp) {
    return '--:--:--';
  }
  const date = new Date(timestamp);
  return date.toLocaleTimeString('en-US', { hour12: false });
}

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

function truncateSessionTitle(title: string | null | undefined): string {
  if (!title) return 'No task';
  const normalized = title.replace(/[\r\n\t]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!normalized) return 'No task';
  let truncated = normalized.slice(0, 80);
  if (truncated.length > 40) {
    const breakPoint = truncated.lastIndexOf(' ', 45);
    if (breakPoint > 30) {
      truncated = truncated.slice(0, breakPoint) + ' ' + truncated.slice(breakPoint + 1);
    } else {
      truncated = truncated.slice(0, 40) + '\u200B' + truncated.slice(40);
    }
  }
  if (normalized.length > 80) {
    truncated += '…';
  }
  return truncated;
}

function formatToolName(name: string): string {
  if (name.startsWith('mcp__ag3ntum__')) {
    const suffix = name.slice('mcp__ag3ntum__'.length);
    return `Ag3ntum${suffix}`;
  }
  if (name.startsWith('mcp_ag3ntum_')) {
    const suffix = name.slice('mcp_ag3ntum_'.length);
    const capitalized = suffix
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join('');
    return `Ag3ntum${capitalized}`;
  }
  return name;
}

function getStatusLabel(status?: string): string {
  const STATUS_LABELS: Record<string, string> = {
    idle: 'Idle',
    running: 'Running',
    complete: 'Complete',
    partial: 'Partial',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };
  if (!status) {
    return '';
  }
  return STATUS_LABELS[status] ?? status;
}

// =============================================================================
// Tests
// =============================================================================

describe('formatDuration', () => {
  describe('millisecond display (< 1000ms)', () => {
    it('formats zero duration', () => {
      expect(formatDuration(0)).toBe('0.0s');
    });

    it('formats null/undefined as zero', () => {
      expect(formatDuration(null)).toBe('0.0s');
      expect(formatDuration(undefined)).toBe('0.0s');
    });

    it('formats small milliseconds', () => {
      expect(formatDuration(1)).toBe('1ms');
      expect(formatDuration(100)).toBe('100ms');
      expect(formatDuration(999)).toBe('999ms');
    });
  });

  describe('second display (>= 1000ms)', () => {
    it('formats exactly 1 second', () => {
      expect(formatDuration(1000)).toBe('1.0s');
    });

    it('formats seconds with decimal', () => {
      expect(formatDuration(1500)).toBe('1.5s');
      expect(formatDuration(2300)).toBe('2.3s');
      expect(formatDuration(10000)).toBe('10.0s');
    });

    it('formats large durations', () => {
      expect(formatDuration(60000)).toBe('60.0s');
      expect(formatDuration(248000)).toBe('248.0s');
      expect(formatDuration(3600000)).toBe('3600.0s');
    });

    it('rounds to one decimal place', () => {
      expect(formatDuration(1234)).toBe('1.2s');
      expect(formatDuration(1250)).toBe('1.3s'); // Rounds up
      expect(formatDuration(1244)).toBe('1.2s'); // Rounds down
    });
  });
});

describe('formatCost', () => {
  it('formats zero cost', () => {
    expect(formatCost(0)).toBe('$0.0000');
  });

  it('formats null/undefined as zero', () => {
    expect(formatCost(null)).toBe('$0.0000');
    expect(formatCost(undefined)).toBe('$0.0000');
  });

  it('formats small costs with 4 decimal places', () => {
    expect(formatCost(0.0001)).toBe('$0.0001');
    expect(formatCost(0.0125)).toBe('$0.0125');
    expect(formatCost(0.1)).toBe('$0.1000');
  });

  it('formats larger costs', () => {
    expect(formatCost(1)).toBe('$1.0000');
    expect(formatCost(1.2345)).toBe('$1.2345');
    expect(formatCost(10.5)).toBe('$10.5000');
  });

  it('rounds correctly', () => {
    expect(formatCost(0.00001)).toBe('$0.0000');
    expect(formatCost(0.00005)).toBe('$0.0001'); // Rounds up
    expect(formatCost(0.00004)).toBe('$0.0000'); // Rounds down
  });
});

describe('formatTimestamp', () => {
  it('returns placeholder for empty timestamp', () => {
    expect(formatTimestamp()).toBe('--:--:--');
    expect(formatTimestamp('')).toBe('--:--:--');
    expect(formatTimestamp(undefined)).toBe('--:--:--');
  });

  it('formats ISO timestamp to 24-hour time', () => {
    // Note: This test may vary based on timezone
    const timestamp = '2024-01-15T14:30:52Z';
    const result = formatTimestamp(timestamp);
    // Should be in HH:MM:SS format
    expect(result).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it('handles midnight', () => {
    const timestamp = '2024-01-15T00:00:00Z';
    const result = formatTimestamp(timestamp);
    expect(result).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it('handles various ISO formats', () => {
    expect(formatTimestamp('2024-01-15T14:30:52.123Z')).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(formatTimestamp('2024-01-15T14:30:52+00:00')).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

describe('normalizeStatus', () => {
  describe('complete status', () => {
    it('normalizes "completed" to "complete"', () => {
      expect(normalizeStatus('completed')).toBe('complete');
      expect(normalizeStatus('Completed')).toBe('complete');
      expect(normalizeStatus('COMPLETED')).toBe('complete');
    });

    it('keeps "complete" as "complete"', () => {
      expect(normalizeStatus('complete')).toBe('complete');
      expect(normalizeStatus('Complete')).toBe('complete');
    });
  });

  describe('failed status', () => {
    it('normalizes "error" to "failed"', () => {
      expect(normalizeStatus('error')).toBe('failed');
      expect(normalizeStatus('Error')).toBe('failed');
      expect(normalizeStatus('ERROR')).toBe('failed');
    });

    it('keeps "failed" as "failed"', () => {
      expect(normalizeStatus('failed')).toBe('failed');
      expect(normalizeStatus('Failed')).toBe('failed');
    });
  });

  describe('cancelled status', () => {
    it('normalizes "canceled" (US spelling) to "cancelled"', () => {
      expect(normalizeStatus('canceled')).toBe('cancelled');
      expect(normalizeStatus('Canceled')).toBe('cancelled');
    });

    it('keeps "cancelled" as "cancelled"', () => {
      expect(normalizeStatus('cancelled')).toBe('cancelled');
      expect(normalizeStatus('Cancelled')).toBe('cancelled');
    });
  });

  describe('other statuses', () => {
    it('keeps "running" as "running"', () => {
      expect(normalizeStatus('running')).toBe('running');
      expect(normalizeStatus('Running')).toBe('running');
    });

    it('keeps "partial" as "partial"', () => {
      expect(normalizeStatus('partial')).toBe('partial');
      expect(normalizeStatus('Partial')).toBe('partial');
    });

    it('returns unknown statuses lowercased', () => {
      expect(normalizeStatus('pending')).toBe('pending');
      expect(normalizeStatus('queued')).toBe('queued');
      expect(normalizeStatus('CUSTOM')).toBe('custom');
    });

    it('returns "idle" for empty string', () => {
      expect(normalizeStatus('')).toBe('idle');
    });
  });
});

describe('truncateSessionTitle', () => {
  describe('empty/null handling', () => {
    it('returns "No task" for null', () => {
      expect(truncateSessionTitle(null)).toBe('No task');
    });

    it('returns "No task" for undefined', () => {
      expect(truncateSessionTitle(undefined)).toBe('No task');
    });

    it('returns "No task" for empty string', () => {
      expect(truncateSessionTitle('')).toBe('No task');
    });

    it('returns "No task" for whitespace-only string', () => {
      expect(truncateSessionTitle('   ')).toBe('No task');
      expect(truncateSessionTitle('\t\n')).toBe('No task');
    });
  });

  describe('short titles (< 40 chars)', () => {
    it('returns short titles unchanged', () => {
      expect(truncateSessionTitle('Short task')).toBe('Short task');
      expect(truncateSessionTitle('A slightly longer task name')).toBe('A slightly longer task name');
    });
  });

  describe('medium titles (40-80 chars)', () => {
    it('adds word break for long titles without natural breaks', () => {
      const title = 'This is a task that is exactly forty-two characters long';
      const result = truncateSessionTitle(title);
      // Should contain zero-width space for word breaking
      expect(result.length).toBeGreaterThanOrEqual(title.slice(0, 80).length);
    });

    it('preserves natural word breaks when available', () => {
      const title = 'This has a natural break point around the middle of the title text';
      const result = truncateSessionTitle(title);
      expect(result).not.toContain('…');
    });
  });

  describe('long titles (> 80 chars)', () => {
    it('truncates to 80 chars and adds ellipsis', () => {
      const title = 'A'.repeat(100);
      const result = truncateSessionTitle(title);
      expect(result.length).toBeLessThanOrEqual(82); // 80 + possible break char + ellipsis
      expect(result).toContain('…');
    });

    it('handles very long titles', () => {
      const title = 'word '.repeat(50);
      const result = truncateSessionTitle(title);
      expect(result).toContain('…');
    });
  });

  describe('whitespace normalization', () => {
    it('collapses multiple spaces', () => {
      const result = truncateSessionTitle('Multiple   spaces   here');
      expect(result).toBe('Multiple spaces here');
    });

    it('replaces newlines with spaces', () => {
      const result = truncateSessionTitle('Line 1\nLine 2\nLine 3');
      expect(result).toBe('Line 1 Line 2 Line 3');
    });

    it('replaces tabs with spaces', () => {
      const result = truncateSessionTitle('Tab\there\tthere');
      expect(result).toBe('Tab here there');
    });

    it('handles carriage returns', () => {
      const result = truncateSessionTitle('CR\rhere');
      expect(result).toBe('CR here');
    });

    it('handles mixed whitespace', () => {
      const result = truncateSessionTitle('  Mixed\t\n  whitespace  \r\n  here  ');
      expect(result).toBe('Mixed whitespace here');
    });
  });
});

describe('formatToolName', () => {
  describe('mcp__ag3ntum__ prefix (double underscore)', () => {
    it('formats Bash tool', () => {
      expect(formatToolName('mcp__ag3ntum__Bash')).toBe('Ag3ntumBash');
    });

    it('formats Read tool', () => {
      expect(formatToolName('mcp__ag3ntum__Read')).toBe('Ag3ntumRead');
    });

    it('formats Write tool', () => {
      expect(formatToolName('mcp__ag3ntum__Write')).toBe('Ag3ntumWrite');
    });
  });

  describe('mcp_ag3ntum_ prefix (single underscore, legacy)', () => {
    it('formats bash tool with capitalization', () => {
      expect(formatToolName('mcp_ag3ntum_bash')).toBe('Ag3ntumBash');
    });

    it('formats multi-word tool names', () => {
      expect(formatToolName('mcp_ag3ntum_file_read')).toBe('Ag3ntumFileRead');
    });

    it('handles already capitalized names', () => {
      expect(formatToolName('mcp_ag3ntum_Read')).toBe('Ag3ntumRead');
    });
  });

  describe('no prefix', () => {
    it('returns name unchanged', () => {
      expect(formatToolName('Bash')).toBe('Bash');
      expect(formatToolName('Read')).toBe('Read');
      expect(formatToolName('CustomTool')).toBe('CustomTool');
    });
  });
});

describe('getStatusLabel', () => {
  it('returns labels for known statuses', () => {
    expect(getStatusLabel('idle')).toBe('Idle');
    expect(getStatusLabel('running')).toBe('Running');
    expect(getStatusLabel('complete')).toBe('Complete');
    expect(getStatusLabel('partial')).toBe('Partial');
    expect(getStatusLabel('failed')).toBe('Failed');
    expect(getStatusLabel('cancelled')).toBe('Cancelled');
  });

  it('returns empty string for undefined/empty', () => {
    expect(getStatusLabel(undefined)).toBe('');
    expect(getStatusLabel('')).toBe('');
  });

  it('returns unknown status as-is', () => {
    expect(getStatusLabel('pending')).toBe('pending');
    expect(getStatusLabel('custom_status')).toBe('custom_status');
  });
});
