/**
 * Toast notification system with stacking support.
 *
 * Features:
 * - Stack up to 3 toasts (oldest auto-removed when exceeded)
 * - Bottom-right positioning
 * - Auto-dismiss after 5 seconds (configurable)
 * - Persistent toasts that require manual dismissal
 * - Types: success, error, warning, info
 */
import React, {
  createContext,
  useContext,
  useCallback,
  useState,
  useEffect,
  useRef,
} from 'react';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastOptions {
  /** Toast type determines styling */
  type: ToastType;
  /** Message to display */
  message: string;
  /** Auto-dismiss duration in ms (default: 5000, set to 0 for persistent) */
  duration?: number;
  /** Whether toast requires manual dismissal (overrides duration) */
  persistent?: boolean;
}

interface Toast extends ToastOptions {
  id: string;
  createdAt: number;
}

interface ToastContextValue {
  /** Add a toast notification */
  addToast: (options: ToastOptions) => string;
  /** Remove a toast by ID */
  removeToast: (id: string) => void;
  /** Remove all toasts */
  clearToasts: () => void;
  /** Shorthand for success toast */
  success: (message: string, persistent?: boolean) => string;
  /** Shorthand for error toast */
  error: (message: string, persistent?: boolean) => string;
  /** Shorthand for warning toast */
  warning: (message: string, persistent?: boolean) => string;
  /** Shorthand for info toast */
  info: (message: string, persistent?: boolean) => string;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const MAX_TOASTS = 3;
const DEFAULT_DURATION = 5000;

let toastIdCounter = 0;

/**
 * Toast provider component - wrap your app with this.
 */
export function ToastProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      timersRef.current.forEach((timer) => clearTimeout(timer));
    };
  }, []);

  const removeToast = useCallback((id: string) => {
    // Clear timer if exists
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback((options: ToastOptions): string => {
    const id = `toast-${++toastIdCounter}`;
    const duration = options.persistent ? 0 : (options.duration ?? DEFAULT_DURATION);

    const newToast: Toast = {
      ...options,
      id,
      createdAt: Date.now(),
      duration,
    };

    setToasts((prev) => {
      // Add new toast at the end (bottom of stack visually)
      const updated = [...prev, newToast];
      // Remove oldest if exceeding max
      if (updated.length > MAX_TOASTS) {
        const removed = updated.shift();
        if (removed) {
          const timer = timersRef.current.get(removed.id);
          if (timer) {
            clearTimeout(timer);
            timersRef.current.delete(removed.id);
          }
        }
      }
      return updated;
    });

    // Set auto-dismiss timer if not persistent
    if (duration > 0) {
      const timer = setTimeout(() => {
        removeToast(id);
      }, duration);
      timersRef.current.set(id, timer);
    }

    return id;
  }, [removeToast]);

  const clearToasts = useCallback(() => {
    timersRef.current.forEach((timer) => clearTimeout(timer));
    timersRef.current.clear();
    setToasts([]);
  }, []);

  // Shorthand methods
  const success = useCallback((message: string, persistent = false) => {
    return addToast({ type: 'success', message, persistent });
  }, [addToast]);

  const error = useCallback((message: string, persistent = false) => {
    return addToast({ type: 'error', message, persistent });
  }, [addToast]);

  const warning = useCallback((message: string, persistent = false) => {
    return addToast({ type: 'warning', message, persistent });
  }, [addToast]);

  const info = useCallback((message: string, persistent = false) => {
    return addToast({ type: 'info', message, persistent });
  }, [addToast]);

  const value: ToastContextValue = {
    addToast,
    removeToast,
    clearToasts,
    success,
    error,
    warning,
    info,
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </ToastContext.Provider>
  );
}

/**
 * Hook to access toast functions.
 */
export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}

/**
 * Toast container - renders the stack of toasts.
 */
function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}): JSX.Element | null {
  if (toasts.length === 0) return null;

  return (
    <div className="toast-container" role="region" aria-label="Notifications">
      {toasts.map((toast, index) => (
        <ToastItem
          key={toast.id}
          toast={toast}
          onDismiss={onDismiss}
          index={index}
        />
      ))}
    </div>
  );
}

/**
 * Individual toast item.
 */
function ToastItem({
  toast,
  onDismiss,
  index,
}: {
  toast: Toast;
  onDismiss: (id: string) => void;
  index: number;
}): JSX.Element {
  const [isExiting, setIsExiting] = useState(false);

  const handleDismiss = useCallback(() => {
    setIsExiting(true);
    // Wait for exit animation
    setTimeout(() => onDismiss(toast.id), 200);
  }, [onDismiss, toast.id]);

  const isPersistent = toast.persistent || toast.duration === 0;

  return (
    <div
      className={`toast toast-${toast.type} ${isExiting ? 'toast-exit' : 'toast-enter'}`}
      role="alert"
      aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
      style={{ '--toast-index': index } as React.CSSProperties}
    >
      <span className="toast-message">{toast.message}</span>
      <button
        type="button"
        className="toast-dismiss"
        onClick={handleDismiss}
        aria-label="Dismiss notification"
      >
        {'\u00D7'}
      </button>
      {!isPersistent && (
        <div
          className="toast-progress"
          style={{ animationDuration: `${toast.duration}ms` }}
        />
      )}
    </div>
  );
}

export default ToastProvider;
