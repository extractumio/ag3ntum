import React from 'react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div className="dash-dialog-overlay" onClick={onCancel}>
      <div className="dash-dialog" onClick={(e) => e.stopPropagation()}>
        <h3 className="dash-dialog-title">{title}</h3>
        <p className="dash-dialog-message">{message}</p>
        <div className="dash-dialog-actions">
          <button className="dash-btn dash-btn-secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className={`dash-btn ${danger ? 'dash-btn-danger' : 'dash-btn-primary'}`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
