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
import React, { useCallback, useEffect, useState } from 'react';
import {
  browseFiles,
  deleteFile,
  getFileContent,
  getFileDownloadUrl,
} from './api';
import type {
  FileContentResponse,
  FileInfo,
  FileSortField,
  SortOrder,
} from './types';
import { FileViewerModal, toFileViewerData, type FileViewerData } from './FileViewer';

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
  if (file.is_directory) {
    return <FolderIcon />;
  }
  return <FileIcon label={getFileIconLabel(file)} />;
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
  onToggle: (path: string) => void;
  onView: (file: FileInfo) => void;
  onDownload: (file: FileInfo) => void;
  onDelete: (file: FileInfo) => void;
}

function FileTreeNode({
  file,
  depth,
  getIsExpanded,
  getIsLoading,
  getChildren,
  onToggle,
  onView,
  onDownload,
  onDelete,
}: FileTreeNodeProps): JSX.Element {
  const isExpanded = file.is_directory ? getIsExpanded(file.path) : false;
  const isLoading = file.is_directory ? getIsLoading(file.path) : false;
  const children = file.is_directory ? getChildren(file.path) : undefined;
  const handleClick = () => {
    if (file.is_directory) {
      onToggle(file.path);
    } else if (file.is_viewable) {
      onView(file);
    } else {
      onDownload(file);
    }
  };

  return (
    <div className="file-tree-node">
      <div
        className={`file-tree-row ${file.is_directory ? 'file-tree-folder' : 'file-tree-file'}`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleClick}
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
        <span className="file-tree-date">{formatDateTime(file.modified_at)}</span>
        <span className="file-tree-actions" onClick={(e) => e.stopPropagation()}>
          {!file.is_directory && (
            <>
              {file.is_viewable && (
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
                onToggle={onToggle}
                onView={onView}
                onDownload={onDownload}
                onDelete={onDelete}
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

  // Preview modal state (using FileViewer)
  const [previewFile, setPreviewFile] = useState<FileViewerData | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [previewFilePath, setPreviewFilePath] = useState<string | null>(null);

  // Delete modal state
  const [deleteTarget, setDeleteTarget] = useState<FileInfo | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Notify parent when modal state changes (for ESC key handling)
  useEffect(() => {
    const isModalOpen = previewFile !== null || isLoadingPreview || deleteTarget !== null;
    onModalStateChange?.(isModalOpen);
  }, [previewFile, isLoadingPreview, deleteTarget, onModalStateChange]);

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
      onError?.(message);
    } finally {
      setIsLoading(false);
    }
  }, [baseUrl, token, sessionId, showHiddenFiles, sortBy, sortOrder, onError]);

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
        onError?.(message);
      } finally {
        setLoadingFolders((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        });
      }
    },
    [baseUrl, token, sessionId, showHiddenFiles, sortBy, sortOrder, onError]
  );

  // Toggle folder expansion
  const handleToggleFolder = useCallback(
    (path: string) => {
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
      setPreviewFilePath(file.path);
      setIsLoadingPreview(true);
      setPreviewFile(null);

      try {
        const content = await getFileContent(baseUrl, token, sessionId, file.path);
        // Convert API response to FileViewerData
        setPreviewFile(toFileViewerData(content));
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
      } finally {
        setIsLoadingPreview(false);
      }
    },
    [baseUrl, token, sessionId]
  );

  // Download file
  const handleDownloadFile = useCallback(
    (file: FileInfo) => {
      const url = getFileDownloadUrl(baseUrl, token, sessionId, file.path);
      // Open in new tab with token in URL for auth
      const downloadUrl = `${url}&token=${encodeURIComponent(token)}`;
      window.open(downloadUrl, '_blank');
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
      onError?.(message);
    } finally {
      setIsDeleting(false);
    }
  }, [baseUrl, token, sessionId, deleteTarget, onError]);

  // Cancel delete
  const handleCancelDelete = useCallback(() => {
    setDeleteTarget(null);
  }, []);

  // Close preview
  const handleClosePreview = useCallback(() => {
    setPreviewFile(null);
    setPreviewFilePath(null);
  }, []);

  // Download from preview
  const handleDownloadFromPreview = useCallback(() => {
    if (previewFilePath) {
      const url = getFileDownloadUrl(baseUrl, token, sessionId, previewFilePath);
      const downloadUrl = `${url}&token=${encodeURIComponent(token)}`;
      window.open(downloadUrl, '_blank');
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
        onToggle={handleToggleFolder}
        onView={handleViewFile}
        onDownload={handleDownloadFile}
        onDelete={handleDeleteFile}
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
            className="file-refresh-btn"
            onClick={loadRootFiles}
            title="Refresh"
            disabled={isLoading}
          >
            {ICONS.refresh}
          </button>
        </div>
      </div>

      <div className="file-explorer-sort">
        <span className="file-sort-label">Sort:</span>
        <SortButton field="name" label="Name" />
        <SortButton field="size" label="Size" />
        <SortButton field="modified_at" label="Date" />
      </div>

      <div className="file-explorer-content">
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

      {/* Preview Modal - Using FileViewer */}
      <FileViewerModal
        file={previewFile}
        isLoading={isLoadingPreview}
        onClose={handleClosePreview}
        onDownload={handleDownloadFromPreview}
        isOpen={isLoadingPreview || previewFile !== null}
      />

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
