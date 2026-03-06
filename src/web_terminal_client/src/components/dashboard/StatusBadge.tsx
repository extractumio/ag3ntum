import React from 'react';

interface StatusBadgeProps {
  status: string;
  label?: string;
}

const STATUS_COLORS: Record<string, string> = {
  active: 'var(--color-success)',
  suspended: 'var(--color-error)',
  delivered: 'var(--color-success)',
  pending: 'var(--color-warning)',
  failed: 'var(--color-error)',
  ok: 'var(--color-success)',
  warning: 'var(--color-warning)',
  exceeded: 'var(--color-error)',
};

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const color = STATUS_COLORS[status.toLowerCase()] || 'var(--color-text-muted)';
  const text = label || status;

  return (
    <span
      className="dash-status-badge"
      style={{ color, borderColor: color }}
    >
      {text}
    </span>
  );
}
