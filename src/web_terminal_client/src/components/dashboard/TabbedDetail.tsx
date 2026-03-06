import React from 'react';

export interface Tab {
  id: string;
  label: string;
  badge?: number | string;
}

interface TabbedDetailProps {
  tabs: Tab[];
  activeTab: string;
  onTabChange: (id: string) => void;
  children: React.ReactNode;
}

export function TabbedDetail({ tabs, activeTab, onTabChange, children }: TabbedDetailProps) {
  return (
    <div>
      <div className="dash-tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`dash-tab${activeTab === tab.id ? ' dash-tab-active' : ''}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
            {tab.badge !== undefined && (
              <span className="dash-tab-badge">{tab.badge}</span>
            )}
          </button>
        ))}
      </div>
      <div className="dash-tab-content">{children}</div>
    </div>
  );
}
