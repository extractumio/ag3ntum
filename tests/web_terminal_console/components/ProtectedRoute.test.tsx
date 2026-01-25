/**
 * Tests for ProtectedRoute component
 *
 * Tests the route protection functionality including:
 * - Showing children when authenticated
 * - Showing login page when not authenticated
 * - Showing loading state while checking auth
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ProtectedRoute } from '../../../src/web_terminal_client/src/ProtectedRoute';
import * as AuthContextModule from '../../../src/web_terminal_client/src/AuthContext';

// Mock the useAuth hook
vi.mock('../../../src/web_terminal_client/src/AuthContext', async () => {
  const actual = await vi.importActual('../../../src/web_terminal_client/src/AuthContext');
  return {
    ...actual,
    useAuth: vi.fn(),
  };
});

// Mock the LoginPage component
vi.mock('../../../src/web_terminal_client/src/LoginPage', () => ({
  LoginPage: () => <div data-testid="login-page">Login Page</div>,
}));

describe('ProtectedRoute', () => {
  const mockUseAuth = vi.mocked(AuthContextModule.useAuth);

  // ==========================================================================
  // Authenticated State
  // ==========================================================================
  describe('Authenticated State', () => {
    it('renders children when authenticated', () => {
      mockUseAuth.mockReturnValue({
        isAuthenticated: true,
        isLoading: false,
        user: { id: 'user-1', username: 'testuser', email: 'test@example.com', role: 'user', created_at: '2024-01-01' },
        token: 'mock-token',
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      render(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      expect(screen.getByTestId('protected-content')).toBeInTheDocument();
      expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    });

    it('renders multiple children when authenticated', () => {
      mockUseAuth.mockReturnValue({
        isAuthenticated: true,
        isLoading: false,
        user: { id: 'user-1', username: 'testuser', email: 'test@example.com', role: 'user', created_at: '2024-01-01' },
        token: 'mock-token',
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      render(
        <ProtectedRoute>
          <div data-testid="child-1">Child 1</div>
          <div data-testid="child-2">Child 2</div>
        </ProtectedRoute>
      );

      expect(screen.getByTestId('child-1')).toBeInTheDocument();
      expect(screen.getByTestId('child-2')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Unauthenticated State
  // ==========================================================================
  describe('Unauthenticated State', () => {
    it('renders LoginPage when not authenticated', () => {
      mockUseAuth.mockReturnValue({
        isAuthenticated: false,
        isLoading: false,
        user: null,
        token: null,
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      render(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      expect(screen.getByTestId('login-page')).toBeInTheDocument();
      expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument();
    });

    it('does not render children when not authenticated', () => {
      mockUseAuth.mockReturnValue({
        isAuthenticated: false,
        isLoading: false,
        user: null,
        token: null,
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      render(
        <ProtectedRoute>
          <div data-testid="secret-data">Secret Data</div>
        </ProtectedRoute>
      );

      expect(screen.queryByTestId('secret-data')).not.toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Loading State
  // ==========================================================================
  describe('Loading State', () => {
    it('shows loading screen while checking authentication', () => {
      mockUseAuth.mockReturnValue({
        isAuthenticated: false,
        isLoading: true,
        user: null,
        token: null,
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      render(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      expect(screen.getByText('Loading...')).toBeInTheDocument();
      expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument();
      expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    });

    it('shows loading spinner element', () => {
      mockUseAuth.mockReturnValue({
        isAuthenticated: false,
        isLoading: true,
        user: null,
        token: null,
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      const { container } = render(
        <ProtectedRoute>
          <div>Content</div>
        </ProtectedRoute>
      );

      expect(container.querySelector('.loading-screen')).toBeInTheDocument();
      expect(container.querySelector('.loading-spinner')).toBeInTheDocument();
    });

    it('transitions from loading to authenticated', () => {
      // Start loading
      mockUseAuth.mockReturnValue({
        isAuthenticated: false,
        isLoading: true,
        user: null,
        token: null,
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      const { rerender } = render(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      expect(screen.getByText('Loading...')).toBeInTheDocument();

      // Finish loading, now authenticated
      mockUseAuth.mockReturnValue({
        isAuthenticated: true,
        isLoading: false,
        user: { id: 'user-1', username: 'testuser', email: 'test@example.com', role: 'user', created_at: '2024-01-01' },
        token: 'mock-token',
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      rerender(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
      expect(screen.getByTestId('protected-content')).toBeInTheDocument();
    });

    it('transitions from loading to unauthenticated', () => {
      // Start loading
      mockUseAuth.mockReturnValue({
        isAuthenticated: false,
        isLoading: true,
        user: null,
        token: null,
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      const { rerender } = render(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      expect(screen.getByText('Loading...')).toBeInTheDocument();

      // Finish loading, not authenticated
      mockUseAuth.mockReturnValue({
        isAuthenticated: false,
        isLoading: false,
        user: null,
        token: null,
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      rerender(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
      expect(screen.getByTestId('login-page')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Edge Cases
  // ==========================================================================
  describe('Edge Cases', () => {
    it('handles isAuthenticated true but isLoading also true', () => {
      // This edge case shouldn't normally happen, but test the priority
      mockUseAuth.mockReturnValue({
        isAuthenticated: true,
        isLoading: true,
        user: { id: 'user-1', username: 'testuser', email: 'test@example.com', role: 'user', created_at: '2024-01-01' },
        token: 'mock-token',
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      render(
        <ProtectedRoute>
          <div data-testid="protected-content">Protected Content</div>
        </ProtectedRoute>
      );

      // Loading takes priority
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });

    it('renders empty fragment when children is undefined', () => {
      mockUseAuth.mockReturnValue({
        isAuthenticated: true,
        isLoading: false,
        user: { id: 'user-1', username: 'testuser', email: 'test@example.com', role: 'user', created_at: '2024-01-01' },
        token: 'mock-token',
        login: vi.fn(),
        logout: vi.fn(),
        error: null,
      });

      const { container } = render(
        <ProtectedRoute>
          {undefined}
        </ProtectedRoute>
      );

      // Should render without error
      expect(container).toBeInTheDocument();
    });
  });
});
