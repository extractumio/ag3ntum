import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LoginPage } from '../../../src/web_terminal_client/src/LoginPage';

// Mock the AuthContext
const mockLogin = vi.fn();
const mockAuthContext = {
  user: null,
  token: null,
  isAuthenticated: false,
  isLoading: false,
  login: mockLogin,
  logout: vi.fn(),
  error: null as string | null,
};

vi.mock('../../../src/web_terminal_client/src/AuthContext', () => ({
  useAuth: () => mockAuthContext,
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function renderLoginPage() {
  return render(
    <BrowserRouter>
      <LoginPage />
    </BrowserRouter>
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuthContext.isLoading = false;
    mockAuthContext.error = null;
  });

  describe('rendering', () => {
    it('renders login form', () => {
      renderLoginPage();

      expect(screen.getByRole('heading', { name: /ag3ntum/i })).toBeInTheDocument();
      expect(screen.getByText(/sign in to continue/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
    });

    it('renders email input with correct attributes', () => {
      renderLoginPage();

      const emailInput = screen.getByLabelText(/email/i);
      expect(emailInput).toHaveAttribute('type', 'text');
      expect(emailInput).toHaveAttribute('required');
    });

    it('renders password input with correct attributes', () => {
      renderLoginPage();

      const passwordInput = screen.getByLabelText(/password/i);
      expect(passwordInput).toHaveAttribute('type', 'password');
      expect(passwordInput).toHaveAttribute('required');
    });

    it('renders footer text', () => {
      renderLoginPage();

      expect(screen.getByText(/contact your administrator/i)).toBeInTheDocument();
    });
  });

  describe('user interaction', () => {
    it('allows entering email', async () => {
      const user = userEvent.setup();
      renderLoginPage();

      const emailInput = screen.getByLabelText(/email/i);
      await user.type(emailInput, 'test@example.com');

      expect(emailInput).toHaveValue('test@example.com');
    });

    it('allows entering password', async () => {
      const user = userEvent.setup();
      renderLoginPage();

      const passwordInput = screen.getByLabelText(/password/i);
      await user.type(passwordInput, 'password123');

      expect(passwordInput).toHaveValue('password123');
    });

    it('calls login on form submission', async () => {
      const user = userEvent.setup();
      mockLogin.mockResolvedValue(undefined);
      renderLoginPage();

      await user.type(screen.getByLabelText(/email/i), 'test@example.com');
      await user.type(screen.getByLabelText(/password/i), 'password123');
      await user.click(screen.getByRole('button', { name: /sign in/i }));

      expect(mockLogin).toHaveBeenCalledWith('test@example.com', 'password123');
    });

    it('handles login error gracefully', async () => {
      const user = userEvent.setup();
      mockLogin.mockRejectedValue(new Error('Invalid credentials'));
      renderLoginPage();

      await user.type(screen.getByLabelText(/email/i), 'wrong@example.com');
      await user.type(screen.getByLabelText(/password/i), 'wrongpassword');
      await user.click(screen.getByRole('button', { name: /sign in/i }));

      // Should not throw, error handled by context
      expect(mockLogin).toHaveBeenCalled();
    });
  });

  describe('loading state', () => {
    it('disables inputs during loading', () => {
      mockAuthContext.isLoading = true;
      renderLoginPage();

      expect(screen.getByLabelText(/email/i)).toBeDisabled();
      expect(screen.getByLabelText(/password/i)).toBeDisabled();
      expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled();
    });

    it('shows loading text in button', () => {
      mockAuthContext.isLoading = true;
      renderLoginPage();

      expect(screen.getByRole('button')).toHaveTextContent(/signing in/i);
    });

    it('shows normal text when not loading', () => {
      mockAuthContext.isLoading = false;
      renderLoginPage();

      expect(screen.getByRole('button')).toHaveTextContent(/sign in/i);
    });
  });

  describe('error state', () => {
    it('displays error message when present', () => {
      mockAuthContext.error = 'Login failed. Check your credentials.';
      renderLoginPage();

      expect(screen.getByText(/login failed/i)).toBeInTheDocument();
    });

    it('does not display error message when null', () => {
      mockAuthContext.error = null;
      renderLoginPage();

      expect(screen.queryByText(/login failed/i)).not.toBeInTheDocument();
    });
  });

  describe('form validation', () => {
    it('requires email field', () => {
      renderLoginPage();

      const emailInput = screen.getByLabelText(/email/i);
      expect(emailInput).toBeRequired();
    });

    it('requires password field', () => {
      renderLoginPage();

      const passwordInput = screen.getByLabelText(/password/i);
      expect(passwordInput).toBeRequired();
    });
  });

  describe('accessibility', () => {
    it('has accessible labels', () => {
      renderLoginPage();

      expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    });

    it('uses correct heading hierarchy', () => {
      renderLoginPage();

      const heading = screen.getByRole('heading', { level: 1 });
      expect(heading).toHaveTextContent(/ag3ntum/i);
    });
  });
});
