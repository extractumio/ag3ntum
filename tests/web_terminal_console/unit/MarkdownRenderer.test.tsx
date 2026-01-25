/**
 * Tests for MarkdownRenderer
 *
 * Tests the markdown parsing and rendering functionality including:
 * - Headers (h1-h4)
 * - Code blocks with language tags
 * - Inline code, bold, italic
 * - Links and images
 * - Tables
 * - Lists (ordered and unordered)
 * - Blockquotes
 * - Horizontal rules
 * - Security sanitization functions
 * - Ag3ntum custom tags
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  renderMarkdown,
  renderMarkdownElements,
  renderInlineMarkdown,
  stripAg3ntumTags,
} from '../../../src/web_terminal_client/src/MarkdownRenderer';

describe('MarkdownRenderer', () => {
  // ==========================================================================
  // Headers
  // ==========================================================================
  describe('Headers', () => {
    it('renders h1 headers', () => {
      const { container } = render(renderMarkdown('# Hello World'));
      const h1 = container.querySelector('h1');
      expect(h1).toBeInTheDocument();
      expect(h1).toHaveTextContent('Hello World');
      expect(h1).toHaveClass('md-h1');
    });

    it('renders h2 headers', () => {
      const { container } = render(renderMarkdown('## Section Title'));
      const h2 = container.querySelector('h2');
      expect(h2).toBeInTheDocument();
      expect(h2).toHaveTextContent('Section Title');
      expect(h2).toHaveClass('md-h2');
    });

    it('renders h3 headers', () => {
      const { container } = render(renderMarkdown('### Subsection'));
      const h3 = container.querySelector('h3');
      expect(h3).toBeInTheDocument();
      expect(h3).toHaveTextContent('Subsection');
      expect(h3).toHaveClass('md-h3');
    });

    it('renders h4 headers', () => {
      const { container } = render(renderMarkdown('#### Small Header'));
      const h4 = container.querySelector('h4');
      expect(h4).toBeInTheDocument();
      expect(h4).toHaveTextContent('Small Header');
      expect(h4).toHaveClass('md-h4');
    });

    it('renders headers with inline formatting', () => {
      const { container } = render(renderMarkdown('# Hello **Bold** World'));
      const h1 = container.querySelector('h1');
      expect(h1).toBeInTheDocument();
      const strong = h1?.querySelector('strong');
      expect(strong).toHaveTextContent('Bold');
    });
  });

  // ==========================================================================
  // Code Blocks
  // ==========================================================================
  describe('Code Blocks', () => {
    it('renders fenced code blocks', () => {
      const markdown = '```\nconst x = 1;\n```';
      const { container } = render(renderMarkdown(markdown));
      const pre = container.querySelector('pre');
      expect(pre).toBeInTheDocument();
      expect(pre).toHaveClass('md-code-block');
      const code = pre?.querySelector('code');
      expect(code).toHaveTextContent('const x = 1;');
    });

    it('renders code blocks with language tag', () => {
      const markdown = '```javascript\nconst x = 1;\n```';
      const { container } = render(renderMarkdown(markdown));
      const pre = container.querySelector('pre');
      expect(pre).toHaveAttribute('data-lang', 'javascript');
    });

    it('handles unclosed code blocks gracefully', () => {
      const markdown = '```python\nprint("hello")\n';
      const { container } = render(renderMarkdown(markdown));
      const pre = container.querySelector('pre');
      expect(pre).toBeInTheDocument();
      expect(pre).toHaveClass('md-code-block');
    });

    it('preserves whitespace in code blocks', () => {
      const markdown = '```\n  indented\n    more indented\n```';
      const { container } = render(renderMarkdown(markdown));
      const code = container.querySelector('code');
      expect(code?.textContent).toContain('  indented');
      expect(code?.textContent).toContain('    more indented');
    });
  });

  // ==========================================================================
  // Inline Formatting
  // ==========================================================================
  describe('Inline Formatting', () => {
    it('renders inline code', () => {
      const { container } = render(renderMarkdown('Use `const` for constants'));
      const code = container.querySelector('code.md-inline-code');
      expect(code).toBeInTheDocument();
      expect(code).toHaveTextContent('const');
    });

    it('renders bold text', () => {
      const { container } = render(renderMarkdown('This is **bold** text'));
      const strong = container.querySelector('strong.md-bold');
      expect(strong).toBeInTheDocument();
      expect(strong).toHaveTextContent('bold');
    });

    it('renders italic text', () => {
      const { container } = render(renderMarkdown('This is *italic* text'));
      const em = container.querySelector('em.md-italic');
      expect(em).toBeInTheDocument();
      expect(em).toHaveTextContent('italic');
    });

    it('renders links', () => {
      const { container } = render(renderMarkdown('[Click here](https://example.com)'));
      const link = container.querySelector('a.md-link');
      expect(link).toBeInTheDocument();
      expect(link).toHaveTextContent('Click here');
      expect(link).toHaveAttribute('href', 'https://example.com');
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    });

    it('renders images', () => {
      const { container } = render(renderMarkdown('![Alt text](https://example.com/image.png)'));
      const img = container.querySelector('img.md-image');
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute('src', 'https://example.com/image.png');
      expect(img).toHaveAttribute('alt', 'Alt text');
    });

    it('handles multiple inline elements in one line', () => {
      const { container } = render(renderMarkdown('**bold** and *italic* and `code`'));
      expect(container.querySelector('strong')).toHaveTextContent('bold');
      expect(container.querySelector('em')).toHaveTextContent('italic');
      expect(container.querySelector('code')).toHaveTextContent('code');
    });
  });

  // ==========================================================================
  // Lists
  // ==========================================================================
  describe('Lists', () => {
    it('renders unordered lists with dash', () => {
      const markdown = '- Item 1\n- Item 2\n- Item 3';
      const { container } = render(renderMarkdown(markdown));
      const items = container.querySelectorAll('.md-li');
      expect(items).toHaveLength(3);
      expect(items[0]).toHaveTextContent('Item 1');
      expect(items[1]).toHaveTextContent('Item 2');
      expect(items[2]).toHaveTextContent('Item 3');
    });

    it('renders unordered lists with asterisk', () => {
      const markdown = '* Item A\n* Item B';
      const { container } = render(renderMarkdown(markdown));
      const items = container.querySelectorAll('.md-li');
      expect(items).toHaveLength(2);
    });

    it('renders ordered lists', () => {
      const markdown = '1. First\n2. Second\n3. Third';
      const { container } = render(renderMarkdown(markdown));
      const items = container.querySelectorAll('.md-li');
      expect(items).toHaveLength(3);
      expect(items[0]).toHaveTextContent('1. First');
      expect(items[1]).toHaveTextContent('2. Second');
    });

    it('renders nested lists with indentation', () => {
      const markdown = '- Item 1\n  - Nested item';
      const { container } = render(renderMarkdown(markdown));
      const items = container.querySelectorAll('.md-li');
      expect(items).toHaveLength(2);
      // Check that nested item has margin-left style
      const nestedItem = items[1];
      expect(nestedItem).toHaveStyle({ marginLeft: '16px' });
    });
  });

  // ==========================================================================
  // Tables
  // ==========================================================================
  describe('Tables', () => {
    it('renders tables with headers', () => {
      const markdown = '| Header 1 | Header 2 |\n|---|---|\n| Cell 1 | Cell 2 |';
      const { container } = render(renderMarkdown(markdown));
      const table = container.querySelector('table.md-table');
      expect(table).toBeInTheDocument();

      const headers = table?.querySelectorAll('th');
      expect(headers).toHaveLength(2);
      expect(headers?.[0]).toHaveTextContent('Header 1');
      expect(headers?.[1]).toHaveTextContent('Header 2');

      const cells = table?.querySelectorAll('td');
      expect(cells).toHaveLength(2);
      expect(cells?.[0]).toHaveTextContent('Cell 1');
      expect(cells?.[1]).toHaveTextContent('Cell 2');
    });

    it('renders tables with multiple rows', () => {
      const markdown = '| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |';
      const { container } = render(renderMarkdown(markdown));
      const rows = container.querySelectorAll('tbody tr');
      expect(rows).toHaveLength(2);
    });

    it('renders inline formatting in table cells', () => {
      const markdown = '| Name | Description |\n|---|---|\n| **Bold** | *Italic* |';
      const { container } = render(renderMarkdown(markdown));
      expect(container.querySelector('td strong')).toHaveTextContent('Bold');
      expect(container.querySelector('td em')).toHaveTextContent('Italic');
    });
  });

  // ==========================================================================
  // Blockquotes
  // ==========================================================================
  describe('Blockquotes', () => {
    it('renders blockquotes', () => {
      const { container } = render(renderMarkdown('> This is a quote'));
      const blockquote = container.querySelector('blockquote.md-blockquote');
      expect(blockquote).toBeInTheDocument();
      expect(blockquote).toHaveTextContent('This is a quote');
    });

    it('renders blockquotes with inline formatting', () => {
      const { container } = render(renderMarkdown('> A **bold** quote'));
      const blockquote = container.querySelector('blockquote');
      expect(blockquote).toBeInTheDocument();
      expect(blockquote?.querySelector('strong')).toHaveTextContent('bold');
    });
  });

  // ==========================================================================
  // Horizontal Rules
  // ==========================================================================
  describe('Horizontal Rules', () => {
    it('renders horizontal rules with dashes', () => {
      const { container } = render(renderMarkdown('---'));
      const hr = container.querySelector('hr.md-hr');
      expect(hr).toBeInTheDocument();
    });

    it('renders horizontal rules with asterisks', () => {
      const { container } = render(renderMarkdown('***'));
      const hr = container.querySelector('hr.md-hr');
      expect(hr).toBeInTheDocument();
    });

    it('renders horizontal rules with underscores', () => {
      const { container } = render(renderMarkdown('___'));
      const hr = container.querySelector('hr.md-hr');
      expect(hr).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Empty Lines and Spacing
  // ==========================================================================
  describe('Empty Lines and Spacing', () => {
    it('renders spacers for empty lines', () => {
      const { container } = render(renderMarkdown('Line 1\n\nLine 2'));
      const spacers = container.querySelectorAll('.md-spacer');
      expect(spacers.length).toBeGreaterThan(0);
    });

    it('renders regular text in divs', () => {
      const { container } = render(renderMarkdown('Just some text'));
      const div = container.querySelector('.md-content > div');
      expect(div).toHaveTextContent('Just some text');
    });
  });

  // ==========================================================================
  // Render Options
  // ==========================================================================
  describe('Render Options', () => {
    it('uses custom class prefix', () => {
      const { container } = render(renderMarkdown('# Header', { classPrefix: 'custom' }));
      const h1 = container.querySelector('h1.custom-h1');
      expect(h1).toBeInTheDocument();
    });

    it('wraps in container by default', () => {
      const { container } = render(renderMarkdown('Text'));
      const wrapper = container.querySelector('.md-content');
      expect(wrapper).toBeInTheDocument();
    });

    it('uses custom container class', () => {
      const { container } = render(renderMarkdown('Text', { containerClass: 'my-class' }));
      const wrapper = container.querySelector('.my-class');
      expect(wrapper).toBeInTheDocument();
    });

    it('can render without container wrapper', () => {
      const { container } = render(renderMarkdown('# Header', { wrapInContainer: false }));
      const h1 = container.querySelector('h1');
      expect(h1).toBeInTheDocument();
      const wrapper = container.querySelector('.md-content');
      expect(wrapper).not.toBeInTheDocument();
    });
  });

  // ==========================================================================
  // renderMarkdownElements (returns array)
  // ==========================================================================
  describe('renderMarkdownElements', () => {
    it('returns an array of JSX elements', () => {
      const elements = renderMarkdownElements('# Header\n\nParagraph');
      expect(Array.isArray(elements)).toBe(true);
      expect(elements.length).toBeGreaterThan(0);
    });

    it('uses custom class prefix', () => {
      const elements = renderMarkdownElements('# Header', 'custom');
      const { container } = render(<>{elements}</>);
      expect(container.querySelector('h1.custom-h1')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // renderInlineMarkdown
  // ==========================================================================
  describe('renderInlineMarkdown', () => {
    it('returns plain text when no markdown', () => {
      const result = renderInlineMarkdown('plain text');
      expect(result).toBe('plain text');
    });

    it('renders bold inline', () => {
      const result = renderInlineMarkdown('**bold**');
      const { container } = render(<>{result}</>);
      expect(container.querySelector('strong')).toHaveTextContent('bold');
    });

    it('renders italic inline', () => {
      const result = renderInlineMarkdown('*italic*');
      const { container } = render(<>{result}</>);
      expect(container.querySelector('em')).toHaveTextContent('italic');
    });

    it('renders inline code', () => {
      const result = renderInlineMarkdown('`code`');
      const { container } = render(<>{result}</>);
      expect(container.querySelector('code')).toHaveTextContent('code');
    });

    it('renders links', () => {
      const result = renderInlineMarkdown('[text](url)');
      const { container } = render(<>{result}</>);
      const link = container.querySelector('a');
      expect(link).toHaveTextContent('text');
      expect(link).toHaveAttribute('href', 'url');
    });

    it('handles mixed inline elements', () => {
      const result = renderInlineMarkdown('**bold** and *italic*');
      const { container } = render(<>{result}</>);
      expect(container.querySelector('strong')).toBeInTheDocument();
      expect(container.querySelector('em')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // stripAg3ntumTags
  // ==========================================================================
  describe('stripAg3ntumTags', () => {
    it('strips ag3ntum-file tags', () => {
      const result = stripAg3ntumTags('Hello <ag3ntum-file>path/to/file.txt</ag3ntum-file> World');
      expect(result).toBe('Hello  World');
    });

    it('strips ag3ntum-image tags', () => {
      const result = stripAg3ntumTags('See <ag3ntum-image>image.png</ag3ntum-image> here');
      expect(result).toBe('See  here');
    });

    it('strips ag3ntum-attached-file tags', () => {
      const result = stripAg3ntumTags('Files: <ag3ntum-attached-file>file.pdf</ag3ntum-attached-file>');
      expect(result).toBe('Files: ');
    });

    it('strips multiple tags', () => {
      const result = stripAg3ntumTags(
        '<ag3ntum-file>a.txt</ag3ntum-file> and <ag3ntum-image>b.png</ag3ntum-image>'
      );
      expect(result).toBe(' and ');
    });

    it('returns original string when no tags', () => {
      const result = stripAg3ntumTags('No tags here');
      expect(result).toBe('No tags here');
    });
  });

  // ==========================================================================
  // Complex Documents
  // ==========================================================================
  describe('Complex Documents', () => {
    it('renders a complete markdown document', () => {
      const markdown = `# Main Title

This is a paragraph with **bold** and *italic* text.

## Code Section

\`\`\`javascript
function hello() {
  console.log("Hello");
}
\`\`\`

### List of Items

- Item 1
- Item 2
- Item 3

| Column A | Column B |
|----------|----------|
| Value 1  | Value 2  |

> A wise quote

---

The end.`;

      const { container } = render(renderMarkdown(markdown));

      expect(container.querySelector('h1')).toBeInTheDocument();
      expect(container.querySelector('h2')).toBeInTheDocument();
      expect(container.querySelector('h3')).toBeInTheDocument();
      expect(container.querySelector('strong')).toBeInTheDocument();
      expect(container.querySelector('em')).toBeInTheDocument();
      expect(container.querySelector('pre')).toBeInTheDocument();
      expect(container.querySelectorAll('.md-li').length).toBe(3);
      expect(container.querySelector('table')).toBeInTheDocument();
      expect(container.querySelector('blockquote')).toBeInTheDocument();
      expect(container.querySelector('hr')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // Edge Cases
  // ==========================================================================
  describe('Edge Cases', () => {
    it('handles empty string', () => {
      const { container } = render(renderMarkdown(''));
      expect(container.querySelector('.md-content')).toBeInTheDocument();
    });

    it('handles only whitespace', () => {
      const { container } = render(renderMarkdown('   \n   \n   '));
      expect(container.querySelector('.md-content')).toBeInTheDocument();
    });

    it('handles special characters in text', () => {
      const { container } = render(renderMarkdown('Price: $100 & Tax: 10%'));
      expect(container.textContent).toContain('$100');
      expect(container.textContent).toContain('&');
      expect(container.textContent).toContain('10%');
    });

    it('handles URLs in text without link syntax', () => {
      const { container } = render(renderMarkdown('Visit https://example.com today'));
      // Without link syntax, URL should be plain text
      expect(container.textContent).toContain('https://example.com');
    });

    it('handles nested formatting attempts', () => {
      // Bold inside italic or vice versa
      const { container } = render(renderMarkdown('*outer **inner** outer*'));
      expect(container.querySelector('em')).toBeInTheDocument();
    });
  });
});
