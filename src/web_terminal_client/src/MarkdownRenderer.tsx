/**
 * Shared Markdown Renderer
 *
 * A unified markdown rendering utility used by both:
 * - FileViewer (for markdown file preview in File Explorer)
 * - Agent message output (for rendering agent responses)
 *
 * Features:
 * - Headers (h1-h4)
 * - Code blocks with language tags (triple backticks)
 * - Inline code (single backticks)
 * - Bold (**text**) and Italic (*text*)
 * - Links [text](url)
 * - Images ![alt](url)
 * - Tables with header and body
 * - Ordered and unordered lists with indentation
 * - Blockquotes (> text)
 * - Horizontal rules (---, ***, ___)
 * - Paragraphs and spacing
 */
import React from 'react';

// =============================================================================
// Types
// =============================================================================

export interface MarkdownRenderOptions {
  /** CSS class prefix for styling (default: 'md') */
  classPrefix?: string;
  /** Whether to wrap output in a container div */
  wrapInContainer?: boolean;
  /** Container class name when wrapInContainer is true */
  containerClass?: string;
}

// =============================================================================
// Inline Markdown Parser
// =============================================================================

/**
 * Renders inline markdown elements within a line of text.
 * Supports: images, bold, italic, inline code, links
 */
export function renderInlineMarkdown(
  text: string,
  classPrefix = 'md'
): React.ReactNode {
  const result: React.ReactNode[] = [];
  let key = 0;

  // Regex to match inline markdown patterns:
  // - Images: ![alt](url)
  // - Bold: **text**
  // - Italic: *text* (but not **)
  // - Inline code: `code`
  // - Links: [text](url)
  const regex =
    /(!\[(.*?)\]\(([^)]+)\)|\*\*(.+?)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    // Add text before this match
    if (match.index > lastIndex) {
      result.push(text.slice(lastIndex, match.index));
    }

    const [
      fullMatch,
      ,
      imageAlt,
      imageUrl,
      bold,
      italic,
      code,
      linkText,
      linkUrl,
    ] = match;

    if (imageUrl) {
      result.push(
        <img
          key={key++}
          src={imageUrl}
          alt={imageAlt ?? ''}
          className={`${classPrefix}-image`}
        />
      );
    } else if (bold) {
      result.push(
        <strong key={key++} className={`${classPrefix}-bold`}>
          {bold}
        </strong>
      );
    } else if (italic) {
      result.push(
        <em key={key++} className={`${classPrefix}-italic`}>
          {italic}
        </em>
      );
    } else if (code) {
      result.push(
        <code key={key++} className={`${classPrefix}-inline-code`}>
          {code}
        </code>
      );
    } else if (linkText && linkUrl) {
      result.push(
        <a
          key={key++}
          href={linkUrl}
          className={`${classPrefix}-link`}
          target="_blank"
          rel="noopener noreferrer"
        >
          {linkText}
        </a>
      );
    }

    lastIndex = match.index + fullMatch.length;
  }

  // Add remaining text after last match
  if (lastIndex < text.length) {
    result.push(text.slice(lastIndex));
  }

  if (result.length === 0) {
    return text;
  }
  if (result.length === 1) {
    return result[0];
  }
  return <>{result}</>;
}

// =============================================================================
// Block Markdown Parser
// =============================================================================

/**
 * Helper: Check if a line is a table separator (e.g., |---|---|)
 */
function isTableSeparator(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed) {
    return false;
  }
  const normalized = trimmed.startsWith('|') ? trimmed : `|${trimmed}`;
  return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(normalized);
}

/**
 * Helper: Split a table row into cells
 */
function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

/**
 * Renders markdown content to an array of JSX elements.
 * Supports all major markdown features.
 */
export function renderMarkdown(
  content: string,
  options: MarkdownRenderOptions = {}
): JSX.Element {
  const { classPrefix = 'md', wrapInContainer = true, containerClass } = options;

  const lines = content.split('\n');
  const elements: JSX.Element[] = [];
  let inCodeBlock = false;
  let codeBlockContent: string[] = [];
  let codeBlockLang = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code blocks (triple backticks)
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        // End code block
        elements.push(
          <pre
            key={`code-${i}`}
            className={`${classPrefix}-code-block`}
            data-lang={codeBlockLang || undefined}
          >
            <code>{codeBlockContent.join('\n')}</code>
          </pre>
        );
        codeBlockContent = [];
        codeBlockLang = '';
        inCodeBlock = false;
      } else {
        // Start code block
        inCodeBlock = true;
        codeBlockLang = line.trim().slice(3).trim();
      }
      continue;
    }

    if (inCodeBlock) {
      codeBlockContent.push(line);
      continue;
    }

    // Tables - detect by looking for | and checking next line for separator
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headerCells = splitTableRow(line);
      const rows: string[][] = [];
      i += 2; // Skip header and separator
      while (i < lines.length && lines[i].includes('|')) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      i -= 1; // Adjust for loop increment

      elements.push(
        <table key={`table-${i}`} className={`${classPrefix}-table`}>
          <thead>
            <tr>
              {headerCells.map((cell, idx) => (
                <th key={`th-${idx}`}>{renderInlineMarkdown(cell, classPrefix)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`tr-${rowIndex}`}>
                {row.map((cell, cellIndex) => (
                  <td key={`td-${rowIndex}-${cellIndex}`}>
                    {renderInlineMarkdown(cell, classPrefix)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
      continue;
    }

    // Headers (h1-h4)
    if (line.startsWith('#### ')) {
      elements.push(
        <h4 key={i} className={`${classPrefix}-h4`}>
          {renderInlineMarkdown(line.slice(5), classPrefix)}
        </h4>
      );
    } else if (line.startsWith('### ')) {
      elements.push(
        <h3 key={i} className={`${classPrefix}-h3`}>
          {renderInlineMarkdown(line.slice(4), classPrefix)}
        </h3>
      );
    } else if (line.startsWith('## ')) {
      elements.push(
        <h2 key={i} className={`${classPrefix}-h2`}>
          {renderInlineMarkdown(line.slice(3), classPrefix)}
        </h2>
      );
    } else if (line.startsWith('# ')) {
      elements.push(
        <h1 key={i} className={`${classPrefix}-h1`}>
          {renderInlineMarkdown(line.slice(2), classPrefix)}
        </h1>
      );
    }
    // Horizontal rule (---, ***, ___, or unicode dashes)
    else if (/^[-—─*_]{3,}$/.test(line.trim())) {
      elements.push(<hr key={i} className={`${classPrefix}-hr`} />);
    }
    // Blockquote
    else if (line.startsWith('> ')) {
      elements.push(
        <blockquote key={i} className={`${classPrefix}-blockquote`}>
          {renderInlineMarkdown(line.slice(2), classPrefix)}
        </blockquote>
      );
    }
    // Unordered list (-, *, +)
    else if (/^[\s]*[-*+]\s/.test(line)) {
      const indent = line.match(/^(\s*)/)?.[1].length || 0;
      const text = line.replace(/^[\s]*[-*+]\s/, '');
      elements.push(
        <div
          key={i}
          className={`${classPrefix}-li`}
          style={{ marginLeft: `${indent * 8}px` }}
        >
          • {renderInlineMarkdown(text, classPrefix)}
        </div>
      );
    }
    // Ordered list (1., 2., etc.)
    else if (/^[\s]*\d+\.\s/.test(line)) {
      const match = line.match(/^(\s*)(\d+)\.\s(.*)$/);
      if (match) {
        const [, spaces, num, content] = match;
        const indent = spaces?.length || 0;
        elements.push(
          <div
            key={i}
            className={`${classPrefix}-li`}
            style={{ marginLeft: `${indent * 8}px` }}
          >
            {num}. {renderInlineMarkdown(content, classPrefix)}
          </div>
        );
      }
    }
    // Empty line (spacer)
    else if (line.trim() === '') {
      elements.push(<div key={i} className={`${classPrefix}-spacer`} />);
    }
    // Regular paragraph
    else {
      elements.push(
        <p key={i} className={`${classPrefix}-p`}>
          {renderInlineMarkdown(line, classPrefix)}
        </p>
      );
    }
  }

  // Handle unclosed code block at end of content
  if (inCodeBlock && codeBlockContent.length > 0) {
    elements.push(
      <pre key="code-final" className={`${classPrefix}-code-block`} data-lang={codeBlockLang || undefined}>
        <code>{codeBlockContent.join('\n')}</code>
      </pre>
    );
  }

  if (wrapInContainer) {
    return (
      <div className={containerClass || `${classPrefix}-content`}>{elements}</div>
    );
  }

  return <>{elements}</>;
}

/**
 * Renders markdown and returns array of elements (for backwards compatibility)
 */
export function renderMarkdownElements(
  content: string,
  classPrefix = 'md'
): JSX.Element[] {
  const lines = content.split('\n');
  const elements: JSX.Element[] = [];
  let inCodeBlock = false;
  let codeBlockContent: string[] = [];
  let codeBlockLang = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code blocks
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <pre
            key={`code-${i}`}
            className={`${classPrefix}-code-block`}
            data-lang={codeBlockLang || undefined}
          >
            <code>{codeBlockContent.join('\n')}</code>
          </pre>
        );
        codeBlockContent = [];
        codeBlockLang = '';
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
        codeBlockLang = line.trim().slice(3).trim();
      }
      continue;
    }

    if (inCodeBlock) {
      codeBlockContent.push(line);
      continue;
    }

    // Tables
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headerCells = splitTableRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|')) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      i -= 1;

      elements.push(
        <table key={`table-${i}`} className={`${classPrefix}-table`}>
          <thead>
            <tr>
              {headerCells.map((cell, idx) => (
                <th key={`th-${idx}`}>{renderInlineMarkdown(cell, classPrefix)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`tr-${rowIndex}`}>
                {row.map((cell, cellIndex) => (
                  <td key={`td-${rowIndex}-${cellIndex}`}>
                    {renderInlineMarkdown(cell, classPrefix)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
      continue;
    }

    // Headers
    if (line.startsWith('#### ')) {
      elements.push(
        <h4 key={i} className={`${classPrefix}-h4`}>
          {renderInlineMarkdown(line.slice(5), classPrefix)}
        </h4>
      );
    } else if (line.startsWith('### ')) {
      elements.push(
        <h3 key={i} className={`${classPrefix}-h3`}>
          {renderInlineMarkdown(line.slice(4), classPrefix)}
        </h3>
      );
    } else if (line.startsWith('## ')) {
      elements.push(
        <h2 key={i} className={`${classPrefix}-h2`}>
          {renderInlineMarkdown(line.slice(3), classPrefix)}
        </h2>
      );
    } else if (line.startsWith('# ')) {
      elements.push(
        <h1 key={i} className={`${classPrefix}-h1`}>
          {renderInlineMarkdown(line.slice(2), classPrefix)}
        </h1>
      );
    }
    // Horizontal rule
    else if (/^[-—─*_]{3,}$/.test(line.trim())) {
      elements.push(<hr key={i} className={`${classPrefix}-hr`} />);
    }
    // Blockquote
    else if (line.startsWith('> ')) {
      elements.push(
        <blockquote key={i} className={`${classPrefix}-blockquote`}>
          {renderInlineMarkdown(line.slice(2), classPrefix)}
        </blockquote>
      );
    }
    // Unordered list
    else if (/^[\s]*[-*+]\s/.test(line)) {
      const indent = line.match(/^(\s*)/)?.[1].length || 0;
      const text = line.replace(/^[\s]*[-*+]\s/, '');
      elements.push(
        <div
          key={i}
          className={`${classPrefix}-li`}
          style={{ marginLeft: `${indent * 8}px` }}
        >
          • {renderInlineMarkdown(text, classPrefix)}
        </div>
      );
    }
    // Ordered list
    else if (/^[\s]*\d+\.\s/.test(line)) {
      const match = line.match(/^(\s*)(\d+)\.\s(.*)$/);
      if (match) {
        const [, spaces, num, content] = match;
        const indent = spaces?.length || 0;
        elements.push(
          <div
            key={i}
            className={`${classPrefix}-li`}
            style={{ marginLeft: `${indent * 8}px` }}
          >
            {num}. {renderInlineMarkdown(content, classPrefix)}
          </div>
        );
      }
    }
    // Empty line
    else if (line.trim() === '') {
      elements.push(<div key={i} className={`${classPrefix}-spacer`} />);
    }
    // Regular text
    else {
      elements.push(
        <div key={i}>{renderInlineMarkdown(line, classPrefix)}</div>
      );
    }
  }

  // Handle unclosed code block
  if (inCodeBlock && codeBlockContent.length > 0) {
    elements.push(
      <pre key="code-final" className={`${classPrefix}-code-block`} data-lang={codeBlockLang || undefined}>
        <code>{codeBlockContent.join('\n')}</code>
      </pre>
    );
  }

  return elements;
}

// =============================================================================
// React Component Wrapper
// =============================================================================

export interface MarkdownProps {
  /** The markdown content to render */
  content: string;
  /** CSS class prefix (default: 'md') */
  classPrefix?: string;
  /** Additional class name for the container */
  className?: string;
}

/**
 * React component for rendering markdown content.
 * Can be used directly in JSX.
 */
export function Markdown({
  content,
  classPrefix = 'md',
  className,
}: MarkdownProps): JSX.Element {
  return renderMarkdown(content, {
    classPrefix,
    wrapInContainer: true,
    containerClass: className || `${classPrefix}-content`,
  });
}

export default Markdown;
