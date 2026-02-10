/**
 * Status footer component
 *
 * Displays session status, connection state, and metrics.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import { formatDuration } from '../../utils';
import { useElapsedTime } from '../../hooks';
import { StatusSpinner } from '../spinners';
import { QueueIndicator } from '../QueueIndicator';

export type ConnectionState = 'connected' | 'reconnecting' | 'polling' | 'degraded' | 'disconnected';

export interface StatusFooterStats {
  turns: number;
  tokensIn: number;
  tokensOut: number;
  cost: number;
  durationMs: number;
}

export interface StatusFooterProps {
  isRunning: boolean;
  isQueued: boolean;
  queuePosition: number | null;
  isAutoResume: boolean;
  statusLabel: string;
  statusClass: string;
  stats: StatusFooterStats;
  connectionState: ConnectionState;
  startTime: string | null;
}

export function StatusFooter({
  isRunning,
  isQueued,
  queuePosition,
  isAutoResume,
  statusLabel,
  statusClass,
  stats,
  connectionState,
  startTime,
}: StatusFooterProps): JSX.Element {
  const elapsedTime = useElapsedTime(startTime, isRunning);

  const connectionDisplay = {
    connected: { icon: '\u25CF', label: 'Connected', className: 'connected' },
    reconnecting: { icon: '\u25CF', label: 'Reconnecting...', className: 'reconnecting' },
    polling: { icon: '\u25CF', label: 'Connected (polling)', className: 'polling' },
    degraded: { icon: '\u25CF', label: 'Connection issues...', className: 'degraded' },
    disconnected: { icon: '\u25CF', label: 'Disconnected', className: 'disconnected' },
  }[connectionState];

  return (
    <div className="terminal-status">
      <div className="status-left">
        <span className={`status-connection ${connectionDisplay.className}`}>
          {connectionDisplay.icon} {connectionDisplay.label}
        </span>
        <span className="status-divider">{'\u2502'}</span>
        <span className={`status-state ${statusClass}`}>
          {isQueued ? (
            <QueueIndicator position={queuePosition ?? 0} isAutoResume={isAutoResume} />
          ) : isRunning ? (
            <>
              <StatusSpinner /> Running...{elapsedTime && ` (${elapsedTime})`}
            </>
          ) : (
            <>
              {statusLabel === 'Idle' && '\u25CF Idle'}
              {statusLabel === 'Cancelled' && '\u2717 Cancelled'}
              {statusLabel === 'Failed' && '\u2717 Failed'}
              {statusLabel !== 'Idle' && statusLabel !== 'Cancelled' && statusLabel !== 'Failed' && statusLabel}
            </>
          )}
        </span>
      </div>
      <div className="status-right">
        <span className="status-metric">Turns: <strong>{stats.turns}</strong></span>
        <span className="status-metric">Tokens: <strong>{stats.tokensIn}</strong> in / <strong>{stats.tokensOut}</strong> out</span>
        <span className="status-metric cost">${stats.cost.toFixed(4)}</span>
        <span className="status-metric">{formatDuration(stats.durationMs)}</span>
      </div>
    </div>
  );
}
