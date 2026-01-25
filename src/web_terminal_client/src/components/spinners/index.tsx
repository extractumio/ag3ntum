/**
 * Spinner components
 *
 * Loading/progress indicator components extracted from App.tsx.
 */

import React from 'react';
import { SPINNER_FRAMES } from '../../constants';
import { useSpinnerFrame } from '../../hooks';

export function AgentSpinner(): JSX.Element {
  const frame = useSpinnerFrame();

  return (
    <span className="agent-spinner">
      <span className="agent-spinner-char">{SPINNER_FRAMES[frame]}</span>
      <span className="agent-spinner-label">processing...</span>
    </span>
  );
}

export function InlineStreamSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="inline-stream-spinner">{SPINNER_FRAMES[frame]}</span>;
}

export function TrailingWaitSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="trailing-wait-spinner">{SPINNER_FRAMES[frame]}</span>;
}

export function StatusSpinner(): JSX.Element {
  const frame = useSpinnerFrame();
  return <span className="status-spinner">{SPINNER_FRAMES[frame]}</span>;
}

// Pulsing filled circle spinner for structured elements (tools, skills, subagents)
export function PulsingCircleSpinner(): JSX.Element {
  return <span className="pulsing-circle-spinner">●</span>;
}
