import React from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../../AuthContext';

interface NavItem {
  to: string;
  label: string;
}

interface DashboardLayoutProps {
  title: string;
  navItems: NavItem[];
}

export function DashboardLayout({ title, navItems }: DashboardLayoutProps) {
  const { user, logout } = useAuth();

  return (
    <div className="dash-layout">
      <aside className="dash-sidebar">
        <div className="dash-sidebar-header">
          <h2 className="dash-sidebar-title">{title}</h2>
          <span className="dash-sidebar-user">{user?.username}</span>
        </div>
        <nav className="dash-sidebar-nav">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `dash-nav-link${isActive ? ' dash-nav-active' : ''}`
              }
              end={item.to.split('/').length <= 3}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="dash-sidebar-footer">
          <NavLink to="/" className="dash-nav-link">Terminal</NavLink>
          <button className="dash-btn dash-btn-sm" onClick={logout}>Logout</button>
        </div>
      </aside>
      <main className="dash-content">
        <Outlet />
      </main>
    </div>
  );
}
