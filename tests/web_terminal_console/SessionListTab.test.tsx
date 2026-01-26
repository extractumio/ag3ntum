/**
 * Tests for SessionListTab component.
 * 
 * Covers:
 * - Columnar layout rendering
 * - Status indicators
 * - Session selection
 * - Badge display
 * - Delete functionality
 * - 30 session limit
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SessionListTab, SessionBadges, useSessionBadges } from '../../src/web_terminal_client/src/components/SessionListTab';
import type { SessionResponse } from '../../src/web_terminal_client/src/types';
import { renderHook, act } from '@testing-library/react';

// Helper to create mock session
function createMockSession(overrides: Partial<SessionResponse> = {}): SessionResponse {
  const now = new Date();
  const id = 'session_' + String(Date.now()) + '_' + String(Math.floor(Math.random() * 1000));
  return {
    id,
    status: 'complete',
    task: 'Test task',
    model: 'claude-sonnet-4',
    created_at: now.toISOString(),
    updated_at: now.toISOString(),
    completed_at: now.toISOString(),
    num_turns: 5,
    duration_ms: 10000,
    total_cost_usd: 0.05,
    cancel_requested: false,
    resumable: true,
    queue_position: null,
    queued_at: null,
    is_auto_resume: false,
    ...overrides,
  };
}

// Helper to create empty badges
function createEmptyBadges(): SessionBadges {
  return {
    completed: new Set<string>(),
    failed: new Set<string>(),
    waiting: new Set<string>(),
  };
}

describe('SessionListTab', () => {
  const mockOnSelectSession = vi.fn();
  const mockOnClearBadge = vi.fn();
  const mockOnDeleteSession = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Rendering', () => {
    it('renders empty state when no sessions', () => {
      render(
        <SessionListTab
          sessions={[]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      expect(screen.getByText('No sessions yet.')).toBeInTheDocument();
      expect(screen.getByText('Create a new session to get started.')).toBeInTheDocument();
    });

    it('renders column headers', () => {
      const session = createMockSession();
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      expect(screen.getByText('Status')).toBeInTheDocument();
      expect(screen.getByText('Session ID')).toBeInTheDocument();
      expect(screen.getByText('Task')).toBeInTheDocument();
      expect(screen.getByText('Elapsed')).toBeInTheDocument();
      expect(screen.getByText('When')).toBeInTheDocument();
    });

    it('renders session data in columns', () => {
      const session = createMockSession({
        id: 'test_session_123',
        task: 'Run the tests',
        status: 'complete',
      });

      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      expect(screen.getByText('test_session_123')).toBeInTheDocument();
      expect(screen.getByText('Run the tests')).toBeInTheDocument();
    });

    it('truncates long task names', () => {
      const longTask = 'This is a very long task description that should be truncated at some point';
      const session = createMockSession({ task: longTask });

      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      // Should truncate at 35 chars + '...'
      const truncatedTask = longTask.slice(0, 35) + '...';
      expect(screen.getByText(truncatedTask)).toBeInTheDocument();
    });

    it('limits display to 30 sessions', () => {
      const sessions = Array.from({ length: 50 }, (_, i) =>
        createMockSession({
          id: 'session_' + String(i).padStart(3, '0'),
          updated_at: new Date(Date.now() - i * 60000).toISOString(),
        })
      );

      render(
        <SessionListTab
          sessions={sessions}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      // Count session rows by looking for session IDs
      const allSessionIds = sessions.map(s => s.id);
      let visibleCount = 0;
      allSessionIds.forEach(id => {
        if (screen.queryByText(id)) visibleCount++;
      });
      expect(visibleCount).toBeLessThanOrEqual(30);
    });
  });

  describe('Status Indicators', () => {
    it('shows checkmark for complete status', () => {
      const session = createMockSession({ status: 'complete' });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      const statusIcon = document.querySelector('.status-complete');
      expect(statusIcon).toBeInTheDocument();
    });

    it('shows indicator for failed status', () => {
      const session = createMockSession({ status: 'failed' });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      const statusIcon = document.querySelector('.status-failed');
      expect(statusIcon).toBeInTheDocument();
    });

    it('shows pulsing spinner for running status', () => {
      const session = createMockSession({ status: 'running', completed_at: null });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      const pulsingSpinner = document.querySelector('.session-pulsing-spinner');
      expect(pulsingSpinner).toBeInTheDocument();
    });

    it('shows clock for queued status with queue position', () => {
      const session = createMockSession({ 
        status: 'queued', 
        completed_at: null,
        queued_at: new Date().toISOString(),
        queue_position: 3,
      });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      const statusIcon = document.querySelector('.status-queued');
      expect(statusIcon).toBeInTheDocument();
      expect(screen.getByText('#3')).toBeInTheDocument();
    });
  });

  describe('Session Selection', () => {
    it('calls onSelectSession when clicking a session', () => {
      const session = createMockSession({ id: 'clickable_session' });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      const sessionRow = screen.getByText('clickable_session').closest('button');
      if (sessionRow) fireEvent.click(sessionRow);

      expect(mockOnSelectSession).toHaveBeenCalledWith('clickable_session');
    });

    it('clears badge when selecting a session', () => {
      const session = createMockSession({ id: 'badged_session' });
      const badges = createEmptyBadges();
      badges.completed.add('badged_session');

      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={badges}
          onClearBadge={mockOnClearBadge}
        />
      );

      const sessionRow = screen.getByText('badged_session').closest('button');
      if (sessionRow) fireEvent.click(sessionRow);

      expect(mockOnClearBadge).toHaveBeenCalledWith('badged_session');
    });

    it('highlights current session', () => {
      const session = createMockSession({ id: 'current_session' });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId="current_session"
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      const row = document.querySelector('.session-list-row.current');
      expect(row).toBeInTheDocument();
    });
  });

  describe('Badges', () => {
    it('shows badge indicator for completed sessions', () => {
      const session = createMockSession({ id: 'completed_badge_session' });
      const badges = createEmptyBadges();
      badges.completed.add('completed_badge_session');

      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={badges}
          onClearBadge={mockOnClearBadge}
        />
      );

      const badge = document.querySelector('.badge-completed');
      expect(badge).toBeInTheDocument();
    });

    it('shows badge indicator for failed sessions', () => {
      const session = createMockSession({ id: 'failed_badge_session', status: 'failed' });
      const badges = createEmptyBadges();
      badges.failed.add('failed_badge_session');

      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={badges}
          onClearBadge={mockOnClearBadge}
        />
      );

      const badge = document.querySelector('.badge-failed');
      expect(badge).toBeInTheDocument();
    });

    it('does not show badge for current session', () => {
      const session = createMockSession({ id: 'current_with_badge' });
      const badges = createEmptyBadges();
      badges.completed.add('current_with_badge');

      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId="current_with_badge"
          onSelectSession={mockOnSelectSession}
          badges={badges}
          onClearBadge={mockOnClearBadge}
        />
      );

      // Badge should not be rendered for current session
      const row = document.querySelector('.session-list-row.current');
      const badge = row?.querySelector('.session-list-item-badge');
      expect(badge).not.toBeInTheDocument();
    });
  });

  describe('Delete Functionality', () => {
    it('shows delete button for non-running sessions', () => {
      const session = createMockSession({ status: 'complete' });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
          onDeleteSession={mockOnDeleteSession}
        />
      );

      const deleteButton = screen.getByRole('button', { name: /delete session/i });
      expect(deleteButton).toBeInTheDocument();
    });

    it('does not show delete button for running sessions', () => {
      const session = createMockSession({ status: 'running', completed_at: null });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
          onDeleteSession={mockOnDeleteSession}
        />
      );

      const deleteButton = screen.queryByRole('button', { name: /delete session/i });
      expect(deleteButton).not.toBeInTheDocument();
    });

    it('shows confirmation modal when delete is clicked', () => {
      const session = createMockSession({ id: 'delete_me', status: 'complete' });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
          onDeleteSession={mockOnDeleteSession}
        />
      );

      const deleteButton = screen.getByRole('button', { name: /delete session/i });
      fireEvent.click(deleteButton);

      expect(screen.getByText('Delete Session')).toBeInTheDocument();
      // Session ID appears in modal - look for it in the modal specifically
      const modal = document.querySelector('.session-delete-modal');
      expect(modal).toBeInTheDocument();
      expect(modal?.textContent).toContain('delete_me');
    });

    it('calls onDeleteSession when confirmed', async () => {
      mockOnDeleteSession.mockResolvedValue(undefined);
      const session = createMockSession({ id: 'confirm_delete', status: 'complete' });
      
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
          onDeleteSession={mockOnDeleteSession}
        />
      );

      // Open modal
      const deleteButton = screen.getByRole('button', { name: /delete session/i });
      fireEvent.click(deleteButton);

      // Confirm delete - look for the button with exact text "Delete"
      const confirmButton = screen.getByRole('button', { name: 'Delete' });
      fireEvent.click(confirmButton);

      expect(mockOnDeleteSession).toHaveBeenCalledWith('confirm_delete');
    });

    it('closes modal when cancelled', () => {
      const session = createMockSession({ id: 'cancel_delete', status: 'complete' });
      render(
        <SessionListTab
          sessions={[session]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
          onDeleteSession={mockOnDeleteSession}
        />
      );

      // Open modal
      const deleteButton = screen.getByRole('button', { name: /delete session/i });
      fireEvent.click(deleteButton);
      expect(screen.getByText('Delete Session')).toBeInTheDocument();

      // Cancel - use exact match to avoid ambiguity
      const cancelButton = screen.getByRole('button', { name: 'Cancel' });
      fireEvent.click(cancelButton);

      expect(screen.queryByText('Delete Session')).not.toBeInTheDocument();
    });
  });

  describe('Sorting', () => {
    it('sorts sessions by updated_at (most recent first)', () => {
      const oldSession = createMockSession({
        id: 'old_session',
        updated_at: new Date(Date.now() - 3600000).toISOString(),
      });
      const newSession = createMockSession({
        id: 'new_session',
        updated_at: new Date().toISOString(),
      });

      render(
        <SessionListTab
          sessions={[oldSession, newSession]}
          currentSessionId={null}
          onSelectSession={mockOnSelectSession}
          badges={createEmptyBadges()}
          onClearBadge={mockOnClearBadge}
        />
      );

      // Get all session ID elements and check order
      const sessionIds = screen.getAllByTitle(/session/i);
      const ids = sessionIds.map(el => el.textContent);
      
      // new_session should come before old_session
      const newIdx = ids.findIndex(id => id === 'new_session');
      const oldIdx = ids.findIndex(id => id === 'old_session');
      expect(newIdx).toBeLessThan(oldIdx);
    });
  });
});

describe('useSessionBadges Hook', () => {
  it('initializes with empty badges', () => {
    const { result } = renderHook(() => useSessionBadges(null));

    expect(result.current.badges.completed.size).toBe(0);
    expect(result.current.badges.failed.size).toBe(0);
    expect(result.current.badges.waiting.size).toBe(0);
  });

  it('adds badge for non-current session', () => {
    const { result } = renderHook(() => useSessionBadges('current_session'));

    act(() => {
      result.current.addBadge('other_session', 'completed');
    });

    expect(result.current.badges.completed.has('other_session')).toBe(true);
    expect(result.current.badgeCounts.completed).toBe(1);
  });

  it('does not add badge for current session', () => {
    const { result } = renderHook(() => useSessionBadges('current_session'));

    act(() => {
      result.current.addBadge('current_session', 'completed');
    });

    expect(result.current.badges.completed.has('current_session')).toBe(false);
    expect(result.current.badgeCounts.completed).toBe(0);
  });

  it('clears badge for specific session', () => {
    const { result } = renderHook(() => useSessionBadges(null));

    act(() => {
      result.current.addBadge('session_1', 'completed');
      result.current.addBadge('session_1', 'failed');
      result.current.addBadge('session_2', 'waiting');
    });

    act(() => {
      result.current.clearBadge('session_1');
    });

    expect(result.current.badges.completed.has('session_1')).toBe(false);
    expect(result.current.badges.failed.has('session_1')).toBe(false);
    expect(result.current.badges.waiting.has('session_2')).toBe(true);
  });

  it('clears all badges', () => {
    const { result } = renderHook(() => useSessionBadges(null));

    act(() => {
      result.current.addBadge('session_1', 'completed');
      result.current.addBadge('session_2', 'failed');
      result.current.addBadge('session_3', 'waiting');
    });

    act(() => {
      result.current.clearAllBadges();
    });

    expect(result.current.badgeCounts.total).toBe(0);
  });
});
