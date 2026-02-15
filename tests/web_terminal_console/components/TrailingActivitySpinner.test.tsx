import { render } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

// Mock useSpinnerFrame to return a fixed frame index for deterministic tests
vi.mock('../../../src/web_terminal_client/src/hooks', () => ({
  useSpinnerFrame: () => 0,
}));

import { TrailingActivitySpinner } from '../../../src/web_terminal_client/src/components/spinners';
import { SPINNER_FRAMES } from '../../../src/web_terminal_client/src/constants';
import type { SubagentView } from '../../../src/web_terminal_client/src/types/conversation';

const SPINNER_CHAR = SPINNER_FRAMES[0]; // '⠋'

function makeSubagent(overrides: Partial<SubagentView> = {}): SubagentView {
  return {
    id: 'sub-1',
    taskId: 'task-1',
    name: 'Explore',
    time: '12:00:00',
    status: 'running',
    ...overrides,
  };
}

describe('TrailingActivitySpinner', () => {
  describe('fallback (no activity)', () => {
    it('renders bare spinner when no toolName and no subagents', () => {
      const { container } = render(
        <TrailingActivitySpinner toolName={null} subagents={[]} />
      );
      const spinner = container.querySelector('.trailing-wait-spinner');
      expect(spinner).not.toBeNull();
      expect(spinner!.textContent).toBe(SPINNER_CHAR);
      // Should NOT have activity wrapper
      expect(container.querySelector('.trailing-activity-spinner')).toBeNull();
    });

    it('renders bare spinner when toolName is undefined', () => {
      const { container } = render(
        <TrailingActivitySpinner subagents={[]} />
      );
      const spinner = container.querySelector('.trailing-wait-spinner');
      expect(spinner).not.toBeNull();
      expect(spinner!.textContent).toBe(SPINNER_CHAR);
    });
  });

  describe('tool name only (no subagents)', () => {
    it('renders tool name with spinner', () => {
      const { container } = render(
        <TrailingActivitySpinner toolName="Bash" subagents={[]} />
      );
      const wrapper = container.querySelector('.trailing-activity-spinner');
      expect(wrapper).not.toBeNull();

      const line = container.querySelector('.trailing-activity-line');
      expect(line).not.toBeNull();

      const name = container.querySelector('.trailing-activity-name');
      expect(name!.textContent).toBe('Bash');

      const char = container.querySelector('.trailing-activity-char');
      expect(char!.textContent).toBe(SPINNER_CHAR);
    });

    it('does not render subagent dots when only tool is present', () => {
      const { container } = render(
        <TrailingActivitySpinner toolName="Edit" subagents={[]} />
      );
      expect(container.querySelector('.subagent-dot')).toBeNull();
    });
  });

  describe('running subagents', () => {
    it('renders running subagent with spinner', () => {
      const sub = makeSubagent({ name: 'Explore', status: 'running' });
      const { container } = render(
        <TrailingActivitySpinner toolName={null} subagents={[sub]} />
      );
      const wrapper = container.querySelector('.trailing-activity-spinner');
      expect(wrapper).not.toBeNull();

      const lines = container.querySelectorAll('.trailing-activity-line');
      expect(lines).toHaveLength(1);

      const dot = lines[0].querySelector('.subagent-dot-running');
      expect(dot).not.toBeNull();
      expect(dot!.textContent).toBe('◆');

      const name = lines[0].querySelector('.trailing-activity-name');
      expect(name!.textContent).toBe('Explore');

      const char = lines[0].querySelector('.trailing-activity-char');
      expect(char!.textContent).toBe(SPINNER_CHAR);
    });

    it('hides tool name line when running subagents exist', () => {
      const sub = makeSubagent({ status: 'running' });
      const { container } = render(
        <TrailingActivitySpinner toolName="Bash" subagents={[sub]} />
      );
      // The tool name line should NOT appear because runningSubagents.length > 0
      const lines = container.querySelectorAll('.trailing-activity-line');
      expect(lines).toHaveLength(1);
      // The line should be the subagent, not the tool
      expect(lines[0].querySelector('.subagent-dot')).not.toBeNull();
    });

    it('renders multiple running subagents', () => {
      const subs = [
        makeSubagent({ id: 'sub-1', name: 'Explore', status: 'running' }),
        makeSubagent({ id: 'sub-2', name: 'Plan', status: 'running' }),
      ];
      const { container } = render(
        <TrailingActivitySpinner toolName={null} subagents={subs} />
      );
      const lines = container.querySelectorAll('.trailing-activity-line');
      expect(lines).toHaveLength(2);

      const names = container.querySelectorAll('.trailing-activity-name');
      expect(names[0].textContent).toBe('Explore');
      expect(names[1].textContent).toBe('Plan');
    });
  });

  describe('completed/failed subagents', () => {
    it('renders completed subagent with checkmark', () => {
      const sub = makeSubagent({ name: 'Plan', status: 'complete' });
      const { container } = render(
        <TrailingActivitySpinner toolName={null} subagents={[sub]} />
      );
      const line = container.querySelector('.trailing-activity-done');
      expect(line).not.toBeNull();

      const dot = line!.querySelector('.subagent-dot-complete');
      expect(dot).not.toBeNull();

      const check = line!.querySelector('.trailing-activity-check');
      expect(check!.textContent).toBe('✓');
    });

    it('renders failed subagent with cross mark', () => {
      const sub = makeSubagent({ name: 'Explore', status: 'failed' });
      const { container } = render(
        <TrailingActivitySpinner toolName={null} subagents={[sub]} />
      );
      const line = container.querySelector('.trailing-activity-done');
      expect(line).not.toBeNull();

      const dot = line!.querySelector('.subagent-dot-failed');
      expect(dot).not.toBeNull();

      const check = line!.querySelector('.trailing-activity-check');
      expect(check!.textContent).toBe('✗');
    });
  });

  describe('mixed subagent states', () => {
    it('renders running subagents before completed ones', () => {
      const subs = [
        makeSubagent({ id: 'sub-1', name: 'Explore', status: 'running' }),
        makeSubagent({ id: 'sub-2', name: 'Plan', status: 'complete' }),
        makeSubagent({ id: 'sub-3', name: 'Research', status: 'failed' }),
      ];
      const { container } = render(
        <TrailingActivitySpinner toolName={null} subagents={subs} />
      );
      const lines = container.querySelectorAll('.trailing-activity-line');
      expect(lines).toHaveLength(3);

      // First: running (has spinner char)
      expect(lines[0].querySelector('.subagent-dot-running')).not.toBeNull();
      expect(lines[0].querySelector('.trailing-activity-char')!.textContent).toBe(SPINNER_CHAR);

      // Second: complete (has check)
      expect(lines[1].querySelector('.subagent-dot-complete')).not.toBeNull();
      expect(lines[1].querySelector('.trailing-activity-check')!.textContent).toBe('✓');

      // Third: failed (has cross)
      expect(lines[2].querySelector('.subagent-dot-failed')).not.toBeNull();
      expect(lines[2].querySelector('.trailing-activity-check')!.textContent).toBe('✗');
    });

    it('shows tool name when only completed subagents exist (no running)', () => {
      const subs = [
        makeSubagent({ id: 'sub-1', name: 'Plan', status: 'complete' }),
      ];
      const { container } = render(
        <TrailingActivitySpinner toolName="Bash" subagents={subs} />
      );
      // Tool line should appear because runningSubagents.length === 0
      const names = container.querySelectorAll('.trailing-activity-name');
      expect(names).toHaveLength(2);
      expect(names[0].textContent).toBe('Bash');   // tool line
      expect(names[1].textContent).toBe('Plan');    // done subagent
    });
  });
});
