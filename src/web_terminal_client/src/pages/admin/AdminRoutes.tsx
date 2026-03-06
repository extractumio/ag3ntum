import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardLayout } from '../../components/dashboard';
import { AdminDashboard } from './AdminDashboard';
import { ResellerList } from './ResellerList';
import { ResellerDetail } from './ResellerDetail';
import { AdminUserList } from './AdminUserList';
import { AdminUserDetail } from './AdminUserDetail';
import { PlatformConfig } from './PlatformConfig';
import { AdminUsage } from './AdminUsage';
import { AuditLog } from './AuditLog';

const NAV_ITEMS = [
  { to: '/admin', label: 'Dashboard' },
  { to: '/admin/resellers', label: 'Resellers' },
  { to: '/admin/users', label: 'Users' },
  { to: '/admin/usage', label: 'Usage' },
  { to: '/admin/audit', label: 'Audit Log' },
  { to: '/admin/config', label: 'Config' },
];

export default function AdminRoutes() {
  return (
    <Routes>
      <Route element={<DashboardLayout title="Admin" navItems={NAV_ITEMS} />}>
        <Route index element={<AdminDashboard />} />
        <Route path="resellers" element={<ResellerList />} />
        <Route path="resellers/:id" element={<ResellerDetail />} />
        <Route path="users" element={<AdminUserList />} />
        <Route path="users/:userId" element={<AdminUserDetail />} />
        <Route path="config" element={<PlatformConfig />} />
        <Route path="usage" element={<AdminUsage />} />
        <Route path="audit" element={<AuditLog />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Route>
    </Routes>
  );
}
