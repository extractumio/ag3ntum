import React from 'react';

interface StatsCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  accent?: 'green' | 'yellow' | 'red' | 'blue' | 'default';
}

type Accent = NonNullable<StatsCardProps['accent']>;

const ACCENT_COLORS: Record<Accent, string> = {
  green: 'var(--color-success)',
  yellow: 'var(--color-warning)',
  red: 'var(--color-error)',
  blue: 'var(--color-accent-secondary)',
  default: 'var(--color-accent-primary)',
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
