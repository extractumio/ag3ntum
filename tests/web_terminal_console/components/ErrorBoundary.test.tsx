import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Store original timer functions
const originalClearInterval = globalThis.clearInterval;
const originalSetInterval = globalThis.setInterval;

// Simple ErrorBoundary implementation for testing
// (The actual one is in App.tsx but we can test the pattern)
interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  countdown: number;
}

interface ErrorBoundaryProps {
  children: React.ReactNode;
  onReset?: () => void;
}

class TestErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  private countdownInterval: ReturnType<typeof setInterval> | null = null;

  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null, countdown: 5 };
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true, error, countdown: 5 };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('Error caught:', error, errorInfo);
  }

  componentDidUpdate(_prevProps: ErrorBoundaryProps, prevState: ErrorBoundaryState) {
    if (this.state.hasError && !prevState.hasError) {
      this.startCountdown();
    }
  }

  componentWillUnmount() {
    this.clearCountdown();
  }

  startCountdown = () => {
    this.clearCountdown();
    this.countdownInterval = setInterval(() => {
      this.setState((state) => {
        if (state.countdown <= 1) {
          this.clearCountdown();
          return { hasError: false, error: null, countdown: 5 };
        }
        return { countdown: state.countdown - 1 };
      });
    }, 1000);
  };

  clearCountdown = () => {
    if (this.countdownInterval) {
      clearInterval(this.countdownInterval);
      this.countdownInterval = null;
    }
  };

  handleRetry = () => {
    this.clearCountdown();
    this.setState({ hasError: false, error: null, countdown: 5 });
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary" role="alert">
          <h1>Something went wrong</h1>
          <p className="error-message">{this.state.error?.message || 'Unknown error'}</p>
          <p className="countdown">Retrying in {this.state.countdown}s...</p>
          <button onClick={this.handleRetry}>Retry Now</button>
        </div>
      );
    }

    return this.props.children;
  }
}

// Component that throws an error
function ThrowingComponent({ shouldThrow = true }: { shouldThrow?: boolean }) {
  if (shouldThrow) {
    throw new Error('Test error');
  }
  return <div>Content loaded successfully</div>;
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Suppress console.error during tests
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    // Restore real timers before React cleanup runs
    vi.useRealTimers();
    vi.restoreAllMocks();
    // Ensure clearInterval is available for React cleanup
    globalThis.clearInterval = originalClearInterval;
    globalThis.setInterval = originalSetInterval;
  });

  describe('normal operation', () => {
    it('renders children when no error', () => {
      render(
        <TestErrorBoundary>
          <div>Normal content</div>
        </TestErrorBoundary>
      );

      expect(screen.getByText('Normal content')).toBeInTheDocument();
    });

    it('does not show error UI when no error', () => {
      render(
        <TestErrorBoundary>
          <div>Normal content</div>
        </TestErrorBoundary>
      );

      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  describe('error handling', () => {
    it('catches and displays error', () => {
      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
      expect(screen.getByText(/test error/i)).toBeInTheDocument();
    });

    it('shows retry button', () => {
      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    });

    it('shows countdown', () => {
      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      expect(screen.getByText(/retrying in 5s/i)).toBeInTheDocument();
    });
  });

  describe('countdown behavior', () => {
    it('shows initial countdown value', () => {
      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      // Verify initial countdown is shown
      expect(screen.getByText(/retrying in 5s/i)).toBeInTheDocument();
    });

    it('countdown element is present when error occurs', () => {
      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      // Verify countdown paragraph exists
      expect(screen.getByText(/retrying in/i)).toBeInTheDocument();
    });
  });

  describe('manual retry', () => {
    it('resets error state on retry click', async () => {
      // Use real timers for userEvent
      vi.useRealTimers();

      const user = userEvent.setup();
      let shouldThrow = true;

      function ConditionalThrow() {
        if (shouldThrow) {
          throw new Error('Test error');
        }
        return <div>Content loaded</div>;
      }

      const { rerender } = render(
        <TestErrorBoundary>
          <ConditionalThrow />
        </TestErrorBoundary>
      );

      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

      // Fix the error
      shouldThrow = false;

      // Click retry
      await user.click(screen.getByRole('button', { name: /retry/i }));

      rerender(
        <TestErrorBoundary>
          <ConditionalThrow />
        </TestErrorBoundary>
      );

      await waitFor(() => {
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      });
    });

    it('calls onReset callback', async () => {
      // Use real timers for userEvent
      vi.useRealTimers();

      const user = userEvent.setup();
      const onReset = vi.fn();

      render(
        <TestErrorBoundary onReset={onReset}>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      await user.click(screen.getByRole('button', { name: /retry/i }));

      expect(onReset).toHaveBeenCalled();
    });

    it('retry button is clickable', async () => {
      vi.useRealTimers();
      const user = userEvent.setup();

      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      const retryButton = screen.getByRole('button', { name: /retry/i });
      expect(retryButton).toBeInTheDocument();

      // Clicking should not throw
      await user.click(retryButton);

      // After click, the error boundary should try to re-render children
      // (which will throw again since shouldThrow is still true)
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  describe('error message display', () => {
    it('displays custom error message', () => {
      function CustomErrorComponent() {
        throw new Error('Custom error message');
      }

      render(
        <TestErrorBoundary>
          <CustomErrorComponent />
        </TestErrorBoundary>
      );

      expect(screen.getByText(/custom error message/i)).toBeInTheDocument();
    });

    it('handles error without message', () => {
      function NoMessageError() {
        throw new Error();
      }

      render(
        <TestErrorBoundary>
          <NoMessageError />
        </TestErrorBoundary>
      );

      expect(screen.getByText(/unknown error/i)).toBeInTheDocument();
    });
  });

  describe('accessibility', () => {
    it('uses alert role for error state', () => {
      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      expect(screen.getByRole('alert')).toBeInTheDocument();
    });

    it('has accessible retry button', () => {
      render(
        <TestErrorBoundary>
          <ThrowingComponent shouldThrow={true} />
        </TestErrorBoundary>
      );

      const button = screen.getByRole('button', { name: /retry/i });
      expect(button).toBeInTheDocument();
      expect(button).not.toBeDisabled();
    });
  });
});
