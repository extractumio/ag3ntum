import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardLayout } from '../../components/dashboard';
import { UserSettings } from './UserSettings';
import { SSHSettingsPage } from './SSHSettingsPage';

const NAV_ITEMS = [
  { to: '/settings', label: 'General' },
  { to: '/settings/ssh', label: 'SSH Connections' },
];

export default function SettingsRoutes() {
  return (
    <Routes>
      <Route element={<DashboardLayout title="Settings" navItems={NAV_ITEMS} />}>
        <Route index element={<UserSettings />} />
        <Route path="ssh" element={<SSHSettingsPage />} />
        <Route path="*" element={<Navigate to="/settings" replace />} />
      </Route>
    </Routes>
  );
}
