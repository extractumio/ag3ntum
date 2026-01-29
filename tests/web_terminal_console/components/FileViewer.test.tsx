/**
 * Tests for FileViewer component
 *
 * Tests the file viewing functionality including:
 * - File content rendering
 * - Syntax highlighting
 * - Markdown rendering
 * - Image viewing
 * - Copy to clipboard
 * - Modal behavior
 * - toFileViewerData conversion
 * - File type detection
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  FileViewer,
  FileViewerModal,
  ImageViewerModal,
  toFileViewerData,
  isSupportedTextFile,
  isSupportedImageFile,
  type FileViewerData,
} from '../../../src/web_terminal_client/src/FileViewer';

// Mock highlight.js
vi.mock('highlight.js', () => ({
  default: {
    highlightElement: vi.fn(),
  },
}));

// Mock MarkdownRenderer
vi.mock('../../../src/web_terminal_client/src/MarkdownRenderer', () => ({
  renderMarkdown: vi.fn((content: string) => <div data-testid="rendered-markdown">{content}</div>),
}));

describe('FileViewer', () => {
  // ==========================================================================
  // FileViewerData Type
  // ==========================================================================
  describe('toFileViewerData', () => {
    it('converts API response to FileViewerData', () => {
      const apiResponse = {
        path: 'test/file.txt',
        name: 'file.txt',
        mime_type: 'text/plain',
        size: 1024,
        content: 'Hello, World!',
        is_binary: false,
        is_truncated: false,
        error: null,
      };

      const result = toFileViewerData(apiResponse);

      expect(result).toEqual({
        name: 'file.txt',
        path: 'test/file.txt',
        content: 'Hello, World!',
        mimeType: 'text/plain',
        size: 1024,
        isBinary: false,
        isTruncated: false,
        error: undefined,
      });
    });

    it('handles null content', () => {
      const apiResponse = {
        path: 'file.bin',
        name: 'file.bin',
        mime_type: 'application/octet-stream',
        size: 2048,
        content: null,
        is_binary: true,
        is_truncated: false,
        error: null,
      };

      const result = toFileViewerData(apiResponse);

      expect(result.content).toBeNull();
      expect(result.isBinary).toBe(true);
    });

    it('handles error in response', () => {
      const apiResponse = {
        path: 'missing.txt',
        name: 'missing.txt',
        mime_type: 'text/plain',
        size: 0,
        content: null,
        is_binary: false,
        is_truncated: false,
        error: 'File not found',
      };

      const result = toFileViewerData(apiResponse);

      expect(result.error).toBe('File not found');
    });

    it('handles truncated files', () => {
      const apiResponse = {
        path: 'large.log',
        name: 'large.log',
        mime_type: 'text/plain',
        size: 10000000,
        content: 'partial content...',
        is_binary: false,
        is_truncated: true,
        error: null,
      };

      const result = toFileViewerData(apiResponse);

      expect(result.isTruncated).toBe(true);
    });
  });

  // ==========================================================================
  // isSupportedTextFile
  // ==========================================================================
  describe('isSupportedTextFile', () => {
    it('returns true for JavaScript files', () => {
      expect(isSupportedTextFile('script.js')).toBe(true);
      expect(isSupportedTextFile('app.jsx')).toBe(true);
    });

    it('returns true for TypeScript files', () => {
      expect(isSupportedTextFile('types.ts')).toBe(true);
      expect(isSupportedTextFile('component.tsx')).toBe(true);
    });

    it('returns true for Python files', () => {
      expect(isSupportedTextFile('main.py')).toBe(true);
    });

    it('returns true for markdown files', () => {
      expect(isSupportedTextFile('README.md')).toBe(true);
    });

    it('returns true for JSON and YAML', () => {
      expect(isSupportedTextFile('config.json')).toBe(true);
      expect(isSupportedTextFile('settings.yaml')).toBe(true);
      expect(isSupportedTextFile('data.yml')).toBe(true);
    });

    it('returns true for HTML/CSS', () => {
      expect(isSupportedTextFile('index.html')).toBe(true);
      expect(isSupportedTextFile('styles.css')).toBe(true);
    });

    it('returns true for plain text', () => {
      expect(isSupportedTextFile('notes.txt')).toBe(true);
    });

    it('returns false for binary files', () => {
      expect(isSupportedTextFile('image.png')).toBe(false);
      expect(isSupportedTextFile('document.pdf')).toBe(false);
      expect(isSupportedTextFile('archive.zip')).toBe(false);
    });

    it('handles paths with directories', () => {
      expect(isSupportedTextFile('src/components/App.tsx')).toBe(true);
      expect(isSupportedTextFile('/home/user/docs/README.md')).toBe(true);
    });

    it('handles case insensitivity', () => {
      expect(isSupportedTextFile('README.MD')).toBe(true);
      expect(isSupportedTextFile('script.JS')).toBe(true);
    });
  });

  // ==========================================================================
  // isSupportedImageFile
  // ==========================================================================
  describe('isSupportedImageFile', () => {
    it('returns true for common image formats', () => {
      expect(isSupportedImageFile('photo.jpg')).toBe(true);
      expect(isSupportedImageFile('photo.jpeg')).toBe(true);
      expect(isSupportedImageFile('icon.png')).toBe(true);
      expect(isSupportedImageFile('animation.gif')).toBe(true);
      expect(isSupportedImageFile('logo.svg')).toBe(true);
      expect(isSupportedImageFile('image.webp')).toBe(true);
    });

    it('returns false for non-image files', () => {
      expect(isSupportedImageFile('document.pdf')).toBe(false);
      expect(isSupportedImageFile('script.js')).toBe(false);
      expect(isSupportedImageFile('data.json')).toBe(false);
    });

    it('handles case insensitivity', () => {
      expect(isSupportedImageFile('PHOTO.JPG')).toBe(true);
      expect(isSupportedImageFile('Image.PNG')).toBe(true);
    });

    it('handles paths with directories', () => {
      expect(isSupportedImageFile('assets/images/logo.png')).toBe(true);
    });
  });

  // ==========================================================================
  // FileViewer Component
  // ==========================================================================
  describe('FileViewer Component', () => {
    const createFile = (overrides: Partial<FileViewerData> = {}): FileViewerData => ({
      name: 'test.txt',
      path: 'test.txt',
      content: 'File content here',
      mimeType: 'text/plain',
      size: 100,
      isBinary: false,
      isTruncated: false,
      ...overrides,
    });

    it('renders file content', () => {
      const file = createFile({ content: 'Hello, World!' });

      render(<FileViewer file={file} />);

      expect(screen.getByText('Hello, World!')).toBeInTheDocument();
    });

    it('shows loading state', () => {
      const { container } = render(<FileViewer file={null} isLoading={true} />);

      expect(container.querySelector('.file-viewer-loading')).toBeInTheDocument();
      expect(screen.getByText(/Loading file content/i)).toBeInTheDocument();
    });

    it('shows error message', () => {
      const file = createFile({ error: 'Failed to load file' });

      render(<FileViewer file={file} />);

      expect(screen.getByText(/failed to load file/i)).toBeInTheDocument();
    });

    it('shows binary file message', () => {
      const file = createFile({ isBinary: true, content: null });

      render(<FileViewer file={file} />);

      expect(screen.getByText(/binary.*cannot be previewed/i)).toBeInTheDocument();
    });

    it('shows truncation warning', () => {
      const file = createFile({ isTruncated: true });

      render(<FileViewer file={file} />);

      expect(screen.getByText(/truncated/i)).toBeInTheDocument();
    });

    it('displays file header with name', () => {
      const file = createFile({ name: 'important.txt' });

      render(<FileViewer file={file} showHeader={true} />);

      expect(screen.getByText('important.txt')).toBeInTheDocument();
    });

    it('displays file size in header', () => {
      const file = createFile({ size: 2048 });

      render(<FileViewer file={file} showHeader={true} />);

      expect(screen.getByText(/2.*kb/i)).toBeInTheDocument();
    });

    it('calls onClose when close button clicked', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      const file = createFile();

      const { container } = render(<FileViewer file={file} onClose={onClose} showActions={true} />);

      const closeButton = container.querySelector('button[title*="Close"]');
      expect(closeButton).toBeInTheDocument();
      await user.click(closeButton!);

      expect(onClose).toHaveBeenCalled();
    });

    it('calls onDownload when download button clicked', async () => {
      const user = userEvent.setup();
      const onDownload = vi.fn();
      const file = createFile();

      const { container } = render(<FileViewer file={file} onDownload={onDownload} showActions={true} />);

      const downloadButton = container.querySelector('button[title="Download"]');
      expect(downloadButton).toBeInTheDocument();
      await user.click(downloadButton!);

      expect(onDownload).toHaveBeenCalled();
    });

    it('applies custom className', () => {
      const file = createFile();

      const { container } = render(<FileViewer file={file} className="custom-viewer" />);

      expect(container.querySelector('.custom-viewer')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Markdown Rendering
  // ==========================================================================
  describe('Markdown Rendering', () => {
    it('renders markdown files with rendered view by default', () => {
      const file: FileViewerData = {
        name: 'README.md',
        path: 'README.md',
        content: '# Hello\n\nThis is markdown',
        mimeType: 'text/markdown',
        size: 100,
      };

      render(<FileViewer file={file} />);

      expect(screen.getByTestId('rendered-markdown')).toBeInTheDocument();
    });

    it('allows switching between rendered and source view for markdown', async () => {
      const user = userEvent.setup();
      const file: FileViewerData = {
        name: 'README.md',
        path: 'README.md',
        content: '# Hello',
        mimeType: 'text/markdown',
        size: 100,
      };

      render(<FileViewer file={file} />);

      // Find and click source toggle button
      const sourceButton = screen.getByTitle(/source/i);
      await user.click(sourceButton);

      // Should now show raw content
      expect(screen.getByText('# Hello')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Code Syntax Highlighting
  // ==========================================================================
  describe('Code Syntax Highlighting', () => {
    it('applies syntax highlighting to code files', async () => {
      const file: FileViewerData = {
        name: 'app.js',
        path: 'app.js',
        content: 'const x = 1;',
        mimeType: 'application/javascript',
        size: 100,
      };

      const { container } = render(<FileViewer file={file} />);

      // Should have code block
      const codeBlock = container.querySelector('pre code');
      expect(codeBlock).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // FileViewerModal
  // ==========================================================================
  describe('FileViewerModal', () => {
    const createFile = (overrides: Partial<FileViewerData> = {}): FileViewerData => ({
      name: 'test.txt',
      path: 'test.txt',
      content: 'Content',
      mimeType: 'text/plain',
      size: 100,
      ...overrides,
    });

    it('renders modal with overlay', () => {
      const file = createFile();

      const { container } = render(<FileViewerModal file={file} />);

      expect(container.querySelector('.file-viewer-overlay')).toBeInTheDocument();
    });

    it('closes on overlay click', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      const file = createFile();

      const { container } = render(<FileViewerModal file={file} onClose={onClose} />);

      const overlay = container.querySelector('.file-viewer-overlay');
      await user.click(overlay!);

      expect(onClose).toHaveBeenCalled();
    });

    it('does not close when clicking modal content', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      const file = createFile();

      const { container } = render(<FileViewerModal file={file} onClose={onClose} />);

      const modal = container.querySelector('.file-viewer-modal');
      await user.click(modal!);

      expect(onClose).not.toHaveBeenCalled();
    });

    it('closes on Escape key', async () => {
      const onClose = vi.fn();
      const file = createFile();

      render(<FileViewerModal file={file} onClose={onClose} />);

      fireEvent.keyDown(document, { key: 'Escape' });

      expect(onClose).toHaveBeenCalled();
    });

    it('renders modal content', () => {
      const file = createFile();

      const { container } = render(<FileViewerModal file={file} />);

      // Modal should contain file viewer content
      expect(container.querySelector('.file-viewer-modal')).toBeInTheDocument();
      expect(screen.getByText('test.txt')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Image Viewing
  // ==========================================================================
  describe('Image Viewing', () => {
    it('renders image when imageUrl provided', () => {
      const file: FileViewerData = {
        name: 'photo.png',
        path: 'photo.png',
        content: null,
        mimeType: 'image/png',
        size: 50000,
        isBinary: true,
      };

      render(<FileViewer file={file} imageUrl="blob:http://localhost/abc123" />);

      const img = screen.getByRole('img');
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute('src', 'blob:http://localhost/abc123');
    });
  });

  // ==========================================================================
  // Copy to Clipboard
  // ==========================================================================
  describe('Copy to Clipboard', () => {
    it('shows copy button for text files', () => {
      const file: FileViewerData = {
        name: 'script.py',
        path: 'script.py',
        content: 'print("hello")',
        mimeType: 'text/x-python',
        size: 100,
      };

      const { container } = render(<FileViewer file={file} />);

      const copyButton = container.querySelector('button[title*="Copy"]');
      expect(copyButton).toBeInTheDocument();
    });

    it('copies content to clipboard on click', async () => {
      const user = userEvent.setup();
      const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText');

      const file: FileViewerData = {
        name: 'script.py',
        path: 'script.py',
        content: 'print("hello")',
        mimeType: 'text/x-python',
        size: 100,
      };

      const { container } = render(<FileViewer file={file} />);

      const copyButton = container.querySelector('button[title*="Copy"]');
      await user.click(copyButton!);

      expect(writeTextSpy).toHaveBeenCalledWith('print("hello")');
    });
  });

  // ==========================================================================
  // Edge Cases
  // ==========================================================================
  describe('Edge Cases', () => {
    it('returns null when file is null and not loading', () => {
      const { container } = render(<FileViewer file={null} />);

      // Component returns null, so container should be empty (just a div wrapper)
      expect(container.querySelector('.file-viewer')).not.toBeInTheDocument();
    });

    it('handles empty content', () => {
      const file: FileViewerData = {
        name: 'empty.txt',
        path: 'empty.txt',
        content: '',
        mimeType: 'text/plain',
        size: 0,
      };

      render(<FileViewer file={file} />);

      // Should render without error
      expect(screen.getByText('empty.txt')).toBeInTheDocument();
    });

    it('handles very long file names', () => {
      const longName = 'a'.repeat(100) + '.txt';
      const file: FileViewerData = {
        name: longName,
        path: longName,
        content: 'content',
        mimeType: 'text/plain',
        size: 100,
      };

      render(<FileViewer file={file} showHeader={true} />);

      // Should truncate and display
      const header = screen.getByText(/\.txt/);
      expect(header).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // ImageViewerModal - Show in File Explorer
  // ==========================================================================
  describe('ImageViewerModal', () => {
    const defaultProps = {
      imageUrl: 'blob:http://localhost/test-image-123',
      fileName: 'test-image.png',
      isOpen: true,
      onClose: vi.fn(),
    };

    beforeEach(() => {
      vi.clearAllMocks();
    });

    it('renders modal when isOpen is true', () => {
      const { container } = render(<ImageViewerModal {...defaultProps} />);

      expect(container.querySelector('.image-viewer-modal-overlay')).toBeInTheDocument();
      expect(container.querySelector('.image-viewer-modal')).toBeInTheDocument();
    });

    it('does not render when isOpen is false', () => {
      const { container } = render(<ImageViewerModal {...defaultProps} isOpen={false} />);

      expect(container.querySelector('.image-viewer-modal-overlay')).not.toBeInTheDocument();
    });

    it('displays file name in header', () => {
      render(<ImageViewerModal {...defaultProps} fileName="my-photo.jpg" />);

      expect(screen.getByText('my-photo.jpg')).toBeInTheDocument();
    });

    it('displays image dimensions when provided', () => {
      render(<ImageViewerModal {...defaultProps} dimensions={{ width: 1024, height: 768 }} />);

      expect(screen.getByText('1024 x 768')).toBeInTheDocument();
    });

    it('renders the image with correct src', () => {
      render(<ImageViewerModal {...defaultProps} />);

      const img = screen.getByRole('img');
      expect(img).toHaveAttribute('src', 'blob:http://localhost/test-image-123');
    });

    it('closes on Escape key press', () => {
      const onClose = vi.fn();
      render(<ImageViewerModal {...defaultProps} onClose={onClose} />);

      fireEvent.keyDown(window, { key: 'Escape' });

      expect(onClose).toHaveBeenCalled();
    });

    it('closes when clicking overlay', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      const { container } = render(<ImageViewerModal {...defaultProps} onClose={onClose} />);

      const overlay = container.querySelector('.image-viewer-modal-overlay');
      await user.click(overlay!);

      expect(onClose).toHaveBeenCalled();
    });

    it('does not close when clicking modal content', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      const { container } = render(<ImageViewerModal {...defaultProps} onClose={onClose} />);

      const modal = container.querySelector('.image-viewer-modal');
      await user.click(modal!);

      expect(onClose).not.toHaveBeenCalled();
    });

    describe('Show in File Explorer button', () => {
      it('renders when onShowInExplorer is provided', () => {
        const onShowInExplorer = vi.fn();
        const { container } = render(
          <ImageViewerModal {...defaultProps} onShowInExplorer={onShowInExplorer} />
        );

        const explorerButton = container.querySelector('button[title="Show in File Explorer"]');
        expect(explorerButton).toBeInTheDocument();
      });

      it('does not render when onShowInExplorer is not provided', () => {
        const { container } = render(<ImageViewerModal {...defaultProps} />);

        const explorerButton = container.querySelector('button[title="Show in File Explorer"]');
        expect(explorerButton).not.toBeInTheDocument();
      });

      it('calls onShowInExplorer when clicked', async () => {
        const user = userEvent.setup();
        const onShowInExplorer = vi.fn();
        const { container } = render(
          <ImageViewerModal {...defaultProps} onShowInExplorer={onShowInExplorer} />
        );

        const explorerButton = container.querySelector('button[title="Show in File Explorer"]');
        await user.click(explorerButton!);

        expect(onShowInExplorer).toHaveBeenCalled();
      });

      it('closes modal after clicking Show in File Explorer', async () => {
        const user = userEvent.setup();
        const onShowInExplorer = vi.fn();
        const onClose = vi.fn();
        const { container } = render(
          <ImageViewerModal {...defaultProps} onShowInExplorer={onShowInExplorer} onClose={onClose} />
        );

        const explorerButton = container.querySelector('button[title="Show in File Explorer"]');
        await user.click(explorerButton!);

        expect(onClose).toHaveBeenCalled();
      });

      it('calls onShowInExplorer before closing modal', async () => {
        const user = userEvent.setup();
        const callOrder: string[] = [];
        const onShowInExplorer = vi.fn(() => callOrder.push('showInExplorer'));
        const onClose = vi.fn(() => callOrder.push('close'));

        const { container } = render(
          <ImageViewerModal {...defaultProps} onShowInExplorer={onShowInExplorer} onClose={onClose} />
        );

        const explorerButton = container.querySelector('button[title="Show in File Explorer"]');
        await user.click(explorerButton!);

        expect(callOrder).toEqual(['showInExplorer', 'close']);
      });

      it('stops event propagation to prevent overlay close handler', async () => {
        const user = userEvent.setup();
        const onShowInExplorer = vi.fn();
        const onClose = vi.fn();

        const { container } = render(
          <ImageViewerModal {...defaultProps} onShowInExplorer={onShowInExplorer} onClose={onClose} />
        );

        const explorerButton = container.querySelector('button[title="Show in File Explorer"]');
        await user.click(explorerButton!);

        // onClose should only be called once (from the button handler, not from overlay)
        expect(onClose).toHaveBeenCalledTimes(1);
      });
    });

    describe('Download button', () => {
      it('renders when onDownload is provided', () => {
        const onDownload = vi.fn();
        const { container } = render(
          <ImageViewerModal {...defaultProps} onDownload={onDownload} />
        );

        const downloadButton = container.querySelector('button[title="Download"]');
        expect(downloadButton).toBeInTheDocument();
      });

      it('does not render when onDownload is not provided', () => {
        const { container } = render(<ImageViewerModal {...defaultProps} />);

        const downloadButton = container.querySelector('button[title="Download"]');
        expect(downloadButton).not.toBeInTheDocument();
      });

      it('calls onDownload when clicked', async () => {
        const user = userEvent.setup();
        const onDownload = vi.fn();
        const { container } = render(
          <ImageViewerModal {...defaultProps} onDownload={onDownload} />
        );

        const downloadButton = container.querySelector('button[title="Download"]');
        await user.click(downloadButton!);

        expect(onDownload).toHaveBeenCalled();
      });
    });

    describe('Copy Image button', () => {
      it('renders copy button', () => {
        const { container } = render(<ImageViewerModal {...defaultProps} />);

        const copyButton = container.querySelector('button[title*="Copy"]');
        expect(copyButton).toBeInTheDocument();
      });

      it('shows "Copied!" state after successful copy', async () => {
        const user = userEvent.setup();

        // Mock fetch for blob copy
        global.fetch = vi.fn().mockResolvedValue({
          blob: () => Promise.resolve(new Blob(['test'], { type: 'image/png' })),
        });

        // Mock clipboard.write
        const clipboardWriteSpy = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator.clipboard, { write: clipboardWriteSpy });

        const { container } = render(<ImageViewerModal {...defaultProps} />);

        const copyButton = container.querySelector('button[title*="Copy"]');
        await user.click(copyButton!);

        await waitFor(() => {
          const copiedButton = container.querySelector('button[title="Copied!"]');
          expect(copiedButton).toBeInTheDocument();
        });
      });
    });
  });
});
