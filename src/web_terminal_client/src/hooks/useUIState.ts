/**
 * useUIState Hook
 *
 * Manages UI state including:
 * - Expanded/collapsed sections
 * - Panel visibility and sizing
 * - Mobile-specific state
 * - Selected message for details panel
 */

import { useCallback, useEffect, useState } from 'react';

// Local storage helpers
function getStoredPanelCollapsed(): boolean {
  try {
    const stored = localStorage.getItem('rightPanelCollapsed');
    return stored === 'true';
  } catch {
    return false;
  }
}

function setStoredPanelCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem('rightPanelCollapsed', String(collapsed));
  } catch {
    // Ignore storage errors
  }
}

function getStoredPanelWidth(): number {
  try {
    const stored = localStorage.getItem('rightPanelWidth');
    return stored ? parseInt(stored, 10) : 400;
  } catch {
    return 400;
  }
}

function setStoredPanelWidth(width: number): void {
  try {
    localStorage.setItem('rightPanelWidth', String(width));
  } catch {
    // Ignore storage errors
  }
}

export interface UseUIStateResult {
  // Expanded sections
  expandedTools: Set<string>;
  toggleToolExpanded: (id: string) => void;
  expandedSubagents: Set<string>;
  toggleSubagentExpanded: (id: string) => void;
  expandedComments: Set<string>;
  toggleCommentsExpanded: (id: string) => void;
  expandedFiles: Set<string>;
  toggleFilesExpanded: (id: string) => void;
  mobileExpandedMessages: Set<string>;
  toggleMobileMessageExpanded: (id: string) => void;
  systemEventsExpanded: boolean;
  setSystemEventsExpanded: React.Dispatch<React.SetStateAction<boolean>>;
  // Panel state
  rightPanelCollapsed: boolean;
  setRightPanelCollapsed: (collapsed: boolean) => void;
  rightPanelWidth: number;
  setRightPanelWidth: (width: number) => void;
  isDraggingDivider: boolean;
  setIsDraggingDivider: React.Dispatch<React.SetStateAction<boolean>>;
  rightPanelMode: 'details' | 'explorer';
  setRightPanelMode: React.Dispatch<React.SetStateAction<'details' | 'explorer'>>;
  mobilePanelOpen: boolean;
  setMobilePanelOpen: React.Dispatch<React.SetStateAction<boolean>>;
  // Selected message
  selectedMessageId: string | null;
  setSelectedMessageId: React.Dispatch<React.SetStateAction<string | null>>;
}

export function useUIState(): UseUIStateResult {
  // Expanded sections
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [expandedSubagents, setExpandedSubagents] = useState<Set<string>>(new Set());
  const [expandedComments, setExpandedComments] = useState<Set<string>>(new Set());
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [mobileExpandedMessages, setMobileExpandedMessages] = useState<Set<string>>(new Set());
  const [systemEventsExpanded, setSystemEventsExpanded] = useState(false);

  // Panel state
  const [rightPanelCollapsed, setRightPanelCollapsedState] = useState<boolean>(() =>
    getStoredPanelCollapsed()
  );
  const [rightPanelWidth, setRightPanelWidthState] = useState<number>(() =>
    getStoredPanelWidth()
  );
  const [isDraggingDivider, setIsDraggingDivider] = useState(false);
  const [rightPanelMode, setRightPanelMode] = useState<'details' | 'explorer'>('details');
  const [mobilePanelOpen, setMobilePanelOpen] = useState(false);

  // Selected message
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);

  // Toggle functions
  const toggleToolExpanded = useCallback((id: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const toggleSubagentExpanded = useCallback((id: string) => {
    setExpandedSubagents((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const toggleCommentsExpanded = useCallback((id: string) => {
    setExpandedComments((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const toggleFilesExpanded = useCallback((id: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const toggleMobileMessageExpanded = useCallback((id: string) => {
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

  // Persisted setters
  const setRightPanelCollapsed = useCallback((collapsed: boolean) => {
    setRightPanelCollapsedState(collapsed);
    setStoredPanelCollapsed(collapsed);
  }, []);

  const setRightPanelWidth = useCallback((width: number) => {
    setRightPanelWidthState(width);
    setStoredPanelWidth(width);
  }, []);

  return {
    // Expanded sections
    expandedTools,
    toggleToolExpanded,
    expandedSubagents,
    toggleSubagentExpanded,
    expandedComments,
    toggleCommentsExpanded,
    expandedFiles,
    toggleFilesExpanded,
    mobileExpandedMessages,
    toggleMobileMessageExpanded,
    systemEventsExpanded,
    setSystemEventsExpanded,
    // Panel state
    rightPanelCollapsed,
    setRightPanelCollapsed,
    rightPanelWidth,
    setRightPanelWidth,
    isDraggingDivider,
    setIsDraggingDivider,
    rightPanelMode,
    setRightPanelMode,
    mobilePanelOpen,
    setMobilePanelOpen,
    // Selected message
    selectedMessageId,
    setSelectedMessageId,
  };
}
