/**
 * useSessionManager Hook
 *
 * Manages session state including:
 * - Session list loading and caching
 * - Current session selection
 * - Session status updates
 * - Session refresh operations
 */

import { useCallback, useEffect, useState } from 'react';
import {
  getSession,
  getSessionEvents,
  invalidateSessionsCache,
  listSessionsCached,
} from '../api';
import type { AppConfig, SessionListResponse, SessionResponse, TerminalEvent } from '../types';
import { isValidSessionId, seedSessionEvents } from '../utils';

export interface SessionStats {
  turns: number;
  cost: number;
  durationMs: number;
  tokensIn: number;
  tokensOut: number;
  model: string;
}

export interface UseSessionManagerResult {
  sessions: SessionResponse[];
  currentSession: SessionResponse | null;
  setCurrentSession: React.Dispatch<React.SetStateAction<SessionResponse | null>>;
  setSessions: React.Dispatch<React.SetStateAction<SessionResponse[]>>;
  stats: SessionStats;
  setStats: React.Dispatch<React.SetStateAction<SessionStats>>;
  refreshSessions: () => void;
  selectSession: (sessionId: string) => Promise<TerminalEvent[]>;
  clearSession: () => void;
  isLoadingSession: boolean;
}

const INITIAL_STATS: SessionStats = {
  turns: 0,
  cost: 0,
  durationMs: 0,
  tokensIn: 0,
  tokensOut: 0,
  model: '',
};

export function useSessionManager(
  config: AppConfig | null,
  token: string | null,
  initialSessionId?: string
): UseSessionManagerResult {
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionResponse | null>(null);
  const [stats, setStats] = useState<SessionStats>(INITIAL_STATS);
  const [isLoadingSession, setIsLoadingSession] = useState(false);

  // Refresh sessions list
  const refreshSessions = useCallback(() => {
    if (!config || !token) {
      return;
    }

    listSessionsCached(config.api.base_url, token)
      .then((response: SessionListResponse) => setSessions(response.sessions))
      .catch((err: Error) => {
        console.error('Failed to load sessions:', err);
      });
  }, [config, token]);

  // Load sessions on mount
  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  // Select a session and load its events
  const selectSession = useCallback(
    async (sessionId: string): Promise<TerminalEvent[]> => {
      if (!config || !token || !isValidSessionId(sessionId)) {
        return [];
      }

      setIsLoadingSession(true);

      try {
        // Fetch session details and events in parallel
        const [sessionData, eventsResponse] = await Promise.all([
          getSession(config.api.base_url, token, sessionId),
          getSessionEvents(config.api.base_url, token, sessionId),
        ]);

        setCurrentSession(sessionData);

        // Update stats from session data
        setStats({
          turns: sessionData.num_turns,
          cost: sessionData.total_cost_usd ?? 0,
          durationMs: sessionData.duration_ms ?? 0,
          tokensIn: 0,
          tokensOut: 0,
          model: sessionData.model ?? '',
        });

        // Seed events with initial user message if needed
        const seededEvents = seedSessionEvents(sessionData, eventsResponse.events);
        return seededEvents;
      } catch (err) {
        console.error('Failed to load session:', err);
        return [];
      } finally {
        setIsLoadingSession(false);
      }
    },
    [config, token]
  );

  // Clear current session
  const clearSession = useCallback(() => {
    setCurrentSession(null);
    setStats(INITIAL_STATS);
  }, []);

  // Load session from URL on mount
  useEffect(() => {
    if (isValidSessionId(initialSessionId) && config && token && !currentSession) {
      selectSession(initialSessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSessionId, config, token]);

  return {
    sessions,
    currentSession,
    setCurrentSession,
    setSessions,
    stats,
    setStats,
    refreshSessions,
    selectSession,
    clearSession,
    isLoadingSession,
  };
}
