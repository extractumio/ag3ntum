/**
 * ErrorBoundary - React error boundary for catching render-phase errors.
 *
 * Features:
 * - Catches errors during rendering, in lifecycle methods, and constructors
 * - Shows user-friendly error message with retry option
 * - Automatic retry after 5 seconds
 * - Logs error details to console for debugging
 * - Preserves connection state (managed externally by ConnectionManager)
 */

import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  retryCountdown: number;
}

export class ErrorBoundary extends Component<Props, State> {
  private retryTimer: ReturnType<typeof setInterval> | null = null;
  private readonly AUTO_RETRY_SECONDS = 5;

  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      retryCountdown: this.AUTO_RETRY_SECONDS,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Log error details for debugging
    console.error('[ErrorBoundary] Caught error:', error);
    console.error('[ErrorBoundary] Component stack:', errorInfo.componentStack);

    this.setState({ errorInfo });
    this.props.onError?.(error, errorInfo);

    // Start auto-retry countdown
    this.startRetryCountdown();
  }

  componentWillUnmount(): void {
    this.clearRetryTimer();
  }

  private startRetryCountdown(): void {
    this.clearRetryTimer();
    this.setState({ retryCountdown: this.AUTO_RETRY_SECONDS });

    this.retryTimer = setInterval(() => {
      this.setState((prevState) => {
        const newCountdown = prevState.retryCountdown - 1;
        if (newCountdown <= 0) {
          this.handleRetry();
          return { retryCountdown: 0 };
        }
        return { retryCountdown: newCountdown };
      });
    }, 1000);
  }

  private clearRetryTimer(): void {
    if (this.retryTimer) {
      clearInterval(this.retryTimer);
      this.retryTimer = null;
    }
  }

  private handleRetry = (): void => {
    this.clearRetryTimer();
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      retryCountdown: this.AUTO_RETRY_SECONDS,
    });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="error-boundary-container">
          <div className="error-boundary-card">
            <svg
              className="error-boundary-icon"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>

            <h2 className="error-boundary-title">Something went wrong</h2>

            <p className="error-boundary-message">
              An unexpected error occurred. The application will automatically retry.
            </p>

            {this.state.error && (
              <div className="error-boundary-error-box">
                <code className="error-boundary-error-text">
                  {this.state.error.message || 'Unknown error'}
                </code>
              </div>
            )}

            <div className="error-boundary-button-container">
              <button onClick={this.handleRetry} className="error-boundary-button">
                Retry Now
              </button>
              <span className="error-boundary-countdown">
                Auto-retry in {this.state.retryCountdown}s
              </span>
            </div>

            <p className="error-boundary-hint">
              If this error persists, try refreshing the page.
            </p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
