/**
 * Custom React hooks
 *
 * Extracted from App.tsx for better modularity.
 */

import { useEffect, useState } from 'react';
import { SPINNER_FRAMES } from '../constants';

// Re-export all hooks
export { useAppConfig, type UseAppConfigResult } from './useAppConfig';
export { useSessionManager, type UseSessionManagerResult, type SessionStats } from './useSessionManager';
export { useSSEConnection, type UseSSEConnectionResult, type ConnectionState } from './useSSEConnection';
export { useFileOperations, type UseFileOperationsResult, type AttachedFile } from './useFileOperations';
export { useUIState, type UseUIStateResult } from './useUIState';

/**
 * Hook for spinner animation frames
 * Returns the current frame index that cycles through SPINNER_FRAMES
 */
export function useSpinnerFrame(intervalMs: number = 80): number {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setFrame((prev) => (prev + 1) % SPINNER_FRAMES.length);
    }, intervalMs);
    return () => clearInterval(interval);
  }, [intervalMs]);

  return frame;
}

/**
 * Hook to display elapsed time since a start timestamp, updating every second
 * Returns a formatted string like "0s", "5m 30s", "1h 30m 45s"
 */
export function useElapsedTime(startTime: string | null, isRunning: boolean): string {
  const [elapsed, setElapsed] = useState('');

  useEffect(() => {
    if (!isRunning || !startTime) {
      setElapsed('');
      return;
    }

    const updateElapsed = () => {
      const start = new Date(startTime).getTime();
      const now = Date.now();
      const diffMs = now - start;

      if (diffMs < 0) {
        setElapsed('0s');
        return;
      }

      const seconds = Math.floor(diffMs / 1000);
      const minutes = Math.floor(seconds / 60);
      const hours = Math.floor(minutes / 60);

      if (hours > 0) {
        setElapsed(`${hours}h ${minutes % 60}m ${seconds % 60}s`);
      } else if (minutes > 0) {
        setElapsed(`${minutes}m ${seconds % 60}s`);
      } else {
        setElapsed(`${seconds}s`);
      }
    };

    // Update immediately
    updateElapsed();

    // Then update every second
    const interval = setInterval(updateElapsed, 1000);
    return () => clearInterval(interval);
  }, [startTime, isRunning]);

  return elapsed;
}

/**
 * Hook to detect mobile viewport
 * Returns true if window width is below breakpoint
 */
export function useIsMobile(breakpoint: number = 768): boolean {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth < breakpoint
  );

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < breakpoint);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [breakpoint]);

  return isMobile;
}
