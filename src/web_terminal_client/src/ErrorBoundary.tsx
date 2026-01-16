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

import React, { Component, type ErrorInfo, type ReactNode } from 'react';

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
        <div style={styles.container}>
          <div style={styles.card}>
            <div style={styles.iconContainer}>
              <svg
                style={styles.icon}
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
            </div>

            <h2 style={styles.title}>Something went wrong</h2>

            <p style={styles.message}>
              An unexpected error occurred. The application will automatically retry.
            </p>

            {this.state.error && (
              <div style={styles.errorBox}>
                <code style={styles.errorText}>
                  {this.state.error.message || 'Unknown error'}
                </code>
              </div>
            )}

            <div style={styles.buttonContainer}>
              <button onClick={this.handleRetry} style={styles.button}>
                Retry Now
              </button>
              <span style={styles.countdown}>
                Auto-retry in {this.state.retryCountdown}s
              </span>
            </div>

            <p style={styles.hint}>
              If this error persists, try refreshing the page.
            </p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '100vh',
    backgroundColor: '#1a1a2e',
    padding: '1rem',
  },
  card: {
    backgroundColor: '#16213e',
    borderRadius: '8px',
    padding: '2rem',
    maxWidth: '500px',
    width: '100%',
    textAlign: 'center',
    boxShadow: '0 4px 6px rgba(0, 0, 0, 0.3)',
    border: '1px solid #0f3460',
  },
  iconContainer: {
    marginBottom: '1rem',
  },
  icon: {
    width: '48px',
    height: '48px',
    color: '#e94560',
    margin: '0 auto',
  },
  title: {
    color: '#e94560',
    fontSize: '1.5rem',
    fontWeight: 600,
    marginBottom: '0.5rem',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  message: {
    color: '#a0a0a0',
    fontSize: '0.95rem',
    marginBottom: '1rem',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  errorBox: {
    backgroundColor: '#0f0f1a',
    borderRadius: '4px',
    padding: '0.75rem',
    marginBottom: '1.5rem',
    border: '1px solid #2a2a4a',
    overflow: 'auto',
    maxHeight: '100px',
  },
  errorText: {
    color: '#ff6b6b',
    fontSize: '0.85rem',
    fontFamily: 'Monaco, Consolas, monospace',
    wordBreak: 'break-word',
  },
  buttonContainer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '1rem',
    marginBottom: '1rem',
  },
  button: {
    backgroundColor: '#e94560',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    padding: '0.5rem 1.5rem',
    fontSize: '0.95rem',
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'background-color 0.2s',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  countdown: {
    color: '#666',
    fontSize: '0.85rem',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  hint: {
    color: '#555',
    fontSize: '0.8rem',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
};

export default ErrorBoundary;
