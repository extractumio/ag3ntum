/**
 * System events panel components
 *
 * Components for displaying system events like permission denials and profile switches.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import type { SystemEventView } from '../../types/conversation';

export interface SystemEventsToggleProps {
  count: number;
  deniedCount: number;
  onClick: () => void;
}

export function SystemEventsToggle({
  count,
  deniedCount,
  onClick
}: SystemEventsToggleProps): JSX.Element | null {
  if (count === 0) return null;

  return (
    <button
      className={`system-events-toggle-btn ${deniedCount > 0 ? 'has-warnings' : ''}`}
      onClick={onClick}
      title="Show system events"
    >
      <span className="system-events-toggle-icon">⚙</span>
      <span className="system-events-toggle-count">{count}</span>
      {deniedCount > 0 && (
        <span className="system-events-toggle-warning">🚫{deniedCount}</span>
      )}
    </button>
  );
}

export interface SystemEventsPanelProps {
  events: SystemEventView[];
  onClose: () => void;
}

export function SystemEventsPanel({
  events,
  onClose
}: SystemEventsPanelProps): JSX.Element | null {
  if (events.length === 0) return null;

  const permissionDenials = events.filter(e => e.eventType === 'permission_denied');

  return (
    <div className="system-events-panel">
      <div
        className="system-events-header"
        onClick={onClose}
        role="button"
      >
        <span className="system-events-toggle">▼</span>
        <span className="system-events-icon">⚙</span>
        <span className="system-events-title">System Events ({events.length})</span>
        {permissionDenials.length > 0 && (
          <span className="system-events-badge system-events-badge-warning">
            {permissionDenials.length} denied
          </span>
        )}
        <span className="system-events-close">✕</span>
      </div>
      <div className="system-events-list">
        {events.map(event => (
          <div key={event.id} className={`system-event system-event-${event.eventType}`}>
            <span className="system-event-time">{event.time}</span>
            {event.eventType === 'permission_denied' && (
              <>
                <span className="system-event-badge-denied">🚫 DENIED</span>
                <span className="system-event-tool">{event.toolName}</span>
                {event.message && (
                  <span className="system-event-message">— {event.message.slice(0, 100)}{event.message.length > 100 ? '...' : ''}</span>
                )}
              </>
            )}
            {event.eventType === 'profile_switch' && (
              <>
                <span className="system-event-badge-info">⚙ PROFILE</span>
                <span className="system-event-profile">{event.profileName}</span>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
