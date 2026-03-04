import React from 'react';

interface StatsCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  accent?: 'green' | 'yellow' | 'red' | 'blue' | 'default';
}

const ACCENT_COLORS: Record<string, string> = {
  green: 'var(--color-success, #4ade80)',
  yellow: 'var(--color-warning, #facc15)',
  red: 'var(--color-error, #f87171)',
  blue: 'var(--color-info, #60a5fa)',
  default: 'var(--color-primary, #22d3ee)',
};

export function StatsCard({ label, value, sublabel, accent = 'default' }: StatsCardProps) {
  return (
    <div className="dash-stats-card" style={{ borderLeftColor: ACCENT_COLORS[accent] }}>
      <div className="dash-stats-label">{label}</div>
      <div className="dash-stats-value">{value}</div>
      {sublabel && <div className="dash-stats-sub">{sublabel}</div>}
    </div>
  );
}
