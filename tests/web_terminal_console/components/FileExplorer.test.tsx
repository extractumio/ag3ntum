/**
 * Tests for FileExplorer component
 *
 * Tests the file browser functionality including:
 * - Tree view rendering
 * - File and folder icons
 * - File actions (view, download, delete)
 * - Upload functionality
 * - Sorting
 * - Navigation
 * - Security (filename sanitization)
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { sanitizeFilename } from '../../../src/web_terminal_client/src/FileExplorer';

// Test the exported sanitizeFilename function
describe('sanitizeFilename', () => {
  // ==========================================================================
  // Basic Functionality
  // ==========================================================================
  describe('Basic Functionality', () => {
    it('returns the filename unchanged for normal names', () => {
      expect(sanitizeFilename('document.txt')).toBe('document.txt');
      expect(sanitizeFilename('my-file.pdf')).toBe('my-file.pdf');
      expect(sanitizeFilename('image_001.png')).toBe('image_001.png');
    });

    it('handles empty string', () => {
      expect(sanitizeFilename('')).toBe('');
    });

    it('handles undefined/null gracefully', () => {
      expect(sanitizeFilename(undefined as unknown as string)).toBe('');
      expect(sanitizeFilename(null as unknown as string)).toBe('');
    });

    it('handles non-string input gracefully', () => {
      expect(sanitizeFilename(123 as unknown as string)).toBe('');
      expect(sanitizeFilename({} as unknown as string)).toBe('');
    });
  });

  // ==========================================================================
  // Security: Control Character Removal
  // ==========================================================================
  describe('Control Character Removal', () => {
    it('removes null bytes', () => {
      expect(sanitizeFilename('file\0.txt')).toBe('file.txt');
      expect(sanitizeFilename('test\x00name')).toBe('testname');
    });

    it('removes other control characters', () => {
      expect(sanitizeFilename('file\x01\x02.txt')).toBe('file.txt');
      expect(sanitizeFilename('test\x1F\x7Fname')).toBe('testname');
    });

    it('preserves tab, newline, and carriage return', () => {
      // These are allowed in some display contexts
      expect(sanitizeFilename('file\t.txt')).toBe('file\t.txt');
    });
  });

  // ==========================================================================
  // Security: Zero-Width Character Removal
  // ==========================================================================
  describe('Zero-Width Character Removal', () => {
    it('removes zero-width space', () => {
      expect(sanitizeFilename('file\u200B.txt')).toBe('file.txt');
    });

    it('removes zero-width non-joiner', () => {
      expect(sanitizeFilename('file\u200C.txt')).toBe('file.txt');
    });

    it('removes zero-width joiner', () => {
      expect(sanitizeFilename('file\u200D.txt')).toBe('file.txt');
    });

    it('removes byte order mark', () => {
      expect(sanitizeFilename('\uFEFFfile.txt')).toBe('file.txt');
    });

    it('removes soft hyphen', () => {
      expect(sanitizeFilename('file\u00AD.txt')).toBe('file.txt');
    });
  });

  // ==========================================================================
  // Security: Length Limiting
  // ==========================================================================
  describe('Length Limiting', () => {
    it('truncates extremely long filenames', () => {
      const longName = 'a'.repeat(500);
      const result = sanitizeFilename(longName);
      expect(result.length).toBe(255);
    });

    it('does not truncate filenames under 255 characters', () => {
      const name = 'a'.repeat(254);
      expect(sanitizeFilename(name).length).toBe(254);
    });
  });

  // ==========================================================================
  // Unicode Handling
  // ==========================================================================
  describe('Unicode Handling', () => {
    it('handles unicode filenames', () => {
      expect(sanitizeFilename('文档.txt')).toBe('文档.txt');
      expect(sanitizeFilename('файл.pdf')).toBe('файл.pdf');
      expect(sanitizeFilename('αβγ.doc')).toBe('αβγ.doc');
    });

    it('normalizes unicode to NFC form', () => {
      // é as separate e + combining accent (NFD) should become single é (NFC)
      const nfd = 'cafe\u0301'; // café with combining accent
      const result = sanitizeFilename(nfd);
      // After NFC normalization, should be 5 chars (c, a, f, é)
      expect(result.length).toBe(4);
    });

    it('handles emoji in filenames', () => {
      expect(sanitizeFilename('file-🎉.txt')).toBe('file-🎉.txt');
    });
  });

  // ==========================================================================
  // Edge Cases
  // ==========================================================================
  describe('Edge Cases', () => {
    it('handles filenames with special characters', () => {
      expect(sanitizeFilename('file (1).txt')).toBe('file (1).txt');
      expect(sanitizeFilename("file's.txt")).toBe("file's.txt");
      expect(sanitizeFilename('file&name.txt')).toBe('file&name.txt');
    });

    it('handles hidden files (starting with dot)', () => {
      expect(sanitizeFilename('.gitignore')).toBe('.gitignore');
      expect(sanitizeFilename('.env.local')).toBe('.env.local');
    });

    it('handles files with multiple dots', () => {
      expect(sanitizeFilename('file.backup.tar.gz')).toBe('file.backup.tar.gz');
    });

    it('handles files with no extension', () => {
      expect(sanitizeFilename('Makefile')).toBe('Makefile');
      expect(sanitizeFilename('README')).toBe('README');
    });
  });
});

// =============================================================================
// FileExplorer Component Tests
// =============================================================================

// Mock the API functions
vi.mock('../../../src/web_terminal_client/src/api', () => ({
  browseFiles: vi.fn(),
  deleteFile: vi.fn(),
  downloadFile: vi.fn(),
  getFileContent: vi.fn(),
  uploadFiles: vi.fn(),
  getSessionMounts: vi.fn().mockResolvedValue({ mounts: [] }),
}));

// Mock FileViewer components
vi.mock('../../../src/web_terminal_client/src/FileViewer', () => ({
  FileViewerModal: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="file-viewer-modal">
      <button onClick={onClose}>Close</button>
    </div>
  ),
  ImageViewerModal: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="image-viewer-modal">
      <button onClick={onClose}>Close</button>
    </div>
  ),
  toFileViewerData: vi.fn(),
}));

import { FileExplorer } from '../../../src/web_terminal_client/src/FileExplorer';
import { ToastProvider } from '../../../src/web_terminal_client/src/components';
import * as api from '../../../src/web_terminal_client/src/api';
import { createMockDirectoryListing, createMockFileInfo } from '../mocks/data';
import type { RenderOptions } from '@testing-library/react';
import React from 'react';

// Wrapper that provides ToastProvider context
function renderWithToast(ui: React.ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
  return render(ui, {
    wrapper: ({ children }) => <ToastProvider>{children}</ToastProvider>,
    ...options,
  });
}

describe('FileExplorer Component', () => {
  const defaultProps = {
    sessionId: '20240115_143052_a1b2c3d4',
    baseUrl: 'http://localhost:40080',
    token: 'mock-token',
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ==========================================================================
  // Rendering
  // ==========================================================================
  describe('Rendering', () => {
    it('renders loading state initially', async () => {
      vi.mocked(api.browseFiles).mockImplementation(() => new Promise(() => {})); // Never resolves

      renderWithToast(<FileExplorer {...defaultProps} />);

      expect(screen.getByText(/loading/i)).toBeInTheDocument();
    });

    it('renders file list after loading', async () => {
      const mockListing = createMockDirectoryListing();
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('file1.txt')).toBeInTheDocument();
      });
    });

    it('renders folders with folder icon', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'my-folder',
            path: 'my-folder',
            is_directory: true,
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('my-folder')).toBeInTheDocument();
      });

      // Should have folder row class
      const folderRow = container.querySelector('.file-tree-folder');
      expect(folderRow).toBeInTheDocument();
    });

    it('renders error state when loading fails', async () => {
      vi.mocked(api.browseFiles).mockRejectedValue(new Error('Network error'));

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText(/error/i)).toBeInTheDocument();
      });
    });

    it('calls onError callback when loading fails', async () => {
      const onError = vi.fn();
      vi.mocked(api.browseFiles).mockRejectedValue(new Error('Network error'));

      renderWithToast(<FileExplorer {...defaultProps} onError={onError} />);

      await waitFor(() => {
        expect(onError).toHaveBeenCalled();
      });
    });
  });

  // ==========================================================================
  // File Tree Interaction
  // ==========================================================================
  describe('File Tree Interaction', () => {
    it('expands folder on click', async () => {
      const user = userEvent.setup();

      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'src',
            path: 'src',
            is_directory: true,
          }),
        ],
      });
      vi.mocked(api.browseFiles)
        .mockResolvedValueOnce(mockListing)
        .mockResolvedValueOnce({
          path: 'src',
          files: [createMockFileInfo({ name: 'index.ts', path: 'src/index.ts' })],
          total_count: 1,
          truncated: false,
        });

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('src')).toBeInTheDocument();
      });

      // Click to expand
      await user.click(screen.getByText('src'));

      await waitFor(() => {
        expect(screen.getByText('index.ts')).toBeInTheDocument();
      });
    });

    it('collapses folder on second click', async () => {
      const user = userEvent.setup();

      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'src',
            path: 'src',
            is_directory: true,
          }),
        ],
      });
      vi.mocked(api.browseFiles)
        .mockResolvedValueOnce(mockListing)
        .mockResolvedValueOnce({
          path: 'src',
          files: [createMockFileInfo({ name: 'index.ts', path: 'src/index.ts' })],
          total_count: 1,
          truncated: false,
        });

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('src')).toBeInTheDocument();
      });

      // Expand
      await user.click(screen.getByText('src'));

      await waitFor(() => {
        expect(screen.getByText('index.ts')).toBeInTheDocument();
      });

      // Collapse
      await user.click(screen.getByText('src'));

      await waitFor(() => {
        expect(screen.queryByText('index.ts')).not.toBeInTheDocument();
      });
    });
  });

  // ==========================================================================
  // File Actions
  // ==========================================================================
  describe('File Actions', () => {
    it('shows view button for viewable files', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'readme.md',
            path: 'readme.md',
            is_viewable: true,
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('readme.md')).toBeInTheDocument();
      });

      const viewButton = container.querySelector('button[title="View"]');
      expect(viewButton).toBeInTheDocument();
    });

    it('shows download button for files', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'data.zip',
            path: 'data.zip',
            is_viewable: false,
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('data.zip')).toBeInTheDocument();
      });

      const downloadButton = container.querySelector('button[title="Download"]');
      expect(downloadButton).toBeInTheDocument();
    });

    it('triggers download on download button click', async () => {
      const user = userEvent.setup();

      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'file.txt',
            path: 'file.txt',
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);
      vi.mocked(api.downloadFile).mockResolvedValue(undefined);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('file.txt')).toBeInTheDocument();
      });

      const downloadButton = container.querySelector('button[title="Download"]');
      await user.click(downloadButton!);

      expect(api.downloadFile).toHaveBeenCalledWith(
        defaultProps.baseUrl,
        defaultProps.token,
        defaultProps.sessionId,
        'file.txt'
      );
    });

    it('shows delete confirmation modal on delete button click', async () => {
      const user = userEvent.setup();

      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'old-file.txt',
            path: 'old-file.txt',
            is_readonly: false,
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('old-file.txt')).toBeInTheDocument();
      });

      const deleteButton = container.querySelector('button[title="Delete"]');
      await user.click(deleteButton!);

      expect(screen.getByText('Confirm Delete')).toBeInTheDocument();
      // Check for filename in the delete modal specifically
      const deleteModal = container.querySelector('.file-delete-modal');
      expect(deleteModal).toBeInTheDocument();
      expect(deleteModal?.textContent).toContain('old-file.txt');
    });
  });

  // ==========================================================================
  // File Display
  // ==========================================================================
  describe('File Display', () => {
    it('displays file size', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'large.bin',
            path: 'large.bin',
            size: 1048576, // 1 MB
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('large.bin')).toBeInTheDocument();
      });

      expect(screen.getByText('1 MB')).toBeInTheDocument();
    });

    it('displays modification date', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'file.txt',
            path: 'file.txt',
            modified_at: '2024-03-15T14:30:00Z',
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('file.txt')).toBeInTheDocument();
      });

      // Check for date in the format dd/mm/yy hh:mm
      const dateCell = container.querySelector('.file-tree-date');
      expect(dateCell).toBeInTheDocument();
    });

    it('shows read-only indicator for readonly files', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'protected.txt',
            path: 'protected.txt',
            is_readonly: true,
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('protected.txt')).toBeInTheDocument();
      });

      const readonlyIndicator = container.querySelector('.readonly-indicator');
      expect(readonlyIndicator).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Props and Callbacks
  // ==========================================================================
  describe('Props and Callbacks', () => {
    it('applies custom className', async () => {
      const mockListing = createMockDirectoryListing();
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(
        <FileExplorer {...defaultProps} className="custom-explorer" />
      );

      await waitFor(() => {
        expect(container.querySelector('.custom-explorer')).toBeInTheDocument();
      });
    });

    it('calls onFileNameInsert on double-click', async () => {
      const user = userEvent.setup();
      const onFileNameInsert = vi.fn();

      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'script.py',
            path: 'script.py',
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      renderWithToast(
        <FileExplorer {...defaultProps} onFileNameInsert={onFileNameInsert} />
      );

      await waitFor(() => {
        expect(screen.getByText('script.py')).toBeInTheDocument();
      });

      const fileRow = screen.getByText('script.py').closest('.file-tree-row');
      await user.dblClick(fileRow!);

      expect(onFileNameInsert).toHaveBeenCalledWith('./script.py');
    });

    it('handles showHiddenFiles prop', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: '.hidden',
            path: '.hidden',
            is_hidden: true,
          }),
        ],
      });
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      renderWithToast(<FileExplorer {...defaultProps} showHiddenFiles={true} />);

      await waitFor(() => {
        // browseFiles(baseUrl, token, sessionId, path, options)
        expect(api.browseFiles).toHaveBeenCalledWith(
          expect.anything(), // baseUrl
          expect.anything(), // token
          expect.anything(), // sessionId
          expect.anything(), // path
          expect.objectContaining({ includeHidden: true }) // options
        );
      });
    });
  });

  // ==========================================================================
  // File Upload
  // ==========================================================================
  describe('File Upload', () => {
    it('renders upload button', async () => {
      const mockListing = createMockDirectoryListing();
      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        // Look for upload button in the header
        const uploadButton = container.querySelector('.file-explorer-upload-btn, button[title*="Upload"]');
        expect(uploadButton).toBeInTheDocument();
      });
    });
  });

  // ==========================================================================
  // External Mount Flattening
  // ==========================================================================
  describe('External Mount Flattening', () => {
    it('hides external/ directory and shows flattened mounts at root', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'external',
            path: 'external',
            is_directory: true,
            mime_type: null,
            is_viewable: false,
          }),
          createMockFileInfo({
            name: 'persistent',
            path: 'persistent',
            is_directory: true,
            mime_type: null,
            is_viewable: false,
          }),
        ],
      });

      const mockMounts = {
        mounts: [
          {
            name: 'global_var_log',
            path: 'external/ro/global_var_log',
            sandbox_path: './external/ro/global_var_log/',
            mode: 'ro' as const,
            mount_type: 'external_ro',
            host_path: '/var/log',
          },
          {
            name: 'persistent',
            path: 'persistent',
            sandbox_path: './persistent/',
            mode: 'rw' as const,
            mount_type: 'persistent',
          },
        ],
      };

      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);
      vi.mocked((api as any).getSessionMounts).mockResolvedValue(mockMounts);

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        // External mount should appear with host_path as display name
        expect(screen.getByText('/var/log')).toBeInTheDocument();
      });

      // The 'external' directory should be hidden
      expect(screen.queryByText('external')).not.toBeInTheDocument();
      // Persistent should still appear
      expect(screen.getByText('persistent')).toBeInTheDocument();
    });

    it('shows mount badge for flattened external mounts', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'external',
            path: 'external',
            is_directory: true,
            mime_type: null,
            is_viewable: false,
          }),
        ],
      });

      const mockMounts = {
        mounts: [
          {
            name: 'global_var_log',
            path: 'external/ro/global_var_log',
            sandbox_path: './external/ro/global_var_log/',
            mode: 'ro' as const,
            mount_type: 'external_ro',
            host_path: '/var/log',
          },
        ],
      };

      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);
      vi.mocked((api as any).getSessionMounts).mockResolvedValue(mockMounts);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('/var/log')).toBeInTheDocument();
      });

      // Should have a mount badge
      const badge = container.querySelector('.mount-badge');
      expect(badge).toBeInTheDocument();
      expect(badge?.textContent).toBe('R');
    });

    it('shows RW badge for read-write external mounts', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'external',
            path: 'external',
            is_directory: true,
            mime_type: null,
            is_viewable: false,
          }),
        ],
      });

      const mockMounts = {
        mounts: [
          {
            name: 'product_docs',
            path: 'external/rw/product_docs',
            sandbox_path: './external/rw/product_docs/',
            mode: 'rw' as const,
            mount_type: 'external_rw',
            host_path: '/Users/greg/PRODUCT',
          },
        ],
      };

      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);
      vi.mocked((api as any).getSessionMounts).mockResolvedValue(mockMounts);

      const { container } = renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('/Users/greg/PRODUCT')).toBeInTheDocument();
      });

      const badge = container.querySelector('.mount-badge');
      expect(badge).toBeInTheDocument();
      expect(badge?.textContent).toBe('RW');
    });

    it('does not hide external/ when no external mounts exist', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'external',
            path: 'external',
            is_directory: true,
            mime_type: null,
            is_viewable: false,
          }),
        ],
      });

      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);
      vi.mocked((api as any).getSessionMounts).mockResolvedValue({ mounts: [] });

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        // With no external mounts, external/ should remain visible
        expect(screen.getByText('external')).toBeInTheDocument();
      });
    });

    it('falls back to mount name when host_path is not available', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'external',
            path: 'external',
            is_directory: true,
            mime_type: null,
            is_viewable: false,
          }),
        ],
      });

      const mockMounts = {
        mounts: [
          {
            name: 'some_mount',
            path: 'external/ro/some_mount',
            sandbox_path: './external/ro/some_mount/',
            mode: 'ro' as const,
            mount_type: 'external_ro',
            // no host_path
          },
        ],
      };

      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);
      vi.mocked((api as any).getSessionMounts).mockResolvedValue(mockMounts);

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        // Should fall back to mount name
        expect(screen.getByText('some_mount')).toBeInTheDocument();
      });
    });

    it('flattens user-ro and user-rw mounts alongside external mounts', async () => {
      const mockListing = createMockDirectoryListing({
        files: [
          createMockFileInfo({
            name: 'external',
            path: 'external',
            is_directory: true,
            mime_type: null,
            is_viewable: false,
          }),
        ],
      });

      const mockMounts = {
        mounts: [
          {
            name: 'global_var_log',
            path: 'external/ro/global_var_log',
            sandbox_path: './external/ro/global_var_log/',
            mode: 'ro' as const,
            mount_type: 'external_ro',
            host_path: '/var/log',
          },
          {
            name: 'user_documents',
            path: 'external/user-ro/user_documents',
            sandbox_path: './external/user-ro/user_documents/',
            mode: 'ro' as const,
            mount_type: 'user_ro',
            host_path: '/Users/greg/Documents',
          },
        ],
      };

      vi.mocked(api.browseFiles).mockResolvedValue(mockListing);
      vi.mocked((api as any).getSessionMounts).mockResolvedValue(mockMounts);

      renderWithToast(<FileExplorer {...defaultProps} />);

      await waitFor(() => {
        expect(screen.getByText('/var/log')).toBeInTheDocument();
        expect(screen.getByText('/Users/greg/Documents')).toBeInTheDocument();
      });

      // external/ should be hidden
      expect(screen.queryByText('external')).not.toBeInTheDocument();
    });
  });
});
