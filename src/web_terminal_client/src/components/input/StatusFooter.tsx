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
  statusLabel: string;
  statusClass: string;
  stats: StatusFooterStats;
  connectionState: ConnectionState;
  startTime: string | null;
}

export function StatusFooter({
  isRunning,
  statusLabel,
  statusClass,
  stats,
  connectionState,
  startTime,
}: StatusFooterProps): JSX.Element {
  const elapsedTime = useElapsedTime(startTime, isRunning);

  const connectionDisplay = {
    connected: { icon: '●', label: 'Connected', className: 'connected' },
    reconnecting: { icon: '●', label: 'Reconnecting...', className: 'reconnecting' },
    polling: { icon: '●', label: 'Connected (polling)', className: 'polling' },
    degraded: { icon: '●', label: 'Connection issues...', className: 'degraded' },
    disconnected: { icon: '●', label: 'Disconnected', className: 'disconnected' },
  }[connectionState];

  return (
    <div className="terminal-status">
      <div className="status-left">
        <span className={`status-connection ${connectionDisplay.className}`}>
          {connectionDisplay.icon} {connectionDisplay.label}
        </span>
        <span className="status-divider">│</span>
        <span className={`status-state ${statusClass}`}>
          {isRunning ? (
            <>
              <StatusSpinner /> Running...{elapsedTime && ` (${elapsedTime})`}
            </>
          ) : (
            <>
              {statusLabel === 'Idle' && '● Idle'}
              {statusLabel === 'Cancelled' && '✗ Cancelled'}
              {statusLabel === 'Failed' && '✗ Failed'}
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
