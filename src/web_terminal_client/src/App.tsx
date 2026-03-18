import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  cancelSession,
  continueTask,
  deleteSession,
  downloadFile,
  getConfig,
  getFileContent,
  getFileDownloadUrl,
  getSession,
  getSessionEvents,
  getSkillsCached,
  invalidateSessionsCache,
  listSessionsCached,
  runTask,
  uploadFiles,
  type DynamicMountRequest,
} from './api';
import { getBoolean, setBoolean, getString, setString, getNumber, setNumber } from './storage';
import { useAuth } from './AuthContext';
import { loadConfig } from './config';
import {
  EMPTY_EVENTS,
  STATUS_CLASS,
  STATUS_LABELS,
} from './constants';
import { FileExplorer } from './FileExplorer';
import {
  AgentMessageContext,
  FileViewerModal,
  toFileViewerData,
  type FileViewerData,
} from './FileViewer';
import { ProtectedRoute } from './ProtectedRoute';
import { connectSSE, connectUserEventsSSE, type UserEvent } from './sse';
import type { AppConfig, SessionListResponse, SessionResponse, SkillInfo, TerminalEvent } from './types';
import type {
  ConversationItem,
  ResultStatus,
  SystemEventView,
  TodoItem,
} from './types/conversation';
import {
  Popup,
  SessionListTab,
  useSessionBadges,
  useToast,
} from './components';
import {
  AgentMessageBlock,
  FooterCopyButtons,
  MessageBlock,
  OutputBlock,
  RightPanelDetails,
  SkillTag,
  SubagentTag,
  SystemEventsPanel,
  SystemEventsToggle,
  ToolTag,
} from './components/messages';
import { InputField, StatusFooter, type AttachedFile } from './components/input';
import { useIsMobile, useConversation } from './hooks';
import {
  extractTodos,
  formatDuration,
  formatTimestamp,
  getLastServerSequence,
  isSafeRelativePath,
  isValidSessionId,
  normalizeStatus,
  seedSessionEvents,
  truncateSessionTitle,
} from './utils';

// Terminal statuses that should not be overwritten by stale server data or events.
// This set is used throughout session state management to prevent race conditions
// where the server hasn't persisted a terminal status yet but the frontend already knows.
const TERMINAL_STATUSES = new Set(['complete', 'completed', 'partial', 'failed', 'cancelled', 'canceled']);

interface AppProps {
  initialSessionId?: string;
}

function App({ initialSessionId }: AppProps): JSX.Element {
  const { token, user, logout } = useAuth();
  const navigate = useNavigate();
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionResponse | null>(null);
  const [events, setEvents] = useState<TerminalEvent[]>(EMPTY_EVENTS);
  const [inputValue, setInputValue] = useState('');
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState<string | null>(null);
  // Session error popup state (for prominent error display with navigation)
  const [sessionErrorPopup, setSessionErrorPopup] = useState<{
    message: string;
    sessionId: string;
  } | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [connectionState, setConnectionState] = useState<'connected' | 'reconnecting' | 'polling' | 'degraded'>('connected');
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [expandedSubagents, setExpandedSubagents] = useState<Set<string>>(new Set());
  const [expandedComments, setExpandedComments] = useState<Set<string>>(new Set());
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [dynamicMounts, setDynamicMounts] = useState<DynamicMountRequest[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState<boolean>(() => getBoolean('ag3ntum_right_panel_collapsed'));
  const [mobileExpandedMessages, setMobileExpandedMessages] = useState<Set<string>>(new Set());
  const [systemEventsExpanded, setSystemEventsExpanded] = useState(false);
  const [fileExplorerVisible, setFileExplorerVisible] = useState(false);
  // New layout state
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [rightPanelMode, setRightPanelMode] = useState<'details' | 'explorer' | 'sessions'>('details');
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false);
  // Session badges for non-current session status changes
  const { badges: sessionBadges, addBadge: addSessionBadge, clearBadge: clearSessionBadge, badgeCounts: sessionBadgeCounts } = useSessionBadges(currentSession?.id ?? null);
  // Resizable panel state
  const [rightPanelWidth, setRightPanelWidth] = useState<number>(() => getNumber('ag3ntum_right_panel_width'));
  const [isDraggingDivider, setIsDraggingDivider] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const [fileExplorerRefreshKey, setFileExplorerRefreshKey] = useState(0);
  const [fileExplorerRefreshTrigger, setFileExplorerRefreshTrigger] = useState(0);
  const [fileExplorerModalOpen, setFileExplorerModalOpen] = useState(false);
  const [showHiddenFiles, setShowHiddenFiles] = useState(false);
  const [navigateToPath, setNavigateToPath] = useState<string | null>(null);
  const [stats, setStats] = useState({
    turns: 0,
    cost: 0,
    durationMs: 0,
    tokensIn: 0,
    tokensOut: 0,
    model: '',
  });
  const [runningStartTime, setRunningStartTime] = useState<string | null>(null);
  const [loadedSkills, setLoadedSkills] = useState<SkillInfo[]>([]);
  // File viewer modal state
  const [viewerFile, setViewerFile] = useState<FileViewerData | null>(null);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerImageUrl, setViewerImageUrl] = useState<string | undefined>(undefined);

  // Security alert state (for sensitive data detection notifications)
  const [securityAlert, setSecurityAlert] = useState<{
    message: string;
    typeLabels: string[];
    filesWithSecrets: number;
    totalSecrets: number;
  } | null>(null);

  // Toast notifications
  const toast = useToast();

  const isMobile = useIsMobile();

  const outputRef = useRef<HTMLDivElement | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const activeTurnRef = useRef(0);
  const lastStableRightPanelMessageRef = useRef<ConversationItem | null>(null);
  const insertTextFnRef = useRef<((text: string) => void) | null>(null);

  const isRunning = status === 'running';
  const statusLabel = STATUS_LABELS[status] ?? STATUS_LABELS.idle;
  const statusClass = STATUS_CLASS[status] ?? STATUS_CLASS.idle;

  useEffect(() => {
    loadConfig().then(setConfig).catch(() => setConfig(null));
  }, []);

  // Load available models from API config
  useEffect(() => {
    if (!config) {
      return;
    }
    getConfig(config.api.base_url)
      .then((apiConfig) => {
        setAvailableModels(apiConfig.models_available);
        // Check for stored model preference
        const storedModel = getString('ag3ntum_selected_model');
        if (storedModel && apiConfig.models_available.includes(storedModel)) {
          // Use stored model if it's still available
          setSelectedModel(storedModel);
        } else {
          // Fall back to default model
          setSelectedModel(apiConfig.default_model);
        }
      })
      .catch((err) => {
        console.error('Failed to load API config:', err);
      });
  }, [config]);

  // Load available skills
  useEffect(() => {
    if (!config || !token) {
      return;
    }
    getSkillsCached(config.api.base_url, token)
      .then((response: { skills: SkillInfo[] }) => {
        setLoadedSkills(response.skills);
      })
      .catch((err: Error) => {
        console.error('Failed to load skills:', err);
      });
  }, [config, token]);

  const refreshSessions = useCallback(() => {
    if (!config || !token) {
      return;
    }

    listSessionsCached(config.api.base_url, token)
      .then((response: SessionListResponse) => {
        // Merge server sessions with local state using defensive merging:
        // 1. Terminal statuses are "sticky" - can't be reverted to non-terminal
        // 2. Newer local timestamps take precedence over stale server data
        setSessions((prevSessions) => {
          const localSessionMap = new Map<string, SessionResponse>();
          for (const session of prevSessions) {
            localSessionMap.set(session.id, session);
          }

          return response.sessions.map((serverSession) => {
            const localSession = localSessionMap.get(serverSession.id);
            if (!localSession) {
              // New session from server, use server data
              return serverSession;
            }

            // RULE 1: If local is terminal and server is non-terminal, keep local
            // (Server hasn't caught up to the terminal event yet)
            if (TERMINAL_STATUSES.has(localSession.status) && !TERMINAL_STATUSES.has(serverSession.status)) {
              return localSession;
            }

            // RULE 2: If local has newer timestamp and is terminal, keep local
            // (Local state was updated more recently and reached a final state)
            const localTime = new Date(localSession.updated_at).getTime();
            const serverTime = new Date(serverSession.updated_at).getTime();
            if (localTime > serverTime && TERMINAL_STATUSES.has(localSession.status)) {
              return localSession;
            }

            // Otherwise, use server data (it's fresher or both are terminal)
            return serverSession;
          });
        });
      })
      .catch((err: Error) => setError(`Failed to load sessions: ${err.message}`));
  }, [config, token]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  // Sync sessions[] from currentSession when currentSession has terminal status but sessions[] doesn't
  // This is a ONE-WAY sync: currentSession → sessions[], never the reverse.
  //
  // Why one-way only?
  // - When user clicks "Continue" on a completed session, handleSubmit sets currentSession to 'running'
  // - If we synced FROM sessions[] TO currentSession, we'd overwrite the intentional 'running' back to 'completed'
  // - Terminal events always come through Session SSE which updates BOTH currentSession and sessions[]
  // - So there's no legitimate case where sessions[] has newer terminal info than currentSession
  useEffect(() => {
    if (!currentSession) return;

    const matchingSession = sessions.find((s) => s.id === currentSession.id);
    if (!matchingSession) return;

    // Only sync if currentSession is terminal and sessions[] is not
    // This handles: User Events SSE sent stale 'running' status for current session
    if (
      matchingSession.status !== currentSession.status &&
      TERMINAL_STATUSES.has(currentSession.status) &&
      !TERMINAL_STATUSES.has(matchingSession.status)
    ) {
      setSessions((prev) =>
        prev.map((s) => (s.id === currentSession.id ? { ...s, status: currentSession.status } : s))
      );
    }
  }, [currentSession, sessions]);

  // Load session from URL on mount
  useEffect(() => {
    if (isValidSessionId(initialSessionId) && config && token && !currentSession) {
      handleSelectSession(initialSessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId, config, token]);

  useEffect(() => {
    return () => {
      if (cleanupRef.current) {
        cleanupRef.current();
      }
    };
  }, []);

  // User-level SSE subscription for cross-session updates (badges, status changes)
  const userEventsCleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!config || !token) {
      return;
    }

    // Clean up any existing subscription
    if (userEventsCleanupRef.current) {
      userEventsCleanupRef.current();
    }

    const cleanup = connectUserEventsSSE({
      baseUrl: config.api.base_url,
      token,
      onEvent: (event: UserEvent) => {
        // Handle specific session status change events
        if (event.type === 'session_status_change') {
          const change = event.data as {
            id: string;
            old_status: string;
            new_status: string;
            queue_position?: number;
          } | undefined;

          if (change) {
            // Update the session in our list, with terminal status protection
            setSessions((prevSessions) =>
              prevSessions.map((session) => {
                if (session.id !== change.id) return session;

                // CRITICAL: Don't overwrite terminal status with non-terminal status
                // This prevents race conditions where server sends stale status updates
                if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(change.new_status)) {
                  return session;
                }

                return { ...session, status: change.new_status, queue_position: change.queue_position ?? null };
              })
            );

            // Add badge for status changes on non-current sessions
            if (change.id !== currentSession?.id) {
              if (change.new_status === 'complete' || change.new_status === 'completed') {
                addSessionBadge(change.id, 'completed');
              } else if (change.new_status === 'failed') {
                addSessionBadge(change.id, 'failed');
              } else if (change.new_status === 'waiting_for_input') {
                addSessionBadge(change.id, 'waiting');
              }
            }
          }
        }

        // Handle bulk session list updates
        if (event.type === 'session_list_update') {
          // Update sessions list with new data
          const sessionList = event.data?.sessions as Array<{
            id: string;
            status: string;
            queue_position?: number;
            is_auto_resume?: boolean;
          }> | undefined;

          if (sessionList) {
            // Update session statuses in our local list, with terminal status protection
            setSessions((prevSessions) =>
              prevSessions.map((session) => {
                const updated = sessionList.find((s) => s.id === session.id);
                if (!updated) return session;

                // CRITICAL: Don't overwrite terminal status with non-terminal status
                // This prevents race conditions where server sends stale status updates
                if (TERMINAL_STATUSES.has(session.status) && !TERMINAL_STATUSES.has(updated.status)) {
                  return session;
                }

                // Check if status changed for badge purposes
                if (session.status !== updated.status && session.id !== currentSession?.id) {
                  // Add badge based on new status
                  if (updated.status === 'complete' || updated.status === 'completed') {
                    addSessionBadge(session.id, 'completed');
                  } else if (updated.status === 'failed') {
                    addSessionBadge(session.id, 'failed');
                  } else if (updated.status === 'waiting_for_input') {
                    addSessionBadge(session.id, 'waiting');
                  }
                }
                return {
                  ...session,
                  status: updated.status,
                  queue_position: updated.queue_position ?? null,
                  is_auto_resume: updated.is_auto_resume ?? false,
                };
              })
            );
          }
        }
      },
      onError: (error: Error) => {
        console.warn('[UserEventsSSE] Error:', error.message);
      },
    });

    userEventsCleanupRef.current = cleanup;

    return () => {
      if (userEventsCleanupRef.current) {
        userEventsCleanupRef.current();
        userEventsCleanupRef.current = null;
      }
    };
  }, [config, token, currentSession?.id, addSessionBadge]);

  const appendEvent = useCallback(
    (event: TerminalEvent) => {
      setEvents((prev) => {
        if (event.type === 'user_message') {
          const last = prev[prev.length - 1];
          const lastText = (last?.data as { text?: unknown } | undefined)?.text;
          const nextText = (event.data as { text?: unknown } | undefined)?.text;
          if (last?.type === 'user_message' && lastText === nextText) {
            return prev;
          }
        }
        const next = [...prev, event];
        const maxLines = config?.ui.max_output_lines ?? 1000;
        if (next.length > maxLines) {
          return next.slice(-maxLines);
        }
        return next;
      });
    },
    [config]
  );

  const handleEvent = useCallback(
    (event: TerminalEvent) => {
      let enriched = event;

      if (event.type === 'conversation_turn') {
        const turnNumber = Number(event.data.turn_number ?? 0);
        activeTurnRef.current = turnNumber;
      }

      if (event.type === 'tool_start') {
        enriched = {
          ...event,
          meta: {
            ...(event.meta ?? {}),
            turn: activeTurnRef.current,
          },
        };
      }

      appendEvent(enriched);

      if (event.type === 'agent_start') {
        setStatus('running');
        setRunningStartTime(new Date().toISOString());
        setError(null);
        // Auto-collapse File Explorer when request processing starts to prevent UI blinking
        setFileExplorerVisible(false);
        const eventSessionId = String(event.data.session_id ?? '');
        setCurrentSession((prev) => ({
          id: prev?.id || eventSessionId || 'unknown',
          status: 'running',
          task: (event.data.task as string | undefined) ?? prev?.task,
          model: (event.data.model as string | undefined) ?? prev?.model,
          created_at: prev?.created_at ?? new Date().toISOString(),
          updated_at: new Date().toISOString(),
          completed_at: prev?.completed_at ?? null,
          num_turns: prev?.num_turns ?? 0,
          duration_ms: prev?.duration_ms ?? null,
          total_cost_usd: prev?.total_cost_usd ?? null,
          cancel_requested: prev?.cancel_requested ?? false,
        }));
        setStats((prev) => ({
          ...prev,
          model: String(event.data.model ?? prev.model ?? ''),
        }));
      }

      if (event.type === 'agent_complete') {
        const normalizedStatus = normalizeStatus(String(event.data.status ?? 'complete'));
        const usage = event.data.usage as {
          input_tokens?: number;
          output_tokens?: number;
          cache_creation_input_tokens?: number;
          cache_read_input_tokens?: number;
        } | undefined;
        const newTokensIn = usage
          ? (usage.input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0)
          : 0;
        const newTokensOut = usage?.output_tokens ?? 0;
        const cumulativeTurns = Number(event.data.cumulative_turns ?? event.data.num_turns ?? 0);
        const cumulativeCost = Number(event.data.cumulative_cost_usd ?? event.data.total_cost_usd ?? 0);

        setStats((prev) => ({
          ...prev,
          turns: cumulativeTurns || prev.turns + Number(event.data.num_turns ?? 0),
          durationMs: prev.durationMs + Number(event.data.duration_ms ?? 0),
          cost: event.data.total_cost_usd !== undefined
            ? cumulativeCost || Number(event.data.total_cost_usd ?? 0)
            : prev.cost,
          tokensIn: usage ? prev.tokensIn + newTokensIn : prev.tokensIn,
          tokensOut: usage ? prev.tokensOut + newTokensOut : prev.tokensOut,
        }));
        setStatus(normalizedStatus);
        setRunningStartTime(null);

        const completedAt = new Date().toISOString();
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: normalizedStatus,
                completed_at: completedAt,
                updated_at: completedAt,
                num_turns: prev.num_turns + Number(event.data.num_turns ?? 0),
              }
            : null
        );
        // Update sessions list with timestamps so elapsed timer stops immediately
        setSessions((prev) =>
          prev.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: normalizedStatus, completed_at: completedAt, updated_at: completedAt }
              : session
          )
        );

        // Invalidate cache and refresh to sync with server
        invalidateSessionsCache();
        refreshSessions();

        // Auto-refresh File Explorer (preserves expanded/collapsed state)
        setFileExplorerRefreshTrigger(prev => prev + 1);
      }

      // Handle status_update event (emitted by agent_runner after pending question check)
      // This corrects the status when agent stops due to AskUserQuestion
      if (event.type === 'status_update') {
        const newStatus = normalizeStatus(String(event.data.status ?? ''));
        if (newStatus) {
          setStatus(newStatus);
          const updatedAt = new Date().toISOString();
          setCurrentSession((prev) =>
            prev
              ? {
                  ...prev,
                  status: newStatus,
                  updated_at: updatedAt,
                }
              : null
          );
          setSessions((prev) =>
            prev.map((session) =>
              session.id === currentSession?.id
                ? { ...session, status: newStatus, updated_at: updatedAt }
                : session
            )
          );
        }
      }

      if (event.type === 'cancelled') {
        setStatus('cancelled');
        setRunningStartTime(null);
        // Check if session is resumable (has resume_id established)
        const resumable = Boolean(event.data?.resumable);
        const cancelledAt = new Date().toISOString();
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'cancelled',
                completed_at: cancelledAt,
                updated_at: cancelledAt,
                resumable,
              }
            : null
        );
        // Update sessions list with timestamps so elapsed timer stops immediately
        setSessions((prev) =>
          prev.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: 'cancelled', completed_at: cancelledAt, updated_at: cancelledAt }
              : session
          )
        );
        // Invalidate cache and refresh to sync with server
        invalidateSessionsCache();
        refreshSessions();
      }

      // Handle queue_started event - task started after being queued
      if (event.type === 'queue_started') {
        // Update status from 'queued' to 'running'
        setStatus('running');
        setRunningStartTime(new Date().toISOString());
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'running',
                queue_position: null,
                queued_at: null,
              }
            : null
        );
        // Update session in list
        setSessions((prevSessions) =>
          prevSessions.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: 'running', queue_position: null }
              : session
          )
        );
      }

      // Handle queue_position_update event - position changed while waiting in queue
      if (event.type === 'queue_position_update') {
        const newPosition = event.data?.position as number | undefined;
        if (newPosition !== undefined) {
          // Update current session's queue position
          setCurrentSession((prev) =>
            prev
              ? {
                  ...prev,
                  queue_position: newPosition,
                }
              : null
          );
          // Update session in list
          setSessions((prevSessions) =>
            prevSessions.map((session) =>
              session.id === currentSession?.id
                ? { ...session, queue_position: newPosition }
                : session
            )
          );
        }
      }

      if (event.type === 'metrics_update') {
        setStats((prev) => ({
          ...prev,
          turns: Number(event.data.turns ?? prev.turns),
          tokensIn: Number(event.data.tokens_in ?? prev.tokensIn),
          tokensOut: Number(event.data.tokens_out ?? prev.tokensOut),
          cost: event.data.total_cost_usd !== undefined ? Number(event.data.total_cost_usd) : prev.cost,
          model: String(event.data.model ?? prev.model ?? ''),
        }));
      }

      if (event.type === 'error') {
        setStatus('failed');
        setRunningStartTime(null);
        setError(String(event.data.message ?? 'Unknown error'));
        const nowIso = new Date().toISOString();
        // Update session status so next submit can continue rather than reset
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'failed',
                completed_at: nowIso,
                updated_at: nowIso,
              }
            : null
        );
        // Update sessions list with timestamps so elapsed timer stops
        setSessions((prevSessions) =>
          prevSessions.map((session) =>
            session.id === currentSession?.id
              ? { ...session, status: 'failed', completed_at: nowIso, updated_at: nowIso }
              : session
          )
        );
        // Invalidate cache and refresh to sync with server
        invalidateSessionsCache();
        refreshSessions();
      }

      // Handle security alert events (sensitive data detected and redacted)
      if (event.type === 'security_alert') {
        const alertData = event.data as {
          message?: string;
          type_labels?: string[];
          files_with_secrets?: number;
          total_secrets?: number;
        };
        setSecurityAlert({
          message: alertData.message ?? 'Sensitive data was detected and redacted.',
          typeLabels: alertData.type_labels ?? [],
          filesWithSecrets: alertData.files_with_secrets ?? 0,
          totalSecrets: alertData.total_secrets ?? 0,
        });
      }
    },
    [appendEvent, currentSession, refreshSessions]
  );

  const startSSE = useCallback(
    (sessionId: string, lastSequence?: number | null) => {
      if (!config || !token) {
        return;
      }

      if (cleanupRef.current) {
        cleanupRef.current();
      }

      cleanupRef.current = connectSSE(
        config.api.base_url,
        sessionId,
        token,
        (event) => {
          setReconnecting(false);
          handleEvent(event);
        },
        (err) => {
          setReconnecting(false);
          setError(err.message);
        },
        (attempt) => {
          setReconnecting(true);
          // Don't show error message - the status indicator shows reconnecting state
          // Only show error after many failed attempts (handled by connection state change)
          if (attempt > 3) {
            setError(`Reconnecting (attempt ${attempt})...`);
          }
        },
        lastSequence ?? null,
        // Heartbeat callback - can detect session completion from heartbeat
        (heartbeatData) => {
          if (heartbeatData.session_status &&
              ['completed', 'failed', 'cancelled'].includes(heartbeatData.session_status)) {
            // Session ended - invalidate cache and refresh to get final state
            invalidateSessionsCache();
            refreshSessions();
          }
        },
        // Connection state change callback
        (state) => {
          const previousState = connectionState;
          setConnectionState(state);
          if (state === 'connected') {
            setReconnecting(false);
            setError(null);
            // RESYNC ON RECONNECT: When transitioning to connected from degraded/reconnecting/polling,
            // refresh sessions to ensure we have the latest state (terminal status protection applies)
            if (previousState === 'reconnecting' || previousState === 'polling' || previousState === 'degraded') {
              invalidateSessionsCache();
              refreshSessions();
            }
          } else if (state === 'polling') {
            // Polling mode still works - just a different transport
            setError(null);
          } else if (state === 'degraded') {
            // Only show error text for truly degraded state
            setError('Connection unstable');
          } else if (state === 'reconnecting') {
            // Clear error - status indicator shows reconnecting state
            setError(null);
          }
        }
      );
    },
    [config, token, handleEvent, refreshSessions]
  );

  const handleSubmit = async (): Promise<void> => {
    if (!config || !token || !inputValue.trim()) {
      return;
    }

    const taskText = inputValue.trim();
    setError(null);
    setStatus('running');
    setRunningStartTime(new Date().toISOString());
    activeTurnRef.current = 0;

    // Helper to format file size for display
    const formatFileSizeForContext = (bytes: number): string => {
      if (bytes < 1024) return `${bytes}B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    };

    // Helper to upload files to a session and return file context string
    const uploadAndBuildContext = async (sessionId: string): Promise<string> => {
      if (attachedFiles.length === 0) return '';

      try {
        const result = await uploadFiles(
          config.api.base_url,
          token,
          sessionId,
          attachedFiles.map((f) => f.file)
        );

        let fileContext = '';
        if (result.uploaded.length > 0) {
          fileContext =
            '\n\n<uploaded-files>\nThe following files have been uploaded to the workspace:\n' +
            result.uploaded
              .map((f) => `- ${f.path} (${formatFileSizeForContext(f.size)}, ${f.mime_type})`)
              .join('\n') +
            '\n</uploaded-files>';
        }

        // Report errors but continue
        if (result.errors.length > 0) {
          setError(`Some files failed to upload: ${result.errors.join(', ')}`);
        }

        return fileContext;
      } catch (err) {
        // Log error but don't block the task
        console.error('File upload failed:', err);
        setError(`File upload failed: ${(err as Error).message}`);
        return '';
      }
    };

    // Build preliminary file context (describes files before upload completes)
    // Uses YAML format for structured metadata that the UI can render nicely
    const buildPreliminaryFileContext = (): string => {
      if (attachedFiles.length === 0) return '';

      // Security: Sanitize filename to prevent injection attacks
      // - Remove control characters and non-printable chars
      // - Remove path traversal attempts
      // - Truncate to reasonable length
      // - Escape special characters for YAML
      const sanitizeFilename = (name: string): string => {
        const MAX_FILENAME_LENGTH = 255;

        let sanitized = name
          // Remove null bytes and control characters (0x00-0x1F, 0x7F)
          .replace(/[\x00-\x1F\x7F]/g, '')
          // Remove path traversal sequences
          .replace(/\.\.\//g, '')
          .replace(/\.\.\\/g, '')
          // Remove leading/trailing dots and spaces (Windows issues)
          .replace(/^[\s.]+|[\s.]+$/g, '')
          // Replace multiple spaces/dots with single
          .replace(/\s+/g, ' ')
          // Remove characters that could break YAML or cause issues
          .replace(/[<>:"|?*\x00-\x1F]/g, '_');

        // Truncate if too long (preserve extension)
        if (sanitized.length > MAX_FILENAME_LENGTH) {
          const lastDot = sanitized.lastIndexOf('.');
          if (lastDot > 0 && sanitized.length - lastDot <= 10) {
            // Preserve extension (up to 10 chars)
            const ext = sanitized.slice(lastDot);
            const nameWithoutExt = sanitized.slice(0, MAX_FILENAME_LENGTH - ext.length - 3);
            sanitized = nameWithoutExt + '...' + ext;
          } else {
            sanitized = sanitized.slice(0, MAX_FILENAME_LENGTH - 3) + '...';
          }
        }

        // If completely empty after sanitization, use placeholder
        return sanitized || 'unnamed_file';
      };

      // Helper to get file extension (sanitized)
      const getExtension = (filename: string): string => {
        const lastDot = filename.lastIndexOf('.');
        if (lastDot <= 0) return '';
        // Limit extension to alphanumeric, max 10 chars
        const ext = filename.slice(lastDot + 1).toLowerCase();
        return ext.replace(/[^a-z0-9]/g, '').slice(0, 10);
      };

      // Security: Sanitize MIME type
      const sanitizeMimeType = (mime: string): string => {
        // MIME types should only contain: a-z, 0-9, /, -, +, .
        const sanitized = mime.toLowerCase().replace(/[^a-z0-9/\-+.]/g, '');
        // Limit length
        return sanitized.slice(0, 100);
      };

      // Build YAML entries for each file
      const fileEntries = attachedFiles.map((f) => {
        const file = f.file;
        const safeName = sanitizeFilename(file.name);
        const ext = getExtension(file.name);
        // Format last modified as ISO string if available
        const lastModified = file.lastModified
          ? new Date(file.lastModified).toISOString()
          : null;

        // Build YAML block for this file
        // Escape quotes and backslashes for YAML string safety
        const yamlEscape = (s: string) => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');

        let yaml = `- name: "${yamlEscape(safeName)}"`;
        yaml += `\n  size: ${Math.max(0, Math.floor(file.size))}`; // Ensure non-negative integer
        yaml += `\n  size_formatted: "${formatFileSizeForContext(file.size)}"`;
        if (file.type) {
          yaml += `\n  mime_type: "${yamlEscape(sanitizeMimeType(file.type))}"`;
        }
        if (ext) {
          yaml += `\n  extension: "${ext}"`;
        }
        if (lastModified) {
          yaml += `\n  last_modified: "${lastModified}"`;
        }
        return yaml;
      }).join('\n');

      return (
        '\n\n<attached-files>\nfiles:\n' + fileEntries + '\n</attached-files>'
      );
    };

    // Check if we can continue the session:
    // - Session exists and is not running
    // - If cancelled, it must be resumable (has resume_id from agent_start)
    const canContinue = currentSession && currentSession.status !== 'running';
    const isCancelledNotResumable =
      currentSession?.status === 'cancelled' && currentSession.resumable === false;

    // If session was cancelled before agent_start, we can't resume - start fresh
    const shouldContinue = canContinue && !isCancelledNotResumable;

    if (shouldContinue && currentSession) {
      // CONTINUE EXISTING SESSION
      // For continuing sessions, upload files first so they're available immediately

      // Close old SSE connection before appending user event
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }

      try {
        // Upload files first (if any) so they're available when agent starts
        const fileContext = await uploadAndBuildContext(currentSession.id);
        const fullTaskText = taskText + fileContext;

        const userEvent: TerminalEvent = {
          type: 'user_message',
          data: { text: fullTaskText },
          timestamp: new Date().toISOString(),
          sequence: Date.now(),
        };
        appendEvent(userEvent);

        const response = await continueTask(
          config.api.base_url,
          token,
          currentSession.id,
          fullTaskText,
          selectedModel
        );

        setCurrentSession((prev) => ({
          ...prev!,
          status: response.status,
          updated_at: new Date().toISOString(),
        }));

        // CRITICAL: Also update sessions[] directly so the session panel shows 'running'
        // We can't rely on refreshSessions() because terminal status protection would
        // keep the old 'completed' status if the server hasn't updated yet.
        setSessions((prev) =>
          prev.map((s) =>
            s.id === currentSession.id
              ? { ...s, status: response.status, updated_at: new Date().toISOString() }
              : s
          )
        );

        setInputValue('');
        setAttachedFiles([]);
        const lastSequence = getLastServerSequence(events);
        startSSE(currentSession.id, lastSequence);
        // Invalidate cache since session status changed
        invalidateSessionsCache();
        refreshSessions();
      } catch (err) {
        setStatus('failed');
        const errorMessage = (err as Error).message;
        if (errorMessage.includes('cannot be resumed')) {
          setError(
            'Session cannot be resumed. The agent was cancelled before it could start. ' +
              'Your next message will start a new session.'
          );
          setCurrentSession((prev) => (prev ? { ...prev, resumable: false } : null));
        } else {
          setError(`Failed to continue task: ${errorMessage}`);
        }
      }
    } else {
      // START NEW SESSION
      // For new sessions, include preliminary file context in task,
      // then upload files immediately after session is created

      const preliminaryFileContext = buildPreliminaryFileContext();
      const fullTaskText = taskText + preliminaryFileContext;

      const userEvent: TerminalEvent = {
        type: 'user_message',
        data: { text: fullTaskText },
        timestamp: new Date().toISOString(),
        sequence: Date.now(),
      };

      setEvents([userEvent]);
      setExpandedTools(new Set());
      setExpandedSubagents(new Set());
      setExpandedComments(new Set());
      setExpandedFiles(new Set());
      setStats({
        turns: 0,
        cost: 0,
        durationMs: 0,
        tokensIn: 0,
        tokensOut: 0,
        model: '',
      });

      try {
        const response = await runTask(config.api.base_url, token, fullTaskText, selectedModel, dynamicMounts);
        const sessionId = response.session_id;

        // Upload files to the new session (agent will see them in workspace)
        if (attachedFiles.length > 0) {
          // Upload in background - don't block SSE start
          uploadFiles(
            config.api.base_url,
            token,
            sessionId,
            attachedFiles.map((f) => f.file)
          ).catch((err) => {
            console.error('File upload failed:', err);
            setError(`File upload failed: ${(err as Error).message}`);
          });
        }

        setCurrentSession({
          id: sessionId,
          status: response.status,
          task: taskText,
          model: selectedModel,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          completed_at: null,
          num_turns: 0,
          duration_ms: null,
          total_cost_usd: null,
          cancel_requested: false,
          queue_position: response.queue_position ?? null,
          queued_at: response.status === 'queued' ? new Date().toISOString() : null,
          is_auto_resume: false,
        });
        setInputValue('');
        setAttachedFiles([]);
        startSSE(sessionId, null);
        // Invalidate cache since new session was created
        invalidateSessionsCache();
        refreshSessions();
        navigate(`/session/${sessionId}/`, { replace: true });
      } catch (err) {
        setStatus('failed');
        setError(`Failed to start task: ${(err as Error).message}`);
      }
    }
  };

  // Handler for AskUserQuestion tool responses (human-in-the-loop)
  // Submits answer to the database, then resumes the session
  const handleSubmitAnswer = useCallback(async (answer: string): Promise<void> => {
    if (!config || !token || !currentSession || !answer.trim()) {
      return;
    }

    const answerText = answer.trim();

    try {
      // Step 1: Submit answer to the API endpoint (stores in database)
      const response = await fetch(
        `${config.api.base_url}/api/v1/sessions/${currentSession.id}/answer`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify({
            question_id: 'latest',
            answer: answerText,
          }),
        }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const result = await response.json();

      // Step 2: If the session can be resumed, resume it automatically
      if (result.can_resume) {
        // Add an event showing the user's answer
        const answerEvent: TerminalEvent = {
          type: 'user_message',
          data: { text: `[Answer submitted: ${answerText}]` },
          timestamp: new Date().toISOString(),
          sequence: Date.now(),
        };
        appendEvent(answerEvent);

        // Resume the session to continue agent execution
        setStatus('running');
        setRunningStartTime(new Date().toISOString());

        try {
          const resumeResponse = await continueTask(
            config.api.base_url,
            token,
            currentSession.id,
            // Send a simple resume message - the answer is already in context
            'Continue with the user\'s answer.',
            selectedModel
          );

          setCurrentSession((prev) => ({
            ...prev!,
            status: resumeResponse.status,
            updated_at: new Date().toISOString(),
          }));

          // Start SSE to receive agent's continued response
          const lastSequence = getLastServerSequence(events);
          startSSE(currentSession.id, lastSequence);
        } catch (resumeErr) {
          setStatus('failed');
          setError(`Failed to resume session: ${(resumeErr as Error).message}`);
        }
      }
    } catch (err) {
      setError(`Failed to submit answer: ${(err as Error).message}`);
    }
  }, [config, token, currentSession, selectedModel, events, appendEvent, startSSE]);

  const handleCancel = async (): Promise<void> => {
    if (!config || !token || !currentSession) {
      return;
    }

    try {
      await cancelSession(config.api.base_url, token, currentSession.id);
      setStatus('cancelled');
    } catch (err) {
      setError(`Failed to cancel: ${(err as Error).message}`);
    }
  };

  const handleAttachFiles = useCallback((files: File[]) => {
    const newFiles: AttachedFile[] = files.map((file) => ({
      file,
      id: `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    }));
    setAttachedFiles((prev) => [...prev, ...newFiles]);
  }, []);

  const handleRemoveFile = useCallback((id: string) => {
    setAttachedFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  const handleModelChange = useCallback((model: string) => {
    setSelectedModel(model);
    setString('ag3ntum_selected_model', model);
  }, []);

  /**
   * Scroll output to bottom. If distance > 1000px, jump instantly.
   * When forceInstant is true, scrolls multiple times to catch lazy-loaded content.
   */
  const scrollOutputToBottom = useCallback((forceInstant = false) => {
    const doScroll = () => {
      if (!outputRef.current) return;
      const { scrollTop, scrollHeight, clientHeight } = outputRef.current;
      const distanceToBottom = scrollHeight - scrollTop - clientHeight;

      // Jump instantly if distance > 1000px or forceInstant is true
      if (forceInstant || distanceToBottom > 1000) {
        outputRef.current.scrollTop = scrollHeight;
      } else {
        outputRef.current.scrollTo({
          top: scrollHeight,
          behavior: 'smooth',
        });
      }
    };

    // First scroll immediately after next frame
    requestAnimationFrame(doScroll);

    // When loading a session, scroll multiple times to catch lazy-loaded content
    // (images, embedded documents, syntax highlighting, etc.)
    if (forceInstant) {
      setTimeout(doScroll, 50);   // After initial render
      setTimeout(doScroll, 150);  // After most content loads
      setTimeout(doScroll, 400);  // After images/heavy content
    }
  }, []);

  const handleSelectSession = async (sessionId: string): Promise<void> => {
    if (!config || !token) {
      return;
    }

    // Validate session ID format before making API calls
    if (!isValidSessionId(sessionId)) {
      setError('Invalid session ID format');
      navigate('/', { replace: true });
      return;
    }

    try {
      const session = await getSession(config.api.base_url, token, sessionId);
      setCurrentSession(session);

      const historyEvents = await getSessionEvents(config.api.base_url, token, sessionId);
      const lastSequence = getLastServerSequence(historyEvents);
      setEvents(seedSessionEvents(session, historyEvents));

      // Scroll to bottom after loading session (instant jump for long conversations)
      scrollOutputToBottom(true);

      // Sum up stats from ALL agent_complete events (for multi-turn/resumed sessions)
      const completionEvents = historyEvents.filter((event) => event.type === 'agent_complete');
      if (completionEvents.length > 0) {
        let totalTokensIn = 0;
        let totalTokensOut = 0;
        let totalDurationMs = 0;
        completionEvents.forEach((event) => {
          const usage = (event.data.usage ?? null) as
            | {
                input_tokens?: number;
                output_tokens?: number;
                cache_creation_input_tokens?: number;
                cache_read_input_tokens?: number;
              }
            | null;
          if (usage) {
            totalTokensIn +=
              (usage.input_tokens ?? 0) +
              (usage.cache_creation_input_tokens ?? 0) +
              (usage.cache_read_input_tokens ?? 0);
            totalTokensOut += usage.output_tokens ?? 0;
          }
          totalDurationMs += Number(event.data.duration_ms ?? 0);
        });

        // Use the last completion event for cumulative turns/cost (backend provides these)
        const lastCompletion = completionEvents[completionEvents.length - 1];
        const cumulativeTurns = Number(lastCompletion.data.cumulative_turns ?? 0);
        const cumulativeCost = Number(lastCompletion.data.cumulative_cost_usd ?? 0);

        setStats({
          turns: cumulativeTurns || Number(lastCompletion.data.num_turns ?? session.num_turns),
          cost: cumulativeCost || Number(lastCompletion.data.total_cost_usd ?? session.total_cost_usd ?? 0),
          durationMs: totalDurationMs || Number(session.duration_ms ?? 0),
          tokensIn: totalTokensIn,
          tokensOut: totalTokensOut,
          model: String(lastCompletion.data.model ?? session.model ?? ''),
        });
      } else {
        const lastMetrics = [...historyEvents].reverse().find((event) => event.type === 'metrics_update');
        if (lastMetrics) {
          setStats((prev) => ({
            ...prev,
            turns: Number(lastMetrics.data.turns ?? prev.turns),
            tokensIn: Number(lastMetrics.data.tokens_in ?? prev.tokensIn),
            tokensOut: Number(lastMetrics.data.tokens_out ?? prev.tokensOut),
            cost: lastMetrics.data.total_cost_usd !== undefined ? Number(lastMetrics.data.total_cost_usd) : prev.cost,
            model: String(lastMetrics.data.model ?? session.model ?? prev.model ?? ''),
          }));
        }
      }

      setStatus(normalizeStatus(session.status));

      // Start SSE for running or queued sessions (to receive queue_started event)
      if (session.status === 'running' || session.status === 'queued') {
        startSSE(sessionId, lastSequence);
      }

      // Update URL to reflect selected session
      navigate(`/session/${sessionId}/`, { replace: true });
    } catch (err) {
      // Parse error message - API returns JSON like {"detail":"Session not found: xyz"}
      let errorMessage = (err as Error).message;
      try {
        const parsed = JSON.parse(errorMessage);
        if (parsed.detail) {
          errorMessage = parsed.detail;
        }
      } catch {
        // Not JSON, use as-is
      }

      // Show prominent error popup
      setSessionErrorPopup({
        message: errorMessage,
        sessionId,
      });
    }
  };

  /**
   * Handle session error popup close - navigate to home.
   */
  const handleSessionErrorClose = useCallback(() => {
    setSessionErrorPopup(null);
    setCurrentSession(null);
    setEvents(EMPTY_EVENTS);
    setStatus('idle');
    navigate('/', { replace: true });
  }, [navigate]);

  /**
   * Handle session deletion from SessionListTab.
   */
  const handleDeleteSession = useCallback(async (sessionId: string): Promise<void> => {
    if (!config || !token) {
      return;
    }

    try {
      await deleteSession(config.api.base_url, token, sessionId);

      // If deleting current session, clear it
      if (currentSession?.id === sessionId) {
        setCurrentSession(null);
        setEvents([]);
        setStatus('idle');
        navigate('/', { replace: true });
      }

      // Refresh sessions list
      invalidateSessionsCache();
      refreshSessions();

      // Show success notification
      toast.success(`Session ${sessionId} deleted`);
    } catch (err) {
      toast.error(`Failed to delete session: ${(err as Error).message}`);
      throw err; // Re-throw so the modal can handle the error state
    }
  }, [config, token, currentSession, navigate, refreshSessions]);

  const handleNewSession = useCallback((): void => {
    if (cleanupRef.current) {
      cleanupRef.current();
    }
    setCurrentSession(null);
    setEvents([]);
    setStatus('idle');
    setExpandedTools(new Set());
    setExpandedSubagents(new Set());
    setExpandedComments(new Set());
    setExpandedFiles(new Set());
    setAttachedFiles([]);
    setMobileExpandedMessages(new Set());
    setStats({
      turns: 0,
      cost: 0,
      durationMs: 0,
      tokensIn: 0,
      tokensOut: 0,
      model: '',
    });
    // Navigate to root URL for new session
    navigate('/', { replace: true });
  }, [navigate]);

  const toggleRightPanel = useCallback(() => {
    setRightPanelCollapsed((prev) => {
      const next = !prev;
      setBoolean('ag3ntum_right_panel_collapsed', next);
      return next;
    });
  }, []);

  const toggleMobileMessageExpand = useCallback((id: string) => {
    setMobileExpandedMessages((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // Divider drag handlers for resizable panels
  const handleDividerMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDraggingDivider(true);
  }, []);

  useEffect(() => {
    if (!isDraggingDivider) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!mainRef.current) return;
      const mainRect = mainRef.current.getBoundingClientRect();
      const newWidth = mainRect.right - e.clientX;
      // Clamp between min (250px) and max (70% of main width)
      const minWidth = 250;
      const maxWidth = mainRect.width * 0.7;
      const clampedWidth = Math.max(minWidth, Math.min(maxWidth, newWidth));
      setRightPanelWidth(clampedWidth);
    };

    const handleMouseUp = () => {
      setIsDraggingDivider(false);
      // Persist to localStorage
      setNumber('ag3ntum_right_panel_width', rightPanelWidth);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDraggingDivider, rightPanelWidth]);

  const conversation = useConversation(events);

  // Auto-select the latest agent message for the right panel
  // Only update when streaming is complete to reduce blinking and unnecessary re-renders
  useEffect(() => {
    // Find the last agent_message or output with content
    const messagesWithDetails = conversation.filter(
      (item) => item.type === 'agent_message' || item.type === 'output'
    );

    if (messagesWithDetails.length > 0) {
      const lastMessage = messagesWithDetails[messagesWithDetails.length - 1];

      // Check if the last message is still streaming - skip update if so
      const isLastMessageStreaming = lastMessage.type === 'agent_message' && lastMessage.isStreaming;

      // Auto-select the latest message when:
      // 1. Nothing is selected yet
      // 2. The selected message no longer exists
      // 3. Session is running AND streaming is complete (to follow the active message without flickering)
      const selectedExists = conversation.find((item) => item.id === selectedMessageId);
      const shouldAutoSelect = !selectedMessageId || !selectedExists || (status === 'running' && !isLastMessageStreaming);

      // Only update state if the ID actually changes to avoid unnecessary re-renders
      if (shouldAutoSelect && lastMessage.id !== selectedMessageId) {
        setSelectedMessageId(lastMessage.id);
      }
    }
  }, [conversation, selectedMessageId, status]);

  const toolStats = useMemo(() => {
    const statsMap: Record<string, number> = {};
    conversation.forEach((item) => {
      if (item.type === 'agent_message') {
        item.toolCalls.forEach((tool) => {
          const toolName = tool.tool;
          statsMap[toolName] = (statsMap[toolName] ?? 0) + 1;
        });
      }
    });
    return statsMap;
  }, [conversation]);

  const totalToolCalls = Object.values(toolStats).reduce((sum, count) => sum + count, 0);

  const subagentStats = useMemo(() => {
    const statsMap: Record<string, number> = {};
    conversation.forEach((item) => {
      if (item.type === 'agent_message') {
        item.subagents.forEach((subagent) => {
          const subagentName = subagent.name;
          statsMap[subagentName] = (statsMap[subagentName] ?? 0) + 1;
        });
      }
    });
    return statsMap;
  }, [conversation]);

  const totalSubagentCalls = Object.values(subagentStats).reduce((sum, count) => sum + count, 0);

  // Memoize the selected message for the right panel to avoid re-renders during streaming
  // Only update when streaming is complete or tool calls/subagents change
  const selectedMessageForRightPanel = useMemo(() => {
    if (!selectedMessageId) {
      lastStableRightPanelMessageRef.current = null;
      return null;
    }
    const message = conversation.find((item) => item.id === selectedMessageId);
    if (!message) {
      // Keep the last stable message if the selected one doesn't exist yet
      return lastStableRightPanelMessageRef.current;
    }

    // For agent messages that are streaming, only update when there's meaningful content
    if (message.type === 'agent_message' && message.isStreaming) {
      const hasToolsOrSubagents = message.toolCalls.length > 0 || message.subagents.length > 0;

      // If streaming with no tools/subagents, keep the last stable message
      if (!hasToolsOrSubagents) {
        // If the last stable message was for a different ID, show empty state for new message
        if (lastStableRightPanelMessageRef.current?.id !== selectedMessageId) {
          return null;
        }
        return lastStableRightPanelMessageRef.current;
      }
    }

    // Update the stable reference and return the message
    lastStableRightPanelMessageRef.current = message;
    return message;
  }, [conversation, selectedMessageId]);

  // Extract system events (permission denials, hook triggers, profile switches)
  const systemEvents = useMemo<SystemEventView[]>(() => {
    const sysEvents: SystemEventView[] = [];
    let eventCounter = 0;

    events.forEach((event) => {
      if (event.type === 'hook_triggered') {
        const decision = String(event.data.decision ?? '');
        // Only show permission denials (not allows) to keep UI clean
        if (decision === 'deny') {
          sysEvents.push({
            id: `sys-${eventCounter++}`,
            time: formatTimestamp(event.timestamp),
            eventType: 'permission_denied',
            toolName: String(event.data.tool_name ?? ''),
            decision,
            message: String(event.data.message ?? ''),
          });
        }
      } else if (event.type === 'profile_switch') {
        sysEvents.push({
          id: `sys-${eventCounter++}`,
          time: formatTimestamp(event.timestamp),
          eventType: 'profile_switch',
          profileName: String(event.data.profile_name ?? ''),
        });
      } else if (event.type === 'queue_started') {
        const wasAutoResume = Boolean(event.data?.was_auto_resume);
        sysEvents.push({
          id: `sys-${eventCounter++}`,
          time: formatTimestamp(event.timestamp),
          eventType: 'queue_started',
          message: wasAutoResume ? 'Auto-resumed task started' : 'Queued task started',
        });
      }
    });

    return sysEvents;
  }, [events]);

  const headerStats = useMemo(() => {
    const counts = { complete: 0, partial: 0, failed: 0 };
    conversation.forEach((item) => {
      if (item.type === 'output') {
        if (item.status === 'complete') {
          counts.complete += 1;
        } else if (item.status === 'partial') {
          counts.partial += 1;
        } else if (item.status === 'failed') {
          counts.failed += 1;
        }
      }
    });
    return counts;
  }, [conversation]);

  const outputItems = useMemo(() => conversation.filter((item) => item.type === 'output'), [conversation]);

  const todosByAgentId = useMemo(() => {
    const todosMap = new Map<
      string,
      { todos: TodoItem[]; status: ResultStatus | undefined }
    >();
    const userIndices = conversation
      .map((item, index) => (item.type === 'user' ? index : -1))
      .filter((index) => index >= 0);

    const segmentStarts = userIndices.length > 0 ? userIndices : [-1];

    segmentStarts.forEach((userIndex, segmentIndex) => {
      const start = userIndex + 1;
      const end = segmentIndex + 1 < segmentStarts.length
        ? segmentStarts[segmentIndex + 1]
        : conversation.length;
      if (start >= end) {
        return;
      }

      let todos: TodoItem[] | null = null;
      let firstTodoIndex = -1;
      const agentIndices: number[] = [];
      let lastAgentIndex = -1;

      for (let i = start; i < end; i += 1) {
        const item = conversation[i];
        if (item.type === 'agent_message') {
          agentIndices.push(i);
          lastAgentIndex = i;
          const foundTodos = extractTodos(item.toolCalls);
          if (foundTodos && foundTodos.length > 0) {
            todos = foundTodos;
            if (firstTodoIndex === -1) {
              firstTodoIndex = i;
            }
          }
        }
      }

      if (!todos || agentIndices.length === 0 || lastAgentIndex < 0) {
        return;
      }

      let terminalStatus: ResultStatus | undefined;
      const lastSegmentItem = conversation[end - 1];
      if (lastSegmentItem?.type === 'output') {
        terminalStatus = lastSegmentItem.status;
      } else if (lastAgentIndex >= 0) {
        const lastAgent = conversation[lastAgentIndex];
        if (lastAgent.type === 'agent_message') {
          terminalStatus = lastAgent.status as ResultStatus | undefined;
        }
      }
      if (!terminalStatus && end === conversation.length && status !== 'running') {
        terminalStatus = normalizeStatus(status) as ResultStatus;
      }

      const targetIndex = lastAgentIndex;
      if (targetIndex < firstTodoIndex) {
        return;
      }
      const agentItem = conversation[targetIndex];
      if (agentItem.type === 'agent_message') {
        todosMap.set(agentItem.id, {
          todos,
          status: terminalStatus,
        });
      }
    });

    return todosMap;
  }, [conversation, status]);


  // Auto-scroll effect - using useLayoutEffect to run synchronously after DOM updates
  useLayoutEffect(() => {
    if (!outputRef.current || !config?.ui.auto_scroll) {
      return;
    }
    // Scroll to bottom after conversation is rendered
    outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [conversation, config]);

  const sessionDuration = formatDuration(stats.durationMs);
  const sessionIdLabel = currentSession?.id ?? 'new';

  const sessionItems = useMemo(() => {
    return sessions.map((session) => (
      <button
        key={session.id}
        className="session-item"
        onClick={() => handleSelectSession(session.id)}
        type="button"
      >
        <div className="session-item-row">
          <span className="session-id">{session.id.slice(0, 8)}...</span>
          <span className={`session-status ${session.status}`}>
            {session.status === 'queued' && session.queue_position != null
              ? `queued #${session.queue_position}`
              : session.status}
          </span>
        </div>
        <div className="session-task">{truncateSessionTitle(session.task)}</div>
      </button>
    ));
  }, [sessions]);

  const handleFileAction = async (filePath: string, mode: 'view' | 'download') => {
    if (!config || !token || !currentSession) {
      return;
    }
    if (!isSafeRelativePath(filePath)) {
      setError('Refusing to open unsafe file path.');
      return;
    }

    if (mode === 'view') {
      // Open in FileViewerModal
      try {
        setViewerLoading(true);
        setViewerFile(null);
        setViewerImageUrl(undefined);
        const response = await getFileContent(config.api.base_url, token, currentSession.id, filePath);
        const fileData = toFileViewerData(response);
        setViewerFile(fileData);
        // If it's an image, set the image URL for the viewer
        if (fileData.mimeType.startsWith('image/')) {
          setViewerImageUrl(getFileDownloadUrl(config.api.base_url, token, currentSession.id, filePath));
        }
      } catch (err) {
        setError(`Failed to load file: ${(err as Error).message}`);
      } finally {
        setViewerLoading(false);
      }
    } else {
      // Download mode - download the file with authentication
      try {
        await downloadFile(config.api.base_url, token, currentSession.id, filePath);
      } catch (err) {
        console.error('Download failed:', err);
      }
    }
  };

  const handleCloseViewer = useCallback(() => {
    setViewerFile(null);
    setViewerImageUrl(undefined);
  }, []);

  const handleViewerDownload = useCallback(async () => {
    if (!config || !token || !currentSession || !viewerFile?.path) {
      return;
    }
    try {
      await downloadFile(config.api.base_url, token, currentSession.id, viewerFile.path);
    } catch (err) {
      console.error('Download failed:', err);
    }
  }, [config, token, currentSession, viewerFile?.path]);

  const handleShowInExplorer = useCallback((filePath: string) => {
    // Open file explorer and navigate to the file
    setRightPanelMode('explorer');
    setFileExplorerVisible(true);
    setNavigateToPath(filePath);
  }, []);

  const handleNavigateComplete = useCallback(() => {
    setNavigateToPath(null);
  }, []);

  const toggleTool = (id: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleSubagent = (id: string) => {
    setExpandedSubagents((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleComments = (id: string) => {
    setExpandedComments((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleFiles = (id: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const expandAllSections = () => {
    const allToolIds = conversation.flatMap((item) =>
      item.type === 'agent_message' ? item.toolCalls.map((tool) => tool.id) : []
    );
    const allSubagentIds = conversation.flatMap((item) =>
      item.type === 'agent_message' ? item.subagents.map((s) => s.id) : []
    );
    const allOutputIds = outputItems.map((item) => item.id);
    const allAgentMessageIds = conversation
      .filter((item) => item.type === 'agent_message')
      .map((item) => item.id);
    setExpandedTools(new Set(allToolIds));
    setExpandedSubagents(new Set(allSubagentIds));
    setExpandedComments(new Set([...allOutputIds, ...allAgentMessageIds]));
    setExpandedFiles(new Set([...allAgentMessageIds, ...allOutputIds]));
  };

  const collapseAllSections = () => {
    setExpandedTools(new Set());
    setExpandedSubagents(new Set());
    setExpandedComments(new Set());
    setExpandedFiles(new Set());
  };

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      // ESC key handling - close overlays first, then cancel running session
      if (event.key === 'Escape') {
        // Skip if a modal inside file explorer is open (let modal handle ESC)
        if (fileExplorerModalOpen) {
          return;
        }
        // Close file explorer overlay first if open
        if (fileExplorerVisible) {
          event.preventDefault();
          setFileExplorerVisible(false);
          return;
        }
        // Then cancel running session if active
        if (isRunning) {
          handleCancel();
          return;
        }
      }
      // All Alt+ hotkeys use stopImmediatePropagation() to prevent special characters
      // from being inserted into focused input fields (macOS Option key produces characters)

      // Alt + [: Expand all sections
      if (event.code === 'BracketLeft' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        expandAllSections();
        return;
      }
      // Alt + ]: Collapse all sections
      if (event.code === 'BracketRight' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        collapseAllSections();
        return;
      }
      // Alt + N: New session (macOS Option+N produces ñ)
      if (event.code === 'KeyN' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        handleNewSession();
        return;
      }
      // Alt + E: Switch to File Explorer tab
      if (event.code === 'KeyE' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setRightPanelMode('explorer');
        if (!fileExplorerVisible) {
          setFileExplorerVisible(true);
          setFileExplorerRefreshKey((k) => k + 1);
        }
        return;
      }
      // Alt + D: Switch to Details tab
      if (event.code === 'KeyD' && event.altKey) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setRightPanelMode('details');
        return;
      }
    };
    window.addEventListener('keydown', handleKey, true); // capture phase to prevent input from receiving special chars
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [handleCancel, isRunning, expandAllSections, collapseAllSections, handleNewSession, fileExplorerVisible, fileExplorerModalOpen, setRightPanelMode]);

  return (
    <div className="terminal-app">
      <header className="terminal-header">
        <div className="header-top">
          <div className="header-title">
            <a href="/" className="header-logo-link">
              <img src="/agentum_logo.png" alt="Ag3ntum" className="header-logo" />
            </a>
            <span className="header-divider">│</span>
            <span className="header-meta">user: {user?.username || 'unknown'}</span>
            <button
              className="settings-button"
              type="button"
              onClick={() => navigate('/settings')}
              title="Settings"
            >
              ⚙
            </button>
            <button
              className="logout-button"
              type="button"
              onClick={logout}
              title="Sign out"
            >
              ⏻
            </button>
          </div>
        </div>
        <div className="header-stats">
          <span>Messages: <strong>{conversation.length}</strong></span>
          <span className="header-stats-separator">│</span>
          <span>Duration: <strong>{sessionDuration}</strong></span>
          <span className="header-stats-separator">│</span>
          <span>Tools: <strong>{totalToolCalls}</strong></span>
          <span className="header-stats-separator">│</span>
          <span className="header-status">
            <span className="status-complete">✓ {headerStats.complete}</span>
            <span className="status-partial">◐ {headerStats.partial}</span>
            <span className="status-failed">✗ {headerStats.failed}</span>
          </span>
        </div>
        <div className="header-filters">
          <div className="session-selector">
            <span className="filter-label">Sessions:</span>
            <span className="session-current-id">{sessionIdLabel}</span>
            <button className="session-new-button" type="button" onClick={handleNewSession} title="New session (Alt+N)">
              [ +New ]
            </button>
            <div className="dropdown session-dropdown">
              <span className="dropdown-value">[...select]</span>
              <span className="dropdown-icon">▾</span>
              <div className="dropdown-list">
                {sessionItems}
              </div>
            </div>
          </div>
          <div className="filter-actions">
            {/* Expand/Collapse All and File Explorer buttons moved to Details panel */}
          </div>
        </div>
      </header>

      <main ref={mainRef} className={`terminal-main ${isDraggingDivider ? 'resizing' : ''}`}>
        {/* Left column - conversation and input */}
        <div className="terminal-left">
          <div ref={outputRef} className="terminal-output">
            {conversation.length === 0 ? (
              <div className="terminal-empty">Enter a task below to begin.</div>
            ) : (
              <AgentMessageContext.Provider
                value={
                  currentSession && config && token
                    ? {
                        sessionId: currentSession.id,
                        baseUrl: config.api.base_url,
                        token,
                        onShowInExplorer: handleShowInExplorer,
                      }
                    : null
                }
              >
                {conversation.map((item, index) => {
                  const isSelected = selectedMessageId === item.id;
                  if (item.type === 'user') {
                    return (
                      <MessageBlock
                        key={item.id}
                        sender="USER"
                        time={item.time}
                        content={item.content}
                        rightPanelCollapsed={rightPanelCollapsed}
                        isMobile={isMobile}
                        isLarge={item.isLarge}
                        sizeDisplay={item.sizeDisplay}
                        processedText={item.processedText}
                      />
                    );
                  }
                  if (item.type === 'agent_message') {
                    const isLastAgentMessage = conversation
                      .slice(index + 1)
                      .every((i) => i.type !== 'agent_message');
                    // Border status: only the last agent message shows the colored left border.
                    // Intermediate messages keep the diamond icon (via messageStatus prop) but no border.
                    const borderStatus = isLastAgentMessage && status !== 'running'
                      ? (item.status ?? status)
                      : undefined;
                    const todoPayload = todosByAgentId.get(item.id);
                    const todos = todoPayload?.todos ?? null;
                    // Only show streaming on the last agent message when overall status is running
                    const showStreaming = item.isStreaming && isLastAgentMessage && status === 'running';
                    return (
                      <div
                        key={item.id}
                        className={`message-block-wrapper selectable ${isSelected ? 'selected' : ''}`}
                        onClick={() => {
                          setSelectedMessageId(item.id);
                          setRightPanelMode('details');
                          if (isMobile) setMobilePanelOpen(true);
                        }}
                      >
                        <AgentMessageBlock
                          id={item.id}
                          time={item.time}
                          content={item.content}
                          toolCalls={item.toolCalls}
                          subagents={item.subagents}
                          todos={todos ?? undefined}
                          toolExpanded={expandedTools}
                          onToggleTool={toggleTool}
                          subagentExpanded={expandedSubagents}
                          onToggleSubagent={toggleSubagent}
                          status={(todoPayload?.status ?? borderStatus) as ResultStatus | undefined}
                          messageStatus={item.messageStatus}
                          messageErrorMessage={item.messageErrorMessage}
                          requestStatus={item.requestStatus}
                          requestErrorMessage={item.requestErrorMessage}
                          comments={item.comments}
                          commentsExpanded={expandedComments.has(item.id)}
                          onToggleComments={() => toggleComments(item.id)}
                          files={item.files}
                          filesExpanded={expandedFiles.has(item.id)}
                          onToggleFiles={() => toggleFiles(item.id)}
                          isStreaming={showStreaming}
                          sessionRunning={isLastAgentMessage && status === 'running'}
                          rightPanelCollapsed={rightPanelCollapsed}
                          isMobile={isMobile}
                          mobileExpanded={mobileExpandedMessages.has(item.id)}
                          onToggleMobileExpand={() => toggleMobileMessageExpand(item.id)}
                          onSubmitAnswer={handleSubmitAnswer}
                        />
                      </div>
                    );
                  }
                  if (item.type === 'output') {
                    // Border status: only the last output block shows the colored left border
                    const isLastOutput = conversation
                      .slice(index + 1)
                      .every((i) => i.type !== 'output');
                    const outputBorderStatus = isLastOutput && status !== 'running'
                      ? item.status
                      : undefined;
                    return (
                      <div
                        key={item.id}
                        className={`message-block-wrapper selectable ${isSelected ? 'selected' : ''}`}
                        onClick={() => {
                          setSelectedMessageId(item.id);
                          setRightPanelMode('details');
                          if (isMobile) setMobilePanelOpen(true);
                        }}
                      >
                        <OutputBlock
                          id={item.id}
                          time={item.time}
                          output={item.output}
                          comments={item.comments}
                          commentsExpanded={expandedComments.has(item.id)}
                          onToggleComments={() => toggleComments(item.id)}
                          files={item.files}
                          filesExpanded={expandedFiles.has(item.id)}
                          onToggleFiles={() => toggleFiles(item.id)}
                          status={outputBorderStatus}
                          error={item.error}
                          onFileAction={handleFileAction}
                          onShowInExplorer={handleShowInExplorer}
                          rightPanelCollapsed={rightPanelCollapsed}
                          isMobile={isMobile}
                          mobileExpanded={mobileExpandedMessages.has(item.id)}
                          onToggleMobileExpand={() => toggleMobileMessageExpand(item.id)}
                        />
                      </div>
                    );
                  }
                  return null;
                })}
              </AgentMessageContext.Provider>
            )}
          </div>

          {/* Footer/Input area - inside left column */}
          <div className="terminal-footer">
        <div className="usage-bar-row">
          <FooterCopyButtons conversation={conversation} outputRef={outputRef} />
        </div>
        {loadedSkills.length > 0 && (
          <div className="usage-bar-row">
            <span className="usage-bar-label">Skills ({loadedSkills.length}):</span>
            {loadedSkills.map((skill) => (
              <SkillTag
                key={skill.id}
                id={skill.id}
                name={skill.name}
                description={skill.description}
                onClick={() => insertTextFnRef.current?.(`/${skill.id}`)}
              />
            ))}
          </div>
        )}
        <div className="usage-bar-row">
          <span className="usage-bar-label">Tools:</span>
          {Object.keys(toolStats).map((tool) => (
            <ToolTag key={tool} type={tool} count={toolStats[tool]} />
          ))}
          {totalSubagentCalls > 0 && (
            <>
              <span className="usage-bar-separator">|</span>
              <span className="usage-bar-label">Agents:</span>
              {Object.keys(subagentStats).map((name) => (
                <SubagentTag key={name} name={name} count={subagentStats[name]} />
              ))}
            </>
          )}
          <SystemEventsToggle
            count={systemEvents.length}
            deniedCount={systemEvents.filter(e => e.eventType === 'permission_denied').length}
            onClick={() => setSystemEventsExpanded(true)}
          />
        </div>
        {systemEventsExpanded && systemEvents.length > 0 && (
          <SystemEventsPanel
            events={systemEvents}
            onClose={() => setSystemEventsExpanded(false)}
          />
        )}
        <div className="input-wrapper">
          <div className="input-section">
            <InputField
              value={inputValue}
              onChange={setInputValue}
              onSubmit={handleSubmit}
              onCancel={handleCancel}
              isRunning={isRunning}
              attachedFiles={attachedFiles}
              onAttachFiles={handleAttachFiles}
              onRemoveFile={handleRemoveFile}
              model={selectedModel}
              onModelChange={handleModelChange}
              availableModels={availableModels}
              baseUrl={config?.api.base_url}
              token={token}
              dynamicMounts={dynamicMounts}
              onMountsChange={setDynamicMounts}
              registerInsertText={(fn) => { insertTextFnRef.current = fn; }}
            />
            <div className={`input-message ${error ? (reconnecting || connectionState === 'polling' ? 'warning' : 'error') : ''}`}>
              {error || '\u00A0'}
            </div>
          </div>
        </div>
        <StatusFooter
          isRunning={isRunning}
          isQueued={status === 'queued'}
          queuePosition={currentSession?.queue_position ?? null}
          isAutoResume={currentSession?.is_auto_resume ?? false}
          statusLabel={statusLabel}
          statusClass={statusClass}
          stats={stats}
          connectionState={!token ? 'disconnected' : connectionState}
          startTime={runningStartTime}
        />
          </div>
        </div>

        {/* Draggable divider */}
        {!isMobile && (
          <div
            className={`terminal-divider ${isDraggingDivider ? 'dragging' : ''}`}
            onMouseDown={handleDividerMouseDown}
          />
        )}

        {/* Right column - details panel or file explorer */}
        <div
          className={`terminal-right ${isMobile && mobilePanelOpen ? 'mobile-open' : ''}`}
          style={!isMobile ? { width: rightPanelWidth, flex: 'none' } : undefined}
        >
          <div className="right-panel-header">
            <button
              type="button"
              className={`right-panel-tab ${rightPanelMode === 'details' ? 'active' : ''}`}
              onClick={() => setRightPanelMode('details')}
              title="Details (Alt+D)"
            >
              Details
            </button>
            <button
              type="button"
              className={`right-panel-tab ${rightPanelMode === 'sessions' ? 'active' : ''}`}
              onClick={() => setRightPanelMode('sessions')}
              title="Sessions (Alt+S)"
            >
              Sessions
              {sessionBadgeCounts.total > 0 && (
                <span className="right-panel-tab-badge">
                  {sessionBadgeCounts.completed > 0 && (
                    <span className="badge-count completed">{sessionBadgeCounts.completed}</span>
                  )}
                  {sessionBadgeCounts.failed > 0 && (
                    <span className="badge-count failed">{sessionBadgeCounts.failed}</span>
                  )}
                  {sessionBadgeCounts.waiting > 0 && (
                    <span className="badge-count waiting">{sessionBadgeCounts.waiting}</span>
                  )}
                </span>
              )}
            </button>
            <button
              type="button"
              className={`right-panel-tab ${rightPanelMode === 'explorer' ? 'active' : ''}`}
              onClick={() => {
                setRightPanelMode('explorer');
                if (!fileExplorerVisible) {
                  setFileExplorerVisible(true);
                  setFileExplorerRefreshKey((k) => k + 1);
                }
              }}
              title="File Explorer (Alt+E)"
            >
              Files
            </button>
            {isMobile && (
              <button
                type="button"
                className="right-panel-tab right-panel-close"
                onClick={() => setMobilePanelOpen(false)}
              >
                ✕
              </button>
            )}
          </div>
          <div className="right-panel-content">
            {rightPanelMode === 'details' ? (
                <RightPanelDetails
                  message={selectedMessageForRightPanel}
                  toolExpanded={expandedTools}
                  onToggleTool={toggleTool}
                  subagentExpanded={expandedSubagents}
                  onToggleSubagent={toggleSubagent}
                  commentsExpanded={selectedMessageId ? expandedComments.has(selectedMessageId) : false}
                  onToggleComments={() => selectedMessageId && toggleComments(selectedMessageId)}
                  filesExpanded={selectedMessageId ? expandedFiles.has(selectedMessageId) : false}
                  onToggleFiles={() => selectedMessageId && toggleFiles(selectedMessageId)}
                  onFileAction={handleFileAction}
                  onShowInExplorer={handleShowInExplorer}
                  onExpandAll={expandAllSections}
                  onCollapseAll={collapseAllSections}
                />
              ) : rightPanelMode === 'sessions' ? (
                <SessionListTab
                  sessions={sessions}
                  currentSessionId={currentSession?.id ?? null}
                  onSelectSession={handleSelectSession}
                  badges={sessionBadges}
                  onClearBadge={clearSessionBadge}
                  onDeleteSession={handleDeleteSession}
                  currentRunStartTime={runningStartTime}
                />
              ) : currentSession && config && token ? (
              <>
                <div className="file-explorer-options">
                  <label className="file-explorer-hidden-toggle">
                    <input
                      type="checkbox"
                      checked={showHiddenFiles}
                      onChange={(e) => setShowHiddenFiles(e.target.checked)}
                    />
                    <span>Show hidden files</span>
                  </label>
                </div>
                <FileExplorer
                  key={fileExplorerRefreshKey}
                  sessionId={currentSession.id}
                  baseUrl={config.api.base_url}
                  token={token}
                  showHiddenFiles={showHiddenFiles}
                  onError={(err) => setError(err)}
                  onModalStateChange={setFileExplorerModalOpen}
                  navigateTo={navigateToPath}
                  onNavigateComplete={handleNavigateComplete}
                  refreshTrigger={fileExplorerRefreshTrigger}
                  onFileNameInsert={(filename) => {
                    insertTextFnRef.current?.(filename);
                  }}
                />
              </>
            ) : (
              <div className="right-panel-empty">
                No session selected
              </div>
            )}
          </div>
        </div>

        {/* File Viewer Modal */}
        {(viewerFile || viewerLoading) && (
          <FileViewerModal
            file={viewerFile}
            isLoading={viewerLoading}
            onClose={handleCloseViewer}
            onDownload={handleViewerDownload}
            imageUrl={viewerImageUrl}
          />
        )}

        {/* Security Alert Toast - Requires manual dismissal */}
        {securityAlert && (
          <div className="security-alert-toast" role="alert" aria-live="assertive">
            <div className="security-alert-header">
              <span className="security-alert-icon">⚠️</span>
              <h3 className="security-alert-title">Sensitive Data Detected</h3>
            </div>
            <p className="security-alert-message">{securityAlert.message}</p>
            <div className="security-alert-details">
              <div className="security-alert-detail-row">
                <span className="security-alert-detail-label">Files affected:</span>
                <span className="security-alert-detail-value">{securityAlert.filesWithSecrets}</span>
              </div>
              <div className="security-alert-detail-row">
                <span className="security-alert-detail-label">Items redacted:</span>
                <span className="security-alert-detail-value">{securityAlert.totalSecrets}</span>
              </div>
              {securityAlert.typeLabels.length > 0 && (
                <div className="security-alert-types">
                  {securityAlert.typeLabels.map((label, idx) => (
                    <span key={idx} className="security-alert-type-badge">{label}</span>
                  ))}
                </div>
              )}
            </div>
            <div className="security-alert-actions">
              <button
                type="button"
                className="security-alert-dismiss"
                onClick={() => setSecurityAlert(null)}
              >
                I Understand
              </button>
            </div>
          </div>
        )}

        {/* Session error popup (e.g., session not found) */}
        <Popup
          isOpen={sessionErrorPopup !== null}
          type="error"
          title="Session Not Found"
          message="The session you're looking for doesn't exist or has been deleted."
          details={sessionErrorPopup?.sessionId}
          onClose={handleSessionErrorClose}
          actions={[
            {
              label: 'Go to Home',
              onClick: handleSessionErrorClose,
              variant: 'primary',
            },
          ]}
        />
      </main>

      {/* Mobile panel backdrop */}
      <div
        className={`mobile-panel-backdrop ${isMobile && mobilePanelOpen ? 'visible' : ''}`}
        onClick={() => setMobilePanelOpen(false)}
      />

      {/* Mobile panel toggle button */}
      <button
        type="button"
        className="mobile-panel-toggle"
        onClick={() => setMobilePanelOpen(true)}
        title="Open details panel"
      >
        ☰
      </button>
    </div>
  );
}

export default function AppWithAuth({ initialSessionId }: AppProps = {}) {
  return (
    <ProtectedRoute>
      <App initialSessionId={initialSessionId} />
    </ProtectedRoute>
  );
}
