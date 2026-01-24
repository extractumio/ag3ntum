/**
 * FileExplorer Component
 *
 * A reusable widget for browsing session workspace files in a tree structure.
 * Features:
 * - Tree view with expandable folders
 * - Sorting by name, size, or date
 * - File actions: view, download, delete
 * - File preview modal for text files (using FileViewer)
 * - Responsive design that adapts to container size
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  browseFiles,
  deleteFile,
  downloadFile,
  getFileContent,
  uploadFiles,
} from './api';
import type {
  FileContentResponse,
  FileInfo,
  FileSortField,
  SortOrder,
} from './types';
import { FileViewerModal, ImageViewerModal, toFileViewerData, type FileViewerData } from './FileViewer';

// =============================================================================
// Types
// =============================================================================

interface FileExplorerProps {
  sessionId: string;
  baseUrl: string;
  token: string;
  showHiddenFiles?: boolean;
  className?: string;
  onError?: (error: string) => void;
  onModalStateChange?: (isModalOpen: boolean) => void;
  /** Path to navigate to and highlight (expands parent folders) */
  navigateTo?: string | null;
  /** Callback when navigation is complete */
  onNavigateComplete?: () => void;
  /** Callback when a filename should be inserted into the input (double-click or drag) */
  onFileNameInsert?: (filename: string) => void;
}

interface ExpandedFolders {
  [path: string]: boolean;
}

// =============================================================================
// SVG Icon Components
// =============================================================================

interface IconProps {
  className?: string;
  label?: string;
}

function FileIcon({ className = '', label }: IconProps): JSX.Element {
  return (
    <span className={`file-icon-wrapper ${className}`}>
      <svg
        className="file-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M7 0H2V16H14V7H7V0Z" fill="currentColor" />
        <path d="M9 0V5H14L9 0Z" fill="currentColor" />
      </svg>
      {label && <span className="file-icon-label">{label}</span>}
    </span>
  );
}

function FolderIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`file-icon-wrapper ${className}`}>
      <svg
        className="file-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M0 1H6L9 4H16V14H0V1Z" fill="currentColor" />
      </svg>
    </span>
  );
}

function EyeIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 32 32"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M0 16q0.064 0.128 0.16 0.352t0.48 0.928 0.832 1.344 1.248 1.536 1.664 1.696 2.144 1.568 2.624 1.344 3.136 0.896 3.712 0.352 3.712-0.352 3.168-0.928 2.592-1.312 2.144-1.6 1.664-1.632 1.248-1.6 0.832-1.312 0.48-0.928l0.16-0.352q-0.032-0.128-0.16-0.352t-0.48-0.896-0.832-1.344-1.248-1.568-1.664-1.664-2.144-1.568-2.624-1.344-3.136-0.896-3.712-0.352-3.712 0.352-3.168 0.896-2.592 1.344-2.144 1.568-1.664 1.664-1.248 1.568-0.832 1.344-0.48 0.928zM10.016 16q0-2.464 1.728-4.224t4.256-1.76 4.256 1.76 1.76 4.224-1.76 4.256-4.256 1.76-4.256-1.76-1.728-4.256zM12 16q0 1.664 1.184 2.848t2.816 1.152 2.816-1.152 1.184-2.848-1.184-2.816-2.816-1.184-2.816 1.184l2.816 2.816h-4z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

function DownloadIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 -0.5 21 21"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M11.55,11 L11.55,4 L9.45,4 L9.45,11 L5.9283,11 L10.38345,16.243 L15.1263,11 L11.55,11 Z M12.6,0 L12.6,2 L18.9,2 L18.9,8 L21,8 L21,0 L12.6,0 Z M18.9,18 L12.6,18 L12.6,20 L21,20 L21,12 L18.9,12 L18.9,18 Z M2.1,12 L0,12 L0,20 L8.4,20 L8.4,18 L2.1,18 L2.1,12 Z M2.1,8 L0,8 L0,0 L8.4,0 L8.4,2 L2.1,2 L2.1,8 Z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

function TrashIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M4 2H1V4H15V2H12V0H4V2Z" fill="currentColor" />
        <path
          fillRule="evenodd"
          clipRule="evenodd"
          d="M3 6H13V16H3V6ZM7 9H9V13H7V9Z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

function UploadIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M8 0L3 5H6V10H10V5H13L8 0Z" fill="currentColor" />
        <path d="M2 12H14V14H2V12Z" fill="currentColor" />
      </svg>
    </span>
  );
}

/**
 * Lock icon to indicate read-only files/folders.
 * Displayed as a small gray dot next to the file icon.
 */
function ReadOnlyIndicator({ className = '' }: IconProps): JSX.Element {
  return (
    <span
      className={`readonly-indicator ${className}`}
      title="Read-only"
    >
      ●
    </span>
  );
}

// Note: CopyIcon, CheckIcon, MarkdownIcon, TextIcon are now in FileViewer.tsx

// =============================================================================
// Icon Constants (for other UI elements)
// =============================================================================

const ICONS = {
  warning: '⚠',
  refresh: '↻',
  spinner: '◌',
  close: '✕',
};

// =============================================================================
// Utility Functions
// =============================================================================

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatDateTime(isoString: string): string {
  const date = new Date(isoString);
  const day = String(date.getDate()).padStart(2, '0');
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const year = String(date.getFullYear()).slice(-2);
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${day}/${month}/${year} ${hours}:${minutes}`;
}

/**
 * Sanitize a filename for safe display.
 * Prevents XSS attacks and removes potentially dangerous characters.
 * React's JSX already escapes HTML, but this provides defense in depth
 * against control characters, null bytes, and unicode tricks.
 */
export function sanitizeFilename(name: string): string {
  if (!name || typeof name !== 'string') return '';

  // Remove null bytes (can truncate strings in some contexts)
  let sanitized = name.replace(/\0/g, '');

  // Remove other control characters (ASCII 0-31 except tab, newline, carriage return)
  // These can cause display issues or be used in attacks
  sanitized = sanitized.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '');

  // Normalize unicode to NFC form to prevent homograph attacks
  // (e.g., using lookalike characters from different scripts)
  try {
    sanitized = sanitized.normalize('NFC');
  } catch {
    // If normalization fails, continue with the string as-is
  }

  // Remove zero-width characters that could hide malicious content
  sanitized = sanitized.replace(/[\u200B-\u200D\uFEFF\u00AD]/g, '');

  // Limit maximum length to prevent DoS via extremely long filenames
  const MAX_DISPLAY_LENGTH = 255;
  if (sanitized.length > MAX_DISPLAY_LENGTH) {
    sanitized = sanitized.slice(0, MAX_DISPLAY_LENGTH);
  }

  return sanitized;
}

function truncateFilename(name: string, maxLength: number = 40): string {
  const sanitized = sanitizeFilename(name);
  if (sanitized.length <= maxLength) return sanitized;
  const firstPart = 18;
  const lastPart = 20;
  return `${sanitized.slice(0, firstPart)}..${sanitized.slice(-lastPart)}`;
}

function getFileIconLabel(file: FileInfo): string | undefined {
  const ext = file.name.split('.').pop()?.toLowerCase() || '';

  // Return short labels for common file types
  // Code files
  if (ext === 'js') return '.JS';
  if (ext === 'ts') return '.TS';
  if (ext === 'jsx') return 'JSX';
  if (ext === 'tsx') return 'TSX';
  if (ext === 'py') return '.PY';
  if (ext === 'rb') return '.RB';
  if (ext === 'go') return '.GO';
  if (ext === 'rs') return '.RS';
  if (ext === 'java') return 'JVA';
  if (['c', 'cpp', 'h'].includes(ext)) return '.C ';

  // Web files
  if (ext === 'html') return 'HTM';
  if (ext === 'css') return 'CSS';
  if (ext === 'scss') return 'SCS';

  // Config files
  if (ext === 'json') return '{ }';
  if (['yaml', 'yml'].includes(ext)) return 'YML';
  if (ext === 'toml') return 'TML';
  if (ext === 'xml') return 'XML';

  // Documents
  if (ext === 'md') return '.MD';
  if (ext === 'txt') return 'TXT';
  if (ext === 'pdf') return 'PDF';

  // Shell
  if (['sh', 'bash', 'zsh'].includes(ext)) return '$';

  // Archives
  if (['zip', 'tar', 'gz', '7z', 'rar'].includes(ext)) return 'Z';

  // Data
  if (ext === 'csv') return 'CSV';
  if (ext === 'sql') return 'SQL';
  if (['db', 'sqlite'].includes(ext)) return '.DB';

  return undefined;
}

function renderFileIcon(file: FileInfo): JSX.Element {
  const icon = file.is_directory ? <FolderIcon /> : <FileIcon label={getFileIconLabel(file)} />;

  // Show read-only indicator (gray dot) for files/folders in read-only areas
  if (file.is_readonly) {
    return (
      <span className="file-icon-with-indicator">
        {icon}
        <ReadOnlyIndicator />
      </span>
    );
  }

  return icon;
}

// Note: Markdown rendering is now handled by FileViewer component

// =============================================================================
// DeleteConfirmModal Component
// =============================================================================

interface DeleteConfirmModalProps {
  fileName: string;
  isDeleting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

function DeleteConfirmModal({
  fileName,
  isDeleting,
  onConfirm,
  onCancel,
}: DeleteConfirmModalProps): JSX.Element {
  // Handle ESC key to close modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isDeleting) {
        e.preventDefault();
        e.stopPropagation();
        onCancel();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isDeleting, onCancel]);

  return (
    <div className="file-delete-overlay" onClick={onCancel}>
      <div className="file-delete-modal" onClick={(e) => e.stopPropagation()}>
        <div className="file-delete-header">
          <span className="file-delete-icon">{ICONS.warning}</span>
          <span>Confirm Delete</span>
        </div>
        <div className="file-delete-content">
          <p>Are you sure you want to delete this file?</p>
          <p className="file-delete-filename">{sanitizeFilename(fileName)}</p>
          <p className="file-delete-warning">This action cannot be undone.</p>
        </div>
        <div className="file-delete-actions">
          <button
            type="button"
            className="file-delete-cancel-btn"
            onClick={onCancel}
            disabled={isDeleting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="file-delete-confirm-btn"
            onClick={onConfirm}
            disabled={isDeleting}
          >
            {isDeleting ? 'Deleting...' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// FileTreeNode Component
// =============================================================================

interface FileTreeNodeProps {
  file: FileInfo;
  depth: number;
  getIsExpanded: (path: string) => boolean;
  getIsLoading: (path: string) => boolean;
  getChildren: (path: string) => FileInfo[] | undefined;
  getIsHighlighted: (path: string) => boolean;
  onToggle: (path: string) => void;
  onView: (file: FileInfo) => void;
  onDownload: (file: FileInfo) => void;
  onDelete: (file: FileInfo) => void;
  onFileNameInsert?: (filename: string) => void;
}

function FileTreeNode({
  file,
  depth,
  getIsExpanded,
  getIsLoading,
  getChildren,
  getIsHighlighted,
  onToggle,
  onView,
  onDownload,
  onDelete,
  onFileNameInsert,
}: FileTreeNodeProps): JSX.Element {
  const isExpanded = file.is_directory ? getIsExpanded(file.path) : false;
  const isLoading = file.is_directory ? getIsLoading(file.path) : false;
  const children = file.is_directory ? getChildren(file.path) : undefined;
  const isHighlighted = getIsHighlighted(file.path);
  const handleClick = () => {
    if (file.is_directory) {
      onToggle(file.path);
    } else if (file.is_viewable || file.mime_type?.startsWith('image/')) {
      // View text files and images (images open in preview popup)
      onView(file);
    }
    // Binary files: do nothing on single click (no download)
  };

  // Convert path to relative format starting with ./
  const getRelativePath = (path: string): string => {
    if (path.startsWith('./')) return path;
    if (path.startsWith('/')) {
      // For absolute paths, just use the path as-is (workspace context handles this)
      return path;
    }
    return './' + path;
  };

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (onFileNameInsert) {
      onFileNameInsert(getRelativePath(file.path));
    }
  };

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('text/plain', getRelativePath(file.path));
    e.dataTransfer.effectAllowed = 'copy';
  };

  return (
    <div className="file-tree-node">
      <div
        className={`file-tree-row ${file.is_directory ? 'file-tree-folder' : 'file-tree-file'}${isHighlighted ? ' file-tree-highlighted' : ''}${file.is_readonly ? ' file-readonly' : ''}`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        draggable
        onDragStart={handleDragStart}
        title={file.is_directory
          ? `${file.name}\nDouble-click or drag to insert path`
          : `${file.name}\nSize: ${formatFileSize(file.size)}\nModified: ${new Date(file.modified_at).toLocaleString()}\nDouble-click or drag to insert path`}
      >
        <span className="file-tree-toggle">
          {file.is_directory ? (isExpanded ? '▼' : '▶') : '\u00A0'}
        </span>
        <span className="file-tree-icon">{renderFileIcon(file)}</span>
        <span className="file-tree-name" title={sanitizeFilename(file.name)}>
          {truncateFilename(file.name)}
        </span>
        <span className="file-tree-size">
          {file.is_directory ? '' : formatFileSize(file.size)}
        </span>
        <span className="file-tree-date" title={`Modified: ${new Date(file.modified_at).toLocaleString()}`}>
          {formatDateTime(file.modified_at)}
        </span>
        <span className="file-tree-actions" onClick={(e) => e.stopPropagation()}>
          {!file.is_directory && (
            <>
              {(file.is_viewable || file.mime_type?.startsWith('image/')) && (
                <button
                  type="button"
                  className="file-action-btn"
                  onClick={() => onView(file)}
                  title="View"
                >
                  <EyeIcon />
                </button>
              )}
              <button
                type="button"
                className="file-action-btn"
                onClick={() => onDownload(file)}
                title="Download"
              >
                <DownloadIcon />
              </button>
              <button
                type="button"
                className="file-action-btn file-action-delete"
                onClick={() => onDelete(file)}
                title="Delete"
              >
                <TrashIcon />
              </button>
            </>
          )}
        </span>
      </div>
      {file.is_directory && isExpanded && (
        <div className="file-tree-children">
          {isLoading ? (
            <div className="file-tree-loading" style={{ paddingLeft: `${(depth + 1) * 16 + 8}px` }}>
              Loading...
            </div>
          ) : children && children.length > 0 ? (
            children.map((child) => (
              <FileTreeNode
                key={child.path}
                file={child}
                depth={depth + 1}
                getIsExpanded={getIsExpanded}
                getIsLoading={getIsLoading}
                getChildren={getChildren}
                getIsHighlighted={getIsHighlighted}
                onToggle={onToggle}
                onView={onView}
                onDownload={onDownload}
                onDelete={onDelete}
                onFileNameInsert={onFileNameInsert}
              />
            ))
          ) : (
            <div className="file-tree-empty" style={{ paddingLeft: `${(depth + 1) * 16 + 8}px` }}>
              (empty folder)
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main FileExplorer Component
// =============================================================================

export function FileExplorer({
  sessionId,
  baseUrl,
  token,
  showHiddenFiles = false,
  className = '',
  onError,
  onModalStateChange,
  navigateTo,
  onNavigateComplete,
  onFileNameInsert,
}: FileExplorerProps): JSX.Element {
  // State
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<ExpandedFolders>({});
  const [folderContents, setFolderContents] = useState<Record<string, FileInfo[]>>({});
  const [loadingFolders, setLoadingFolders] = useState<Set<string>>(new Set());
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [truncatedInfo, setTruncatedInfo] = useState<{ truncated: boolean; totalCount: number; shownCount: number } | null>(null);

  // Sorting state
  const [sortBy, setSortBy] = useState<FileSortField>('modified_at');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  // Navigation/highlight state
  const [highlightedPath, setHighlightedPath] = useState<string | null>(null);

  // Preview modal state (using FileViewer)
  const [previewFile, setPreviewFile] = useState<FileViewerData | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [previewFilePath, setPreviewFilePath] = useState<string | null>(null);
  const [previewImageUrl, setPreviewImageUrl] = useState<string | undefined>(undefined);
  const [previewIsImage, setPreviewIsImage] = useState(false);
  const [previewImageDimensions, setPreviewImageDimensions] = useState<{ width: number; height: number } | null>(null);

  // Delete modal state
  const [deleteTarget, setDeleteTarget] = useState<FileInfo | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Upload state
  const [isUploading, setIsUploading] = useState(false);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [uploadNotification, setUploadNotification] = useState<{
    type: 'uploading' | 'success' | 'error';
    message: string;
    fileCount: number;
  } | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const dragCounter = useRef(0);
  const notificationTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Store callback props in refs to avoid re-creating callbacks when they change.
  // This prevents unnecessary API calls when parent re-renders (e.g., during user input or streaming).
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const onModalStateChangeRef = useRef(onModalStateChange);
  onModalStateChangeRef.current = onModalStateChange;
  const onNavigateCompleteRef = useRef(onNavigateComplete);
  onNavigateCompleteRef.current = onNavigateComplete;

  // Notify parent when modal state changes (for ESC key handling)
  useEffect(() => {
    const isModalOpen = previewFile !== null || isLoadingPreview || deleteTarget !== null;
    onModalStateChangeRef.current?.(isModalOpen);
  }, [previewFile, isLoadingPreview, deleteTarget]);

  // Handle navigation to a specific path
  useEffect(() => {
    if (!navigateTo) return;

    // Normalize the path (remove leading ./, /, and workspace/ prefixes)
    // Agent messages use sandbox format like "/workspace/file.txt"
    let targetPath = navigateTo;
    if (targetPath.startsWith('./')) {
      targetPath = targetPath.slice(2);
    }
    // Remove leading slashes
    while (targetPath.startsWith('/')) {
      targetPath = targetPath.slice(1);
    }
    // Remove workspace/ prefix (sandbox format from agent messages)
    if (targetPath.startsWith('workspace/')) {
      targetPath = targetPath.slice('workspace/'.length);
    } else if (targetPath === 'workspace') {
      targetPath = '';
    }

    // Get parent folder path
    const lastSlashIndex = targetPath.lastIndexOf('/');
    const parentPath = lastSlashIndex > 0 ? targetPath.slice(0, lastSlashIndex) : '';

    // Build list of all parent folders to expand
    const foldersToExpand: string[] = [];
    if (parentPath) {
      const parts = parentPath.split('/');
      let currentPath = '';
      for (const part of parts) {
        currentPath = currentPath ? `${currentPath}/${part}` : part;
        foldersToExpand.push(currentPath);
      }
    }

    // Expand all parent folders
    if (foldersToExpand.length > 0) {
      setExpandedFolders((prev) => {
        const next = { ...prev };
        for (const folder of foldersToExpand) {
          next[folder] = true;
        }
        return next;
      });

      // Load contents of each folder that needs to be expanded
      const loadFolders = async () => {
        for (const folder of foldersToExpand) {
          if (!folderContents[folder]) {
            try {
              const listing = await browseFiles(baseUrl, token, sessionId, folder, {
                includeHidden: showHiddenFiles,
                sortBy,
                sortOrder,
              });
              setFolderContents((prev) => ({ ...prev, [folder]: listing.files }));
            } catch {
              // Ignore errors during navigation
            }
          }
        }
      };
      loadFolders();
    }

    // Highlight the target file
    setHighlightedPath(targetPath);

    // Clear highlight after a few seconds
    const timer = setTimeout(() => {
      setHighlightedPath(null);
    }, 3000);

    // Notify that navigation is complete
    onNavigateCompleteRef.current?.();

    return () => clearTimeout(timer);
  }, [navigateTo, baseUrl, token, sessionId, showHiddenFiles, sortBy, sortOrder, folderContents]);

  // Load root directory
  const loadRootFiles = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setTruncatedInfo(null);

    try {
      const listing = await browseFiles(baseUrl, token, sessionId, '', {
        includeHidden: showHiddenFiles,
        sortBy,
        sortOrder,
      });
      setFiles(listing.files);
      // Track truncation info for warning display
      setTruncatedInfo({
        truncated: listing.truncated,
        totalCount: listing.total_count,
        shownCount: listing.files.length,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load files';
      setError(message);
      onErrorRef.current?.(message);
    } finally {
      setIsLoading(false);
    }
  }, [baseUrl, token, sessionId, showHiddenFiles, sortBy, sortOrder]);

  // Load folder contents
  const loadFolderContents = useCallback(
    async (path: string) => {
      setLoadingFolders((prev) => new Set(prev).add(path));

      try {
        const listing = await browseFiles(baseUrl, token, sessionId, path, {
          includeHidden: showHiddenFiles,
          sortBy,
          sortOrder,
        });
        setFolderContents((prev) => ({ ...prev, [path]: listing.files }));
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to load folder';
        onErrorRef.current?.(message);
      } finally {
        setLoadingFolders((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        });
      }
    },
    [baseUrl, token, sessionId, showHiddenFiles, sortBy, sortOrder]
  );

  // Refresh all: root + all expanded folders
  const refreshAll = useCallback(async () => {
    // Get all currently expanded folder paths
    const expandedPaths = Object.entries(expandedFolders)
      .filter(([, isExpanded]) => isExpanded)
      .map(([path]) => path);

    // Refresh root and all expanded folders in parallel
    await Promise.all([
      loadRootFiles(),
      ...expandedPaths.map((path) => loadFolderContents(path)),
    ]);
  }, [expandedFolders, loadRootFiles, loadFolderContents]);

  // Toggle folder expansion
  const handleToggleFolder = useCallback(
    (path: string) => {
      // Clear any navigation highlight when clicking a folder
      setHighlightedPath(null);
      setExpandedFolders((prev) => {
        const isCurrentlyExpanded = prev[path];
        if (!isCurrentlyExpanded && !folderContents[path]) {
          // Load contents when expanding for the first time
          loadFolderContents(path);
        }
        return { ...prev, [path]: !isCurrentlyExpanded };
      });
    },
    [folderContents, loadFolderContents]
  );

  // View file
  const handleViewFile = useCallback(
    async (file: FileInfo) => {
      // Clear any navigation highlight when clicking a file
      setHighlightedPath(null);
      setPreviewFilePath(file.path);
      setIsLoadingPreview(true);
      setPreviewFile(null);
      setPreviewImageDimensions(null);
      // Clean up previous image URL
      setPreviewImageUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return undefined;
      });

      // Check if this is an image file
      const isImage = file.mime_type?.startsWith('image/');
      setPreviewIsImage(!!isImage);

      try {
        // For images, fetch as blob with auth headers
        if (isImage) {
          const params = new URLSearchParams({ path: file.path });
          const url = `${baseUrl}/api/v1/files/${sessionId}/download?${params.toString()}`;
          const response = await fetch(url, {
            headers: { 'Authorization': `Bearer ${token}` },
          });
          if (!response.ok) {
            throw new Error(`Failed to load image: ${response.statusText}`);
          }
          const blob = await response.blob();
          const objectUrl = URL.createObjectURL(blob);
          setPreviewImageUrl(objectUrl);
          // Set file data for the viewer
          setPreviewFile({
            path: file.path,
            name: file.name,
            mimeType: file.mime_type || 'image/unknown',
            size: file.size,
            content: null,
            isBinary: true,
            isTruncated: false,
          });
        } else {
          const content = await getFileContent(baseUrl, token, sessionId, file.path);
          // Convert API response to FileViewerData
          setPreviewFile(toFileViewerData(content));
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to load file';
        setPreviewFile({
          path: file.path,
          name: file.name,
          mimeType: file.mime_type || 'unknown',
          size: file.size,
          content: null,
          isBinary: false,
          isTruncated: false,
          error: message,
        });
        setPreviewIsImage(false);
      } finally {
        setIsLoadingPreview(false);
      }
    },
    [baseUrl, token, sessionId]
  );

  // Download file
  const handleDownloadFile = useCallback(
    async (file: FileInfo) => {
      // Clear any navigation highlight when clicking download
      setHighlightedPath(null);
      try {
        await downloadFile(baseUrl, token, sessionId, file.path);
      } catch (err) {
        console.error('Download failed:', err);
      }
    },
    [baseUrl, token, sessionId]
  );

  // Initiate delete
  const handleDeleteFile = useCallback((file: FileInfo) => {
    setDeleteTarget(file);
  }, []);

  // Confirm delete
  const handleConfirmDelete = useCallback(async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);

    try {
      await deleteFile(baseUrl, token, sessionId, deleteTarget.path);

      // Remove from local state
      const parentPath = deleteTarget.path.includes('/')
        ? deleteTarget.path.substring(0, deleteTarget.path.lastIndexOf('/'))
        : '';

      if (parentPath) {
        // Remove from folder contents
        setFolderContents((prev) => ({
          ...prev,
          [parentPath]: prev[parentPath]?.filter((f) => f.path !== deleteTarget.path) || [],
        }));
      } else {
        // Remove from root files
        setFiles((prev) => prev.filter((f) => f.path !== deleteTarget.path));
      }

      setDeleteTarget(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete file';
      onErrorRef.current?.(message);
    } finally {
      setIsDeleting(false);
    }
  }, [baseUrl, token, sessionId, deleteTarget]);

  // Cancel delete
  const handleCancelDelete = useCallback(() => {
    setDeleteTarget(null);
  }, []);

  // Close preview
  const handleClosePreview = useCallback(() => {
    setPreviewFile(null);
    setPreviewFilePath(null);
    setPreviewIsImage(false);
    setPreviewImageDimensions(null);
    // Clean up image URL
    setPreviewImageUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return undefined;
    });
  }, []);

  // Download from preview
  const handleDownloadFromPreview = useCallback(async () => {
    if (previewFilePath) {
      try {
        await downloadFile(baseUrl, token, sessionId, previewFilePath);
      } catch (err) {
        console.error('Download failed:', err);
      }
    }
  }, [baseUrl, token, sessionId, previewFilePath]);

  // Handle sort change
  const handleSortChange = useCallback((field: FileSortField) => {
    setSortBy((prevField) => {
      if (prevField === field) {
        // Toggle order if same field
        setSortOrder((prevOrder) => (prevOrder === 'asc' ? 'desc' : 'asc'));
        return field;
      }
      // Reset to desc for new field (except name which is asc)
      setSortOrder(field === 'name' ? 'asc' : 'desc');
      return field;
    });
  }, []);

  // Handle file upload
  const handleUpload = useCallback(
    async (filesToUpload: File[], targetPath: string = '') => {
      if (filesToUpload.length === 0) return;

      const fileCount = filesToUpload.length;
      const fileLabel = fileCount === 1 ? 'file' : 'files';

      // Clear any existing notification timeout
      if (notificationTimeoutRef.current) {
        clearTimeout(notificationTimeoutRef.current);
      }

      // Show uploading notification
      setUploadNotification({
        type: 'uploading',
        message: `Uploading ${fileCount} ${fileLabel}...`,
        fileCount,
      });

      setIsUploading(true);
      try {
        const result = await uploadFiles(baseUrl, token, sessionId, filesToUpload, targetPath);

        // Report errors if any
        if (result.errors.length > 0) {
          onErrorRef.current?.(result.errors.join(', '));
          setUploadNotification({
            type: 'error',
            message: `Upload failed: ${result.errors[0]}`,
            fileCount,
          });
        } else {
          // Show success notification
          setUploadNotification({
            type: 'success',
            message: `${fileCount} ${fileLabel} uploaded successfully`,
            fileCount,
          });
        }

        // Refresh the file listing to show new files
        if (targetPath) {
          // Refresh the specific folder
          const listing = await browseFiles(baseUrl, token, sessionId, targetPath, {
            includeHidden: showHiddenFiles,
            sortBy,
            sortOrder,
          });
          setFolderContents((prev) => ({ ...prev, [targetPath]: listing.files }));
        } else {
          // Refresh root
          await loadRootFiles();
        }

        // Auto-dismiss notification after 3 seconds
        notificationTimeoutRef.current = setTimeout(() => {
          setUploadNotification(null);
        }, 3000);
      } catch (err) {
        console.error('Upload failed:', err);
        onErrorRef.current?.((err as Error).message);
        setUploadNotification({
          type: 'error',
          message: `Upload failed: ${(err as Error).message}`,
          fileCount,
        });
        // Auto-dismiss error after 5 seconds
        notificationTimeoutRef.current = setTimeout(() => {
          setUploadNotification(null);
        }, 5000);
      } finally {
        setIsUploading(false);
      }
    },
    [baseUrl, token, sessionId, showHiddenFiles, sortBy, sortOrder, loadRootFiles]
  );

  // Handle file input change
  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selectedFiles = Array.from(e.target.files || []);
      if (selectedFiles.length > 0) {
        handleUpload(selectedFiles);
      }
      // Reset input so same file can be selected again
      e.target.value = '';
    },
    [handleUpload]
  );

  // Drag and drop handlers for the file explorer area
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current += 1;
    if (e.dataTransfer.types.includes('Files')) {
      setIsDraggingOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) {
      setIsDraggingOver(false);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter.current = 0;
      setIsDraggingOver(false);

      const droppedFiles = Array.from(e.dataTransfer.files);
      if (droppedFiles.length > 0) {
        handleUpload(droppedFiles);
      }
    },
    [handleUpload]
  );

  // Refresh on mount and when sort changes
  useEffect(() => {
    loadRootFiles();
  }, [loadRootFiles]);

  // Render sorting header
  const SortButton = ({
    field,
    label,
  }: {
    field: FileSortField;
    label: string;
  }): JSX.Element => (
    <button
      type="button"
      className={`filter-button ${sortBy === field ? 'active' : ''}`}
      onClick={() => handleSortChange(field)}
      title={`Sort by ${label}`}
    >
      {sortBy === field
        ? `[${label} ${sortOrder === 'asc' ? '▲' : '▼'}]`
        : `[${label}]`}
    </button>
  );

  // Getter functions for tree node state lookup
  const getIsExpanded = useCallback(
    (path: string) => expandedFolders[path] || false,
    [expandedFolders]
  );

  const getIsLoading = useCallback(
    (path: string) => loadingFolders.has(path),
    [loadingFolders]
  );

  const getChildren = useCallback(
    (path: string) => folderContents[path],
    [folderContents]
  );

  const getIsHighlighted = useCallback(
    (path: string) => highlightedPath === path,
    [highlightedPath]
  );

  // Render recursive tree with expanded state
  const renderFileTree = (fileList: FileInfo[], depth: number = 0): JSX.Element[] => {
    return fileList.map((file) => (
      <FileTreeNode
        key={file.path}
        file={file}
        depth={depth}
        getIsExpanded={getIsExpanded}
        getIsLoading={getIsLoading}
        getChildren={getChildren}
        getIsHighlighted={getIsHighlighted}
        onToggle={handleToggleFolder}
        onView={handleViewFile}
        onDownload={handleDownloadFile}
        onDelete={handleDeleteFile}
        onFileNameInsert={onFileNameInsert}
      />
    ));
  };

  return (
    <div className={`file-explorer ${className}`}>
      <div className="file-explorer-header">
        <div className="file-explorer-title">
          <span className="file-explorer-icon"><FolderIcon /></span>
          <span>Files</span>
        </div>
        <div className="file-explorer-toolbar">
          <button
            type="button"
            className="file-upload-btn"
            onClick={() => uploadInputRef.current?.click()}
            title="Upload files"
            disabled={isLoading || isUploading}
          >
            <UploadIcon />
          </button>
          <button
            type="button"
            className="file-refresh-btn"
            onClick={refreshAll}
            title="Refresh"
            disabled={isLoading}
          >
            {ICONS.refresh}
          </button>
          <input
            ref={uploadInputRef}
            type="file"
            multiple
            style={{ display: 'none' }}
            onChange={handleFileInputChange}
          />
        </div>
      </div>

      <div className="file-explorer-sort">
        <span className="file-sort-label">Sort:</span>
        <SortButton field="name" label="Name" />
        <SortButton field="size" label="Size" />
        <SortButton field="modified_at" label="Date" />
      </div>

      <div
        className={`file-explorer-content ${isDraggingOver ? 'file-explorer-drag-over' : ''}`}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {isDraggingOver && (
          <div className="file-explorer-drop-overlay">
            <div className="file-explorer-drop-content">
              <UploadIcon />
              <span>Drop files here to upload</span>
            </div>
          </div>
        )}
        {/* Upload notification toast */}
        {uploadNotification && (
          <div className={`file-upload-toast file-upload-toast-${uploadNotification.type}`}>
            <span className="file-upload-toast-icon">
              {uploadNotification.type === 'uploading' && ICONS.spinner}
              {uploadNotification.type === 'success' && '✓'}
              {uploadNotification.type === 'error' && '✗'}
            </span>
            <span className="file-upload-toast-message">{uploadNotification.message}</span>
            {uploadNotification.type !== 'uploading' && (
              <button
                type="button"
                className="file-upload-toast-dismiss"
                onClick={() => setUploadNotification(null)}
                title="Dismiss"
              >
                ×
              </button>
            )}
          </div>
        )}
        {isLoading ? (
          <div className="file-explorer-loading">
            <span className="file-explorer-spinner">{ICONS.spinner}</span>
            Loading files...
          </div>
        ) : error ? (
          <div className="file-explorer-error">
            <span className="file-explorer-error-icon">{ICONS.warning}</span>
            {error}
          </div>
        ) : files.length === 0 ? (
          <div className="file-explorer-empty">
            <span className="file-explorer-empty-icon"><FolderIcon /></span>
            <span>No files in workspace</span>
            <span className="file-explorer-empty-hint">Drag files here or click upload</span>
          </div>
        ) : (
          <>
            {truncatedInfo?.truncated && (
              <div className="file-explorer-truncated-warning">
                <span className="file-explorer-warning-icon">{ICONS.warning}</span>
                <span>
                  Showing {truncatedInfo.shownCount.toLocaleString()} of {truncatedInfo.totalCount.toLocaleString()} files.
                  File limit exceeded.
                </span>
              </div>
            )}
            <div className="file-tree">{renderFileTree(files)}</div>
          </>
        )}
      </div>

      {/* Preview Modal - Use ImageViewerModal for images, FileViewerModal for others */}
      {previewIsImage && previewImageUrl ? (
        <ImageViewerModal
          imageUrl={previewImageUrl}
          fileName={previewFile?.name || ''}
          dimensions={previewImageDimensions}
          isOpen={!isLoadingPreview && previewFile !== null}
          onClose={handleClosePreview}
          onDownload={handleDownloadFromPreview}
        />
      ) : (
        <FileViewerModal
          file={previewFile}
          isLoading={isLoadingPreview}
          onClose={handleClosePreview}
          onDownload={handleDownloadFromPreview}
          isOpen={isLoadingPreview || previewFile !== null}
          imageUrl={previewImageUrl}
        />
      )}

      {/* Delete Confirmation Modal */}
      {deleteTarget && (
        <DeleteConfirmModal
          fileName={deleteTarget.name}
          isDeleting={isDeleting}
          onConfirm={handleConfirmDelete}
          onCancel={handleCancelDelete}
        />
      )}
    </div>
  );
}

export default FileExplorer;
