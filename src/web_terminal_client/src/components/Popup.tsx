/**
 * Unified Popup component for modals, dialogs, and alerts.
 *
 * Supports different types (error, success, warning, info, confirm) with
 * consistent styling and behavior across the application.
 */
import React, { useCallback, useEffect } from 'react';

export type PopupType = 'error' | 'success' | 'warning' | 'info' | 'confirm';

export interface PopupAction {
  label: string;
  onClick: () => void;
  variant?: 'primary' | 'secondary' | 'danger';
  disabled?: boolean;
}

export interface PopupProps {
  /** Whether the popup is visible */
  isOpen: boolean;
  /** Popup type determines icon and color scheme */
  type: PopupType;
  /** Title text (required) */
  title: string;
  /** Main message content */
  message?: string;
  /** Additional details (displayed in a muted style) */
  details?: string;
  /** Action buttons (if not provided, shows a single "OK"/"Close" button) */
  actions?: PopupAction[];
  /** Called when popup is closed (ESC, overlay click, or close button) */
  onClose: () => void;
  /** Show overlay background (default: true for modals) */
  showOverlay?: boolean;
  /** Allow closing by clicking overlay (default: true) */
  closeOnOverlayClick?: boolean;
  /** Allow closing with ESC key (default: true) */
  closeOnEsc?: boolean;
}

/**
 * Unified Popup component.
 *
 * Usage:
 * ```tsx
 * <Popup
 *   isOpen={showError}
 *   type="error"
 *   title="Session Not Found"
 *   message="The session you're looking for doesn't exist or has been deleted."
 *   onClose={() => setShowError(false)}
 * />
 * ```
 *
 * With custom actions:
 * ```tsx
 * <Popup
 *   isOpen={showConfirm}
 *   type="confirm"
 *   title="Delete Session?"
 *   message="This action cannot be undone."
 *   actions={[
 *     { label: 'Cancel', onClick: () => setShowConfirm(false), variant: 'secondary' },
 *     { label: 'Delete', onClick: handleDelete, variant: 'danger' },
 *   ]}
 *   onClose={() => setShowConfirm(false)}
 * />
 * ```
 */
export function Popup({
  isOpen,
  type,
  title,
  message,
  details,
  actions,
  onClose,
  showOverlay = true,
  closeOnOverlayClick = true,
  closeOnEsc = true,
}: PopupProps): JSX.Element | null {
  // Handle ESC key
  useEffect(() => {
    if (!isOpen || !closeOnEsc) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, closeOnEsc, onClose]);

  // Handle overlay click
  const handleOverlayClick = useCallback(() => {
    if (closeOnOverlayClick) {
      onClose();
    }
  }, [closeOnOverlayClick, onClose]);

  // Stop propagation on modal content click
  const handleContentClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
  }, []);

  if (!isOpen) return null;

  // Default action if none provided
  const displayActions = actions ?? [
    {
      label: type === 'error' ? 'Close' : 'OK',
      onClick: onClose,
      variant: 'primary' as const,
    },
  ];

  return (
    <div
      className={`popup-overlay ${showOverlay ? '' : 'popup-overlay-transparent'}`}
      onClick={handleOverlayClick}
      role="dialog"
      aria-modal="true"
      aria-labelledby="popup-title"
    >
      <div
        className={`popup-modal popup-${type}`}
        onClick={handleContentClick}
      >
        <div className="popup-header">
          <h2 id="popup-title" className="popup-title">{title}</h2>
        </div>

        {(message || details) && (
          <div className="popup-content">
            {message && <p className="popup-message">{message}</p>}
            {details && <p className="popup-details">{details}</p>}
          </div>
        )}

        <div className="popup-actions">
          {displayActions.map((action, index) => (
            <button
              key={index}
              type="button"
              className={`popup-btn popup-btn-${action.variant ?? 'primary'}`}
              onClick={action.onClick}
              disabled={action.disabled}
            >
              {action.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export default Popup;
