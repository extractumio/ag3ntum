import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardLayout } from '../../components/dashboard';
import { AdminDashboard } from './AdminDashboard';
import { ResellerList } from './ResellerList';
import { ResellerDetail } from './ResellerDetail';

const NAV_ITEMS = [
  { to: '/admin', label: 'Dashboard' },
  { to: '/admin/resellers', label: 'Resellers' },
];

export default function AdminRoutes() {
  return (
    <Routes>
      <Route element={<DashboardLayout title="Admin" navItems={NAV_ITEMS} />}>
        <Route index element={<AdminDashboard />} />
        <Route path="resellers" element={<ResellerList />} />
        <Route path="resellers/:id" element={<ResellerDetail />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Route>
    </Routes>
  );
}
