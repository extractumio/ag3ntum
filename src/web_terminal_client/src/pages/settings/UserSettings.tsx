import { useState } from 'react';
import { TabbedDetail } from '../../components/dashboard';
import type { Tab } from '../../components/dashboard';
import { SSHProfileList } from './SSHProfileList';

const SETTINGS_TABS: Tab[] = [
  { id: 'ssh', label: 'SSH Connections' },
];

export function UserSettings() {
  const [activeTab, setActiveTab] = useState('ssh');

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Settings</h2>
      </div>
      <TabbedDetail tabs={SETTINGS_TABS} activeTab={activeTab} onTabChange={setActiveTab}>
        {activeTab === 'ssh' && <SSHProfileList />}
      </TabbedDetail>
    </div>
  );
}
