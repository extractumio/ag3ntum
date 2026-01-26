/**
 * SessionListTab component - displays all user sessions with status indicators and badges.
 *
 * Features:
 * - Lists all sessions in chronological order (most recent first)
 * - Real-time status updates via SSE
 * - Status indicators (pulsing spinner for running, checkmark, X, clock, etc.)
 * - Badge system for non-current session changes
 * - Delete functionality for non-running sessions
 * - Columnar layout with elapsed time timers
 */
import React, { useCallback, useState, useEffect, useRef } from 'react';
import type { SessionResponse } from '../types';

interface SessionListTabProps {
  sessions: SessionResponse[];
  currentSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  badges: SessionBadges;
  onClearBadge: (sessionId: string) => void;
  onDeleteSession?: (sessionId: string) => Promise<void>;
  /** Start time of the current run (for active sessions) - used for elapsed time calculation */
  currentRunStartTime?: string | null;
}

export interface SessionBadges {
  completed: Set<string>;
  failed: Set<string>;
  waiting: Set<string>;
}

// Icons
const ICONS = {
  checkmark: '\u2713',    // checkmark
  cross: '\u2717',        // X
  halfCircle: '\u25D0',   // half circle
  clock: '\u23F1',        // stopwatch
  questionMark: '?',
  circle: '\u25CB',       // empty circle
  filledCircle: '\u25CF', // filled circle for pulsing spinner
  warning: '\u26A0',      // warning
};

/**
 * Trash icon SVG component.
 */
function TrashIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" className="session-trash-svg">
      <path d="M4 2H1V4H15V2H12V0H4V2Z" fill="currentColor" />
      <path fillRule="evenodd" clipRule="evenodd" d="M3 6H13V16H3V6ZM7 9H9V13H7V9Z" fill="currentColor" />
    </svg>
  );
}

/**
 * Pulsing circle spinner for running status.
 */
function PulsingCircle(): JSX.Element {
  return <span className="session-pulsing-spinner">{ICONS.filledCircle}</span>;
}

/**
 * Get status indicator for a session.
 */
function getStatusIndicator(status: string): { icon: React.ReactNode; className: string } {
  switch (status) {
    case 'complete':
    case 'completed':
      return { icon: ICONS.checkmark, className: 'status-complete' };
    case 'failed':
      return { icon: ICONS.cross, className: 'status-failed' };
    case 'partial':
      return { icon: ICONS.halfCircle, className: 'status-partial' };
    case 'running':
      return { icon: <PulsingCircle />, className: 'status-running' };
    case 'queued':
      return { icon: ICONS.clock, className: 'status-queued' };
    case 'waiting_for_input':
      return { icon: ICONS.questionMark, className: 'status-waiting' };
    case 'cancelled':
      return { icon: ICONS.circle, className: 'status-cancelled' };
    default:
      return { icon: ICONS.circle, className: 'status-pending' };
  }
}

/**
 * Format relative time from a date string.
 */
function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '';

  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  if (diffDay < 7) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}

/**
 * Format elapsed time in human-readable format.
 * Returns: XX sec, NN min XX sec, or HH hours NN min XX sec
 */
function formatElapsedTime(seconds: number): string {
  if (seconds < 0) return '0 sec';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}h ${minutes}m ${secs}s`;
  } else if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  } else {
    return `${secs}s`;
  }
}

/**
 * Calculate elapsed seconds from a start time to now or end time.
 */
function calculateElapsedSeconds(startTime: string, endTime?: string | null): number {
  const start = new Date(startTime).getTime();
  const end = endTime ? new Date(endTime).getTime() : Date.now();
  return Math.max(0, Math.floor((end - start) / 1000));
}

/**
 * ElapsedTime component - displays a live timer that updates every 2 seconds.
 * Shows the duration of the current/last run, NOT total session time.
 * Stops updating when the session is no longer running/queued.
 */
interface ElapsedTimeProps {
  session: SessionResponse;
  /** For the current running session, the time when the run started */
  runStartTime?: string | null;
}

function ElapsedTime({ session, runStartTime }: ElapsedTimeProps): JSX.Element {
  const isActive = session.status === 'running' || session.status === 'queued';

  // Determine the start time based on status:
  // - For queued: use queued_at
  // - For running: use runStartTime if provided, otherwise updated_at (approximation)
  // - For completed/failed: use duration_ms if available, otherwise calculate from updated_at
  const getStartTime = (): string => {
    if (session.status === 'queued' && session.queued_at) {
      return session.queued_at;
    }
    if (session.status === 'running') {
      // For running sessions, prefer the explicit run start time
      // Fall back to updated_at which is set when status changes to running
      return runStartTime || session.updated_at;
    }
    // For completed/failed/cancelled sessions, we want to show the duration of the last run
    // We'll calculate from the point the session status was last set to running (approximated by updated_at)
    // This will be refined by the end time calculation
    return session.updated_at;
  };

  const startTime = getStartTime();

  // Determine end time - use completed_at if available for finished sessions
  const endTime = !isActive ? (session.completed_at || session.updated_at) : null;

  // For completed sessions with duration_ms, use that directly
  const getDurationSeconds = (): number => {
    if (!isActive && session.duration_ms != null && session.duration_ms > 0) {
      return Math.floor(session.duration_ms / 1000);
    }
    return calculateElapsedSeconds(startTime, endTime);
  };

  const [elapsed, setElapsed] = useState(() => getDurationSeconds());
  const intervalRef = useRef<number | null>(null);

  useEffect(() => {
    // Initial calculation
    setElapsed(getDurationSeconds());

    // Only set up interval if session is active
    if (isActive) {
      intervalRef.current = window.setInterval(() => {
        setElapsed(calculateElapsedSeconds(startTime, null));
      }, 2000);
    }

    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [startTime, endTime, isActive, runStartTime, session.duration_ms]);

  // Update elapsed when session status changes (e.g., from running to complete)
  useEffect(() => {
    if (!isActive) {
      setElapsed(getDurationSeconds());
    }
  }, [isActive, startTime, endTime, session.duration_ms]);

  const label = session.status === 'queued' ? 'queued' : 'elapsed';

  return (
    <span className={`session-elapsed-time ${isActive ? 'active' : ''}`} title={`${label}: ${formatElapsedTime(elapsed)}`}>
      {formatElapsedTime(elapsed)}
    </span>
  );
}

/**
 * Delete confirmation modal.
 */
interface DeleteConfirmModalProps {
  sessionId: string;
  onConfirm: () => void;
  onCancel: () => void;
  isDeleting: boolean;
}

function DeleteConfirmModal({
  sessionId,
  onConfirm,
  onCancel,
  isDeleting,
}: DeleteConfirmModalProps): JSX.Element {
  return (
    <div className="session-delete-overlay" onClick={onCancel}>
      <div className="session-delete-modal" onClick={(e) => e.stopPropagation()}>
        <div className="session-delete-header">
          <span>Delete Session</span>
        </div>
        <div className="session-delete-content">
          <p>Are you sure you want to delete this session?</p>
          <p className="session-delete-id">{sessionId}</p>
          <p className="session-delete-warning">
            This will permanently remove the session and all associated files.
          </p>
        </div>
        <div className="session-delete-actions">
          <button
            type="button"
            className="session-delete-cancel-btn"
            onClick={onCancel}
            disabled={isDeleting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="session-delete-confirm-btn"
            onClick={onConfirm}
            disabled={isDeleting}
          >
            {isDeleting ? 'Deleting...' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * SessionListTab shows all sessions with status indicators and selection.
 */
export function SessionListTab({
  sessions,
  currentSessionId,
  onSelectSession,
  badges,
  onClearBadge,
  onDeleteSession,
  currentRunStartTime,
}: SessionListTabProps) {
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Sort sessions by updated_at (most recent first) and limit to 30
  const sortedSessions = [...sessions]
    .sort((a, b) => {
      const dateA = new Date(a.updated_at || a.created_at).getTime();
      const dateB = new Date(b.updated_at || b.created_at).getTime();
      return dateB - dateA;
    })
    .slice(0, 30);

  const handleSelectSession = useCallback(
    (sessionId: string) => {
      onSelectSession(sessionId);
      onClearBadge(sessionId);
    },
    [onSelectSession, onClearBadge]
  );

  const handleDeleteClick = useCallback((e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    setDeleteTarget(sessionId);
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget || !onDeleteSession) return;

    setIsDeleting(true);
    try {
      await onDeleteSession(deleteTarget);
      setDeleteTarget(null);
    } catch (error) {
      console.error('Failed to delete session:', error);
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, onDeleteSession]);

  const handleDeleteCancel = useCallback(() => {
    if (!isDeleting) {
      setDeleteTarget(null);
    }
  }, [isDeleting]);

  // Check if a session can be deleted (not running)
  const canDelete = (status: string): boolean => {
    return status !== 'running';
  };

  if (sessions.length === 0) {
    return (
      <div className="session-list-empty">
        <p>No sessions yet.</p>
        <p>Create a new session to get started.</p>
      </div>
    );
  }

  return (
    <>
      <div className="session-list-tab">
        {/* Column headers */}
        <div className="session-list-header">
          <span className="session-col-status">Status</span>
          <span className="session-col-id">Session ID</span>
          <span className="session-col-task">Task</span>
          <span className="session-col-elapsed">Elapsed</span>
          <span className="session-col-time">When</span>
          <span className="session-col-actions"></span>
        </div>

        {/* Session rows */}
        {sortedSessions.map((session) => {
          const { icon, className } = getStatusIndicator(session.status);
          const isCurrent = session.id === currentSessionId;
          const hasBadge =
            badges.completed.has(session.id) ||
            badges.failed.has(session.id) ||
            badges.waiting.has(session.id);

          // Determine badge type for styling
          let badgeType: string | null = null;
          if (badges.failed.has(session.id)) badgeType = 'failed';
          else if (badges.waiting.has(session.id)) badgeType = 'waiting';
          else if (badges.completed.has(session.id)) badgeType = 'completed';

          const showDeleteButton = onDeleteSession && canDelete(session.status);

          return (
            <div
              key={session.id}
              className={`session-list-row ${isCurrent ? 'current' : ''} ${hasBadge ? 'has-badge' : ''}`}
            >
              <button
                className="session-list-row-main"
                onClick={() => handleSelectSession(session.id)}
                type="button"
              >
                {/* Status column */}
                <span className={`session-col-status session-status-icon ${className}`}>
                  {icon}
                  {badgeType && !isCurrent && (
                    <span className={`session-list-item-badge badge-${badgeType}`} />
                  )}
                </span>

                {/* Session ID column */}
                <span className="session-col-id" title={session.id}>
                  {session.id}
                </span>

                {/* Task column */}
                <span className="session-col-task" title={session.task || 'No task'}>
                  {session.task ? (session.task.length > 35 ? session.task.slice(0, 35) + '...' : session.task) : 'No task'}
                </span>

                {/* Elapsed time column */}
                <span className="session-col-elapsed">
                  <ElapsedTime
                    session={session}
                    runStartTime={isCurrent ? currentRunStartTime : undefined}
                  />
                  {session.status === 'queued' && session.queue_position != null && (
                    <span className="session-queue-pos">#{session.queue_position}</span>
                  )}
                </span>

                {/* Relative time column */}
                <span className="session-col-time">
                  {formatRelativeTime(session.updated_at || session.created_at)}
                </span>
              </button>

              {/* Actions column */}
              <span className="session-col-actions">
                {showDeleteButton && (
                  <button
                    className="session-list-item-delete"
                    onClick={(e) => handleDeleteClick(e, session.id)}
                    type="button"
                    title="Delete session"
                    aria-label="Delete session"
                  >
                    <TrashIcon />
                  </button>
                )}
              </span>
            </div>
          );
        })}
      </div>

      {deleteTarget && (
        <DeleteConfirmModal
          sessionId={deleteTarget}
          onConfirm={handleDeleteConfirm}
          onCancel={handleDeleteCancel}
          isDeleting={isDeleting}
        />
      )}
    </>
  );
}

/**
 * Hook to manage session badges for non-current session updates.
 */
export function useSessionBadges(currentSessionId: string | null) {
  const [badges, setBadges] = useState<SessionBadges>({
    completed: new Set(),
    failed: new Set(),
    waiting: new Set(),
  });

  const addBadge = useCallback(
    (sessionId: string, type: 'completed' | 'failed' | 'waiting') => {
      // Don't add badge for current session
      if (sessionId === currentSessionId) return;

      setBadges((prev) => {
        const newBadges = { ...prev };
        newBadges[type] = new Set(prev[type]);
        newBadges[type].add(sessionId);
        return newBadges;
      });
    },
    [currentSessionId]
  );

  const clearBadge = useCallback((sessionId: string) => {
    setBadges((prev) => {
      const newBadges = {
        completed: new Set(prev.completed),
        failed: new Set(prev.failed),
        waiting: new Set(prev.waiting),
      };
      newBadges.completed.delete(sessionId);
      newBadges.failed.delete(sessionId);
      newBadges.waiting.delete(sessionId);
      return newBadges;
    });
  }, []);

  const clearAllBadges = useCallback(() => {
    setBadges({
      completed: new Set(),
      failed: new Set(),
      waiting: new Set(),
    });
  }, []);

  // Get badge counts for tab display
  const badgeCounts = {
    completed: badges.completed.size,
    failed: badges.failed.size,
    waiting: badges.waiting.size,
    total: badges.completed.size + badges.failed.size + badges.waiting.size,
  };

  return {
    badges,
    addBadge,
    clearBadge,
    clearAllBadges,
    badgeCounts,
  };
}
