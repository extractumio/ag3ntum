/**
 * Spinner components
 *
 * Loading/progress indicator components extracted from App.tsx.
 */

import { SPINNER_FRAMES } from '../../constants';
import { useSpinnerFrame } from '../../hooks';
import type { SubagentView } from '../../types/conversation';

export function AgentSpinner({ toolName }: { toolName?: string | null }): JSX.Element {
  const frame = useSpinnerFrame();

  return (
    <span className="agent-spinner">
      <span className="agent-spinner-char">{SPINNER_FRAMES[frame]}</span>
      <span className="agent-spinner-label">{toolName || 'processing...'}</span>
    </span>
  );
}

export function InlineStreamSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="inline-stream-spinner">{SPINNER_FRAMES[frame]}</span>;
}

export function StatusSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="status-spinner">{SPINNER_FRAMES[frame]}</span>;
}

export function TrailingActivitySpinner({
  toolName,
  subagents,
}: {
  toolName?: string | null;
  subagents: SubagentView[];
}): JSX.Element {
  const frame = useSpinnerFrame();
  const spinner = SPINNER_FRAMES[frame];
  const runningSubagents = subagents.filter(s => s.status === 'running');
  const doneSubagents = subagents.filter(s => s.status !== 'running');
  const hasActivity = toolName || subagents.length > 0;

  if (!hasActivity) {
    // Fallback: bare spinner (current behavior)
    return <span className="trailing-wait-spinner">{spinner}</span>;
  }

  return (
    <div className="trailing-activity-spinner">
      {toolName && !runningSubagents.length && (
        <div className="trailing-activity-line">
          <span className="trailing-activity-name">{toolName}</span>
          <span className="trailing-activity-char">{spinner}</span>
        </div>
      )}
      {runningSubagents.map(sub => (
        <div key={sub.id} className="trailing-activity-line">
          <span className="subagent-dot subagent-dot-running">◆</span>
          <span className="trailing-activity-name">{sub.name}</span>
          <span className="trailing-activity-char">{spinner}</span>
        </div>
      ))}
      {doneSubagents.map(sub => (
        <div key={sub.id} className="trailing-activity-line trailing-activity-done">
          <span className={`subagent-dot subagent-dot-${sub.status}`}>◆</span>
          <span className="trailing-activity-name">{sub.name}</span>
          <span className="trailing-activity-check">{sub.status === 'complete' ? '✓' : '✗'}</span>
        </div>
      ))}
    </div>
  );
}

// Pulsing filled circle spinner for structured elements (tools, skills, subagents)
export function PulsingCircleSpinner(): JSX.Element {
  return <span className="pulsing-circle-spinner">●</span>;
}
