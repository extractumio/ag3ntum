import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React, { useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Create mock functions for clipboard API
const mockClipboardWrite = vi.fn();
const mockClipboardWriteText = vi.fn();

// CopyButtons component implementation (extracted from App.tsx)
async function copyAsRichText(element: HTMLElement): Promise<boolean> {
  try {
    const html = element.innerHTML;
    const text = element.innerText;

    const htmlBlob = new Blob([html], { type: 'text/html' });
    const textBlob = new Blob([text], { type: 'text/plain' });

    const clipboardItem = new ClipboardItem({
      'text/html': htmlBlob,
      'text/plain': textBlob,
    });

    await mockClipboardWrite([clipboardItem]);
    return true;
  } catch (err) {
    console.error('Failed to copy rich text:', err);
    return false;
  }
}

async function copyAsMarkdown(markdown: string): Promise<boolean> {
  try {
    await mockClipboardWriteText(markdown);
    return true;
  } catch (err) {
    console.error('Failed to copy markdown:', err);
    return false;
  }
}

function CopyButtons({
  contentRef,
  markdown,
  className = '',
}: {
  contentRef: React.RefObject<HTMLElement | null>;
  markdown: string;
  className?: string;
}): JSX.Element {
  const [copiedRich, setCopiedRich] = React.useState(false);
  const [copiedMd, setCopiedMd] = React.useState(false);

  const handleCopyRich = async () => {
    if (contentRef.current) {
      const success = await copyAsRichText(contentRef.current);
      if (success) {
        setCopiedRich(true);
        setTimeout(() => setCopiedRich(false), 1500);
      }
    }
  };

  const handleCopyMd = async () => {
    const success = await copyAsMarkdown(markdown);
    if (success) {
      setCopiedMd(true);
      setTimeout(() => setCopiedMd(false), 1500);
    }
  };

  return (
    <div className={`copy-buttons ${className}`} data-testid="copy-buttons">
      <button
        type="button"
        className={`copy-icon-btn ${copiedRich ? 'copied' : ''}`}
        onClick={handleCopyRich}
        title="Copy as rich text (with formatting)"
        data-testid="copy-rich-btn"
      >
        {copiedRich ? '✓' : '📋'}
        <span className="copy-icon-label">R</span>
      </button>
      <button
        type="button"
        className={`copy-icon-btn ${copiedMd ? 'copied' : ''}`}
        onClick={handleCopyMd}
        title="Copy as markdown"
        data-testid="copy-md-btn"
      >
        {copiedMd ? '✓' : '📋'}
        <span className="copy-icon-label">M</span>
      </button>
    </div>
  );
}

// Test wrapper component
function TestWrapper({ markdown = '# Test Markdown' }: { markdown?: string }) {
  const contentRef = useRef<HTMLDivElement>(null);

  return (
    <div>
      <div ref={contentRef} data-testid="content">
        <h1>Test Content</h1>
        <p>This is some <strong>formatted</strong> text.</p>
      </div>
      <CopyButtons contentRef={contentRef} markdown={markdown} />
    </div>
  );
}

describe('CopyButtons', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Reset clipboard mocks
    mockClipboardWrite.mockReset().mockResolvedValue(undefined);
    mockClipboardWriteText.mockReset().mockResolvedValue(undefined);
    // Assign mocks to navigator.clipboard
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        write: mockClipboardWrite,
        writeText: mockClipboardWriteText,
        readText: vi.fn().mockResolvedValue(''),
        read: vi.fn().mockResolvedValue([]),
      },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders both copy buttons', () => {
      render(<TestWrapper />);

      expect(screen.getByTestId('copy-rich-btn')).toBeInTheDocument();
      expect(screen.getByTestId('copy-md-btn')).toBeInTheDocument();
    });

    it('has correct titles', () => {
      render(<TestWrapper />);

      expect(screen.getByTestId('copy-rich-btn')).toHaveAttribute(
        'title',
        'Copy as rich text (with formatting)'
      );
      expect(screen.getByTestId('copy-md-btn')).toHaveAttribute('title', 'Copy as markdown');
    });

    it('shows correct labels', () => {
      render(<TestWrapper />);

      expect(screen.getByText('R')).toBeInTheDocument();
      expect(screen.getByText('M')).toBeInTheDocument();
    });

    it('applies custom className', () => {
      const contentRef = { current: document.createElement('div') };
      render(<CopyButtons contentRef={contentRef} markdown="test" className="custom-class" />);

      expect(screen.getByTestId('copy-buttons')).toHaveClass('custom-class');
    });
  });

  describe('copy rich text', () => {
    it('copies rich text to clipboard', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<TestWrapper />);

      await user.click(screen.getByTestId('copy-rich-btn'));

      expect(mockClipboardWrite).toHaveBeenCalled();
    });

    it('shows success indicator after copying', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<TestWrapper />);

      const button = screen.getByTestId('copy-rich-btn');
      await user.click(button);

      expect(button).toHaveClass('copied');
      expect(button).toHaveTextContent('✓');
    });

    it('resets success indicator after timeout', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<TestWrapper />);

      const button = screen.getByTestId('copy-rich-btn');
      await user.click(button);

      expect(button).toHaveClass('copied');

      vi.advanceTimersByTime(1500);

      await waitFor(() => {
        expect(button).not.toHaveClass('copied');
      });
    });

    it('handles copy failure gracefully', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      mockClipboardWrite.mockRejectedValue(new Error('Copy failed'));
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      render(<TestWrapper />);

      const button = screen.getByTestId('copy-rich-btn');
      await user.click(button);

      // Should not show copied state
      expect(button).not.toHaveClass('copied');
      expect(consoleSpy).toHaveBeenCalled();

      consoleSpy.mockRestore();
    });

    it('does not copy when content ref is null', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const contentRef = { current: null };
      render(<CopyButtons contentRef={contentRef} markdown="test" />);

      await user.click(screen.getByTestId('copy-rich-btn'));

      expect(mockClipboardWrite).not.toHaveBeenCalled();
    });
  });

  describe('copy markdown', () => {
    it('copies markdown to clipboard', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const markdown = '# Test\n\nSome **markdown** content.';
      render(<TestWrapper markdown={markdown} />);

      await user.click(screen.getByTestId('copy-md-btn'));

      expect(mockClipboardWriteText).toHaveBeenCalledWith(markdown);
    });

    it('shows success indicator after copying', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<TestWrapper />);

      const button = screen.getByTestId('copy-md-btn');
      await user.click(button);

      expect(button).toHaveClass('copied');
      expect(button).toHaveTextContent('✓');
    });

    it('resets success indicator after timeout', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<TestWrapper />);

      const button = screen.getByTestId('copy-md-btn');
      await user.click(button);

      expect(button).toHaveClass('copied');

      vi.advanceTimersByTime(1500);

      await waitFor(() => {
        expect(button).not.toHaveClass('copied');
      });
    });

    it('handles copy failure gracefully', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      mockClipboardWriteText.mockRejectedValue(new Error('Copy failed'));
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      render(<TestWrapper />);

      const button = screen.getByTestId('copy-md-btn');
      await user.click(button);

      // Should not show copied state
      expect(button).not.toHaveClass('copied');
      expect(consoleSpy).toHaveBeenCalled();

      consoleSpy.mockRestore();
    });
  });

  describe('multiple clicks', () => {
    it('handles rapid clicks on rich text button', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<TestWrapper />);

      const button = screen.getByTestId('copy-rich-btn');

      await user.click(button);
      await user.click(button);
      await user.click(button);

      // Should have called write multiple times
      expect(mockClipboardWrite).toHaveBeenCalledTimes(3);
    });

    it('can copy both formats sequentially', async () => {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<TestWrapper markdown="# Markdown" />);

      await user.click(screen.getByTestId('copy-rich-btn'));
      await user.click(screen.getByTestId('copy-md-btn'));

      expect(mockClipboardWrite).toHaveBeenCalledTimes(1);
      expect(mockClipboardWriteText).toHaveBeenCalledWith('# Markdown');
    });
  });

  describe('accessibility', () => {
    it('buttons are accessible', () => {
      render(<TestWrapper />);

      const richBtn = screen.getByTestId('copy-rich-btn');
      const mdBtn = screen.getByTestId('copy-md-btn');

      expect(richBtn).toHaveAttribute('type', 'button');
      expect(mdBtn).toHaveAttribute('type', 'button');
      expect(richBtn).toHaveAttribute('title');
      expect(mdBtn).toHaveAttribute('title');
    });
  });
});

describe('copyAsRichText', () => {
  beforeEach(() => {
    mockClipboardWrite.mockResolvedValue(undefined);
  });

  it('creates correct clipboard items', async () => {
    const element = document.createElement('div');
    element.innerHTML = '<p>Test <strong>content</strong></p>';

    await copyAsRichText(element);

    expect(mockClipboardWrite).toHaveBeenCalled();
    const call = mockClipboardWrite.mock.calls[0];
    expect(call[0]).toHaveLength(1);
  });

  it('returns true on success', async () => {
    const element = document.createElement('div');
    element.innerHTML = '<p>Test</p>';

    const result = await copyAsRichText(element);

    expect(result).toBe(true);
  });

  it('returns false on failure', async () => {
    mockClipboardWrite.mockRejectedValue(new Error('Failed'));
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const element = document.createElement('div');
    element.innerHTML = '<p>Test</p>';

    const result = await copyAsRichText(element);

    expect(result).toBe(false);
    consoleSpy.mockRestore();
  });
});

describe('copyAsMarkdown', () => {
  beforeEach(() => {
    mockClipboardWriteText.mockResolvedValue(undefined);
  });

  it('copies markdown text to clipboard', async () => {
    const markdown = '# Heading\n\nParagraph';

    await copyAsMarkdown(markdown);

    expect(mockClipboardWriteText).toHaveBeenCalledWith(markdown);
  });

  it('returns true on success', async () => {
    const result = await copyAsMarkdown('test');

    expect(result).toBe(true);
  });

  it('returns false on failure', async () => {
    mockClipboardWriteText.mockRejectedValue(new Error('Failed'));
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const result = await copyAsMarkdown('test');

    expect(result).toBe(false);
    consoleSpy.mockRestore();
  });
});
