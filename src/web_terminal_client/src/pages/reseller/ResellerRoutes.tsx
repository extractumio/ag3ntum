import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardLayout } from '../../components/dashboard';
import { ResellerDashboard } from './ResellerDashboard';
import { UserList } from './UserList';
import { UserDetail } from './UserDetail';
import { ApiKeyManagement } from './ApiKeyManagement';

const NAV_ITEMS = [
  { to: '/reseller', label: 'Dashboard' },
  { to: '/reseller/users', label: 'Users' },
  { to: '/reseller/api-keys', label: 'API Keys' },
];

export default function ResellerRoutes() {
  return (
    <Routes>
      <Route element={<DashboardLayout title="Reseller" navItems={NAV_ITEMS} />}>
        <Route index element={<ResellerDashboard />} />
        <Route path="users" element={<UserList />} />
        <Route path="users/:userId" element={<UserDetail />} />
        <Route path="api-keys" element={<ApiKeyManagement />} />
        <Route path="*" element={<Navigate to="/reseller" replace />} />
      </Route>
    </Routes>
  );
}
