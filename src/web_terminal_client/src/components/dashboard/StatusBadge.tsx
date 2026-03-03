import React from 'react';

interface StatusBadgeProps {
  status: string;
  label?: string;
}

const STATUS_COLORS: Record<string, string> = {
  active: '#4ade80',
  suspended: '#f87171',
  delivered: '#4ade80',
  pending: '#facc15',
  failed: '#f87171',
  ok: '#4ade80',
  warning: '#facc15',
  exceeded: '#f87171',
};

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const color = STATUS_COLORS[status.toLowerCase()] || '#94a3b8';
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
