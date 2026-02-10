/**
 * Input field component
 *
 * Provides the main text input area with file attachment, model selection,
 * and mount selector. Extracted from App.tsx for better modularity.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { blockAltKeyHotkeys, calculateInsertText } from '../../utils';
import { MountSelectorPopup } from '../MountSelectorPopup';
import type { DynamicMountRequest } from '../../api';

export type AttachedFile = {
  file: File;
  id: string;
};

/**
 * Format a model name for display in the dropdown.
 *
 * Transforms model identifiers into user-friendly names:
 * - Removes 'claude-' prefix
 * - Removes date suffix (e.g., '-20250929')
 * - Replaces ':mode=thinking' suffix with ' [thinking]' indicator
 */
export function formatModelName(model: string): string {
  let displayName = model;

  // Check if thinking mode is enabled
  const isThinking = model.endsWith(':mode=thinking');
  if (isThinking) {
    displayName = displayName.replace(':mode=thinking', '');
  }

  // Remove 'claude-' prefix
  displayName = displayName.replace(/^claude-/, '');

  // Remove date suffix (8-digit date at end)
  displayName = displayName.replace(/-\d{8}$/, '');

  // Add thinking indicator if applicable
  if (isThinking) {
    displayName += ' [thinking]';
  }

  return displayName;
}

export interface InputFieldProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  isRunning: boolean;
  attachedFiles: AttachedFile[];
  onAttachFiles: (files: File[]) => void;
  onRemoveFile: (id: string) => void;
  model: string;
  onModelChange: (model: string) => void;
  availableModels: string[];
  baseUrl?: string;
  token?: string;
  dynamicMounts: DynamicMountRequest[];
  onMountsChange: (mounts: DynamicMountRequest[]) => void;
  registerInsertText?: (insertFn: (text: string) => void) => void;
}

export function InputField({
  value,
  onChange,
  onSubmit,
  onCancel,
  isRunning,
  attachedFiles,
  onAttachFiles,
  onRemoveFile,
  model,
  onModelChange,
  availableModels,
  baseUrl,
  token,
  dynamicMounts,
  onMountsChange,
  registerInsertText,
}: InputFieldProps): JSX.Element {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounter = useRef(0);

  // Auto-focus textarea when not running, and refocus after running completes
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isRunning]);

  // Keep focus on the input area - refocus when clicking elsewhere in the app
  useEffect(() => {
    const handleWindowFocus = () => {
      if (textareaRef.current && document.activeElement !== textareaRef.current) {
        // Small delay to not interfere with intentional clicks
        setTimeout(() => {
          if (textareaRef.current && !document.activeElement?.closest('.input-shell')) {
            textareaRef.current.focus();
          }
        }, 100);
      }
    };
    window.addEventListener('focus', handleWindowFocus);
    return () => window.removeEventListener('focus', handleWindowFocus);
  }, []);

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current += 1;
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) {
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current = 0;
    setIsDragging(false);

    // Check for text/plain first (filename from file explorer drag)
    const textData = e.dataTransfer.getData('text/plain');
    if (textData && !e.dataTransfer.files.length) {
      handleInsertText(textData);
      return;
    }

    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      onAttachFiles(files);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) {
      onAttachFiles(files);
    }
    e.target.value = '';
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Block Alt+key hotkeys from inserting special characters
    if (blockAltKeyHotkeys(e)) return;

    if (e.key === 'Enter') {
      // Shift+Enter = new line (let default behavior happen)
      if (e.shiftKey) {
        return;
      }
      // Enter or Ctrl+Enter or Cmd+Enter = send message
      e.preventDefault();
      if (!isRunning && value.trim()) {
        onSubmit();
      }
    }
  };

  // Auto-resize textarea based on content
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      // Reset height to auto to get the correct scrollHeight
      textarea.style.height = 'auto';
      // Set height to scrollHeight, capped at max-height via CSS
      textarea.style.height = `${textarea.scrollHeight}px`;
    }
  }, [value]);

  // Smart text insertion at cursor position with automatic spacing
  const handleInsertText = useCallback((text: string): void => {
    if (!text) return;

    const textarea = textareaRef.current;
    if (!textarea) {
      // Fallback: append with spacing if textarea not available
      const needsSpace = value.length > 0 && !/\s$/.test(value);
      onChange(value + (needsSpace ? ' ' : '') + text);
      return;
    }

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;

    // Use utility function for the calculation
    const { newValue, newCursorPos } = calculateInsertText(value, start, end, text);
    onChange(newValue);

    // Position cursor after inserted text
    setTimeout(() => {
      textarea.selectionStart = textarea.selectionEnd = newCursorPos;
      textarea.focus();
    }, 0);
  }, [value, onChange]);

  // Register the insert function with parent component
  useEffect(() => {
    if (registerInsertText) {
      registerInsertText(handleInsertText);
    }
  }, [registerInsertText, handleInsertText]);

  return (
    <div className="input-area">
      <div
        className={`input-shell ${isDragging ? 'input-dragging' : ''}`}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {isDragging && (
          <div className="input-drop-overlay">
            <div className="input-drop-content">
              <span className="input-drop-icon">{'\uD83D\uDCC1'}</span>
              <span className="input-drop-text">Drop files here</span>
            </div>
          </div>
        )}

        {attachedFiles.length > 0 && (
          <div className="attached-files">
            {attachedFiles.map((item) => (
              <div key={item.id} className="attached-file">
                <span className="attached-file-icon">{'\uD83D\uDCC4'}</span>
                <span className="attached-file-name" title={item.file.name}>
                  {item.file.name.length > 24
                    ? `${item.file.name.slice(0, 20)}...${item.file.name.slice(-4)}`
                    : item.file.name}
                </span>
                <span className="attached-file-size">{formatFileSize(item.file.size)}</span>
                <button
                  type="button"
                  className="attached-file-remove"
                  onClick={() => onRemoveFile(item.id)}
                  title="Remove file"
                >
                  {'\u00D7'}
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="input-main">
          <span className="input-prompt">{'\u27E9'}</span>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter your request... (Shift+Enter for new line)"
            className="input-textarea"
            rows={2}
          />
        </div>

        <div className="input-footer">
          <button
            type="button"
            className="filter-button"
            onClick={() => fileInputRef.current?.click()}
            title="Attach files"
          >
            {attachedFiles.length > 0 ? `[Attach (${attachedFiles.length})]` : '[Attach]'}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFileSelect}
            style={{ display: 'none' }}
          />

          {baseUrl && token && (
            <MountSelectorPopup
              baseUrl={baseUrl}
              token={token}
              selectedMounts={dynamicMounts}
              onMountsChange={onMountsChange}
            />
          )}

          <div className="input-spacer" />

          <div className="dropdown input-model-dropdown">
            <span className="dropdown-value">
              {formatModelName(model)}
            </span>
            <span className="dropdown-icon">{'\u25BE'}</span>
            <div className="dropdown-list">
              {availableModels.map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`dropdown-item ${m === model ? 'active' : ''}`}
                  onClick={() => onModelChange(m)}
                >
                  {formatModelName(m)}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="input-actions">
        {isRunning ? (
          <button className="filter-button" type="button" onClick={onCancel} title="Cancel (Esc)">
            [Stop]
          </button>
        ) : (
          <button
            className="filter-button"
            type="button"
            onClick={onSubmit}
            disabled={!value.trim()}
            title="Send (Enter)"
          >
            [Send]
          </button>
        )}
      </div>
    </div>
  );
}
