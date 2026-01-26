/**
 * QueueIndicator component - displays queue status for queued sessions.
 */
import React from 'react';

interface QueueIndicatorProps {
  position: number;
  isAutoResume?: boolean;
}

/**
 * Displays a spinner and queue position when a task is queued.
 */
export function QueueIndicator({ position, isAutoResume = false }: QueueIndicatorProps) {
  return (
    <div className="queue-indicator">
      <span className="queue-spinner" />
      <span className="queue-text">
        {isAutoResume ? 'Resuming' : 'Queued'}: Position {position}
      </span>
    </div>
  );
}
