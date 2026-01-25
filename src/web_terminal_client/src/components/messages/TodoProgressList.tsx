/**
 * Todo progress list component
 *
 * Displays todo items with progress indicators.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import type { ResultStatus, TodoItem } from '../../types/conversation';
import { PulsingCircleSpinner } from '../spinners';

export interface TodoProgressListProps {
  todos: TodoItem[];
  overallStatus: ResultStatus | undefined;
}

export function TodoProgressList({
  todos,
  overallStatus,
}: TodoProgressListProps): JSX.Element {
  const isRunning = overallStatus === 'running' || !overallStatus;
  const isCancelled = overallStatus === 'cancelled';
  const isFailed = overallStatus === 'failed';
  const isDone = !isRunning;

  return (
    <div className={`todo-progress${isDone ? ' todo-progress-done' : ''}`}>
      {todos.map((todo, index) => {
        const status = todo.status?.toLowerCase?.() ?? 'pending';
        const isActive = status === 'in_progress' && isRunning;
        const isCompleted = isDone || status === 'completed';
        const label = isActive && todo.activeForm ? todo.activeForm : todo.content;
        const showCancel = (isCancelled || isFailed) && status === 'in_progress';
        const bullet = showCancel
          ? '✗'
          : isCompleted
            ? '✓'
            : '•';

        return (
          <div
            key={`${todo.content}-${index}`}
            className={`todo-item todo-${status}${showCancel ? ' todo-cancelled' : ''}`}
          >
            {isActive ? (
              <PulsingCircleSpinner />
            ) : (
              <span className="todo-bullet">
                {bullet}
              </span>
            )}
            <span
              className={`todo-text${isActive ? ' todo-active' : ''}${isCompleted ? ' todo-completed' : ''}`}
            >
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
