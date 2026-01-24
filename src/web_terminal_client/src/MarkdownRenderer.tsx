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
 * - Ag3ntum file/image tags for inline previews
 */
import React from 'react';
import {
  InlineFileViewer,
  InlineImageViewer,
} from './FileViewer';

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
// Ag3ntum Tag Parsing
// =============================================================================

// Regex patterns for ag3ntum tags
const AG3NTUM_FILE_REGEX = /<ag3ntum-file>([^<]+)<\/ag3ntum-file>/g;
const AG3NTUM_IMAGE_REGEX = /<ag3ntum-image>([^<]+)<\/ag3ntum-image>/g;
const AG3NTUM_ATTACHED_FILE_REGEX = /<ag3ntum-attached-file>([^<]+)<\/ag3ntum-attached-file>/g;
const AG3NTUM_TAG_LINE_REGEX = /^<ag3ntum-(file|image|attached-file)>(.+)<\/ag3ntum-\1>$/s;

// Type for attached file metadata
export interface AttachedFileInfo {
  name: string;
  size?: number;
  size_formatted?: string;
  mime_type?: string;
  extension?: string;
  last_modified?: string;
}

// =============================================================================
// Security: Filename Sanitization for Display
// =============================================================================

const MAX_DISPLAY_FILENAME_LENGTH = 100;
const MAX_DISPLAY_MIME_LENGTH = 50;

/**
 * Sanitize a filename for safe display.
 * This is a final defense layer - data should already be sanitized server-side.
 */
function sanitizeFilenameForDisplay(name: string | undefined): string {
  if (!name || typeof name !== 'string') {
    return 'unnamed_file';
  }

  let sanitized = name
    // Remove null bytes and control characters
    .replace(/[\x00-\x1f\x7f]/g, '')
    // Remove HTML-like tags to prevent any XSS (React escapes, but belt-and-suspenders)
    .replace(/<[^>]*>/g, '')
    // Remove path traversal
    .replace(/\.\.\//g, '')
    .replace(/\.\.\\/g, '')
    // Trim whitespace
    .trim();

  // Truncate for display
  if (sanitized.length > MAX_DISPLAY_FILENAME_LENGTH) {
    const lastDot = sanitized.lastIndexOf('.');
    if (lastDot > 0 && sanitized.length - lastDot <= 10) {
      const ext = sanitized.slice(lastDot);
      sanitized = sanitized.slice(0, MAX_DISPLAY_FILENAME_LENGTH - ext.length - 3) + '...' + ext;
    } else {
      sanitized = sanitized.slice(0, MAX_DISPLAY_FILENAME_LENGTH - 3) + '...';
    }
  }

  return sanitized || 'unnamed_file';
}

/**
 * Sanitize a MIME type for safe display.
 */
function sanitizeMimeForDisplay(mime: string | undefined): string {
  if (!mime || typeof mime !== 'string') {
    return '';
  }

  // MIME types should only contain specific safe characters
  const sanitized = mime
    .toLowerCase()
    .replace(/[^a-z0-9/\-+.]/g, '')
    .slice(0, MAX_DISPLAY_MIME_LENGTH);

  return sanitized;
}

/**
 * Sanitize a size string for safe display.
 */
function sanitizeSizeForDisplay(size: string | undefined): string {
  if (!size || typeof size !== 'string') {
    return '';
  }

  // Size should only contain digits, dots, spaces, and unit letters
  return size.replace(/[^0-9.a-zA-Z ]/g, '').slice(0, 20);
}

/**
 * Validate and sanitize attached file info.
 * Returns a safe copy of the file info for display.
 */
function sanitizeFileInfo(file: AttachedFileInfo): AttachedFileInfo {
  return {
    name: sanitizeFilenameForDisplay(file.name),
    size: typeof file.size === 'number' && file.size >= 0 ? file.size : undefined,
    size_formatted: sanitizeSizeForDisplay(file.size_formatted),
    mime_type: sanitizeMimeForDisplay(file.mime_type),
    extension: file.extension?.replace(/[^a-z0-9]/gi, '').slice(0, 10).toLowerCase(),
    last_modified: file.last_modified?.slice(0, 30), // ISO dates are ~24 chars
  };
}

/**
 * Check if a line is an ag3ntum tag (file, image, or attached-file)
 */
function isAg3ntumTagLine(line: string): { type: 'file' | 'image' | 'attached-file'; content: string } | null {
  const trimmed = line.trim();
  const match = trimmed.match(AG3NTUM_TAG_LINE_REGEX);
  if (match) {
    return { type: match[1] as 'file' | 'image' | 'attached-file', content: match[2] };
  }
  return null;
}

/**
 * Parse attached-file tag content (JSON array or legacy format)
 */
function parseAttachedFileContent(content: string): AttachedFileInfo[] {
  // Try JSON format first (new format)
  if (content.startsWith('[')) {
    try {
      const parsed = JSON.parse(content);
      if (Array.isArray(parsed)) {
        return parsed as AttachedFileInfo[];
      }
    } catch {
      // Fall through to legacy parsing
    }
  }

  // Legacy format: "filename|size" (for backwards compatibility)
  const [name, size] = content.split('|');
  if (name) {
    return [{ name: name.trim(), size_formatted: size?.trim() || '' }];
  }

  return [];
}

/**
 * Parse consecutive attached-file tags into a group
 * Returns the file entries and the number of lines consumed
 */
function parseAttachedFileGroup(lines: string[], startIndex: number): { files: AttachedFileInfo[]; linesConsumed: number } {
  const files: AttachedFileInfo[] = [];
  let i = startIndex;

  while (i < lines.length) {
    const tag = isAg3ntumTagLine(lines[i]);
    if (tag && tag.type === 'attached-file') {
      // Parse the content (JSON array or legacy format)
      const parsedFiles = parseAttachedFileContent(tag.content);
      files.push(...parsedFiles);
      i++;
    } else {
      break;
    }
  }

  return { files, linesConsumed: i - startIndex };
}

/**
 * Strip ag3ntum tags from content (for display in non-rendering contexts)
 */
export function stripAg3ntumTags(content: string): string {
  return content
    .replace(AG3NTUM_FILE_REGEX, '')
    .replace(AG3NTUM_IMAGE_REGEX, '')
    .replace(AG3NTUM_ATTACHED_FILE_REGEX, '');
}

/**
 * Component for rendering inline file viewer from ag3ntum-file tags.
 * Uses the InlineFileViewer which gets context from AgentMessageContext.
 */
export function Ag3ntumFilePlaceholder({ filePath }: { filePath: string }): JSX.Element {
  return <InlineFileViewer filePath={filePath} />;
}

/**
 * Component for rendering inline image viewer from ag3ntum-image tags.
 * Uses the InlineImageViewer which gets context from AgentMessageContext.
 */
export function Ag3ntumImagePlaceholder({ imagePath }: { imagePath: string }): JSX.Element {
  return <InlineImageViewer imagePath={imagePath} />;
}

/**
 * Get an appropriate icon for a file based on its extension or mime type
 */
function getFileIcon(file: AttachedFileInfo): string {
  const ext = file.extension?.toLowerCase() || '';
  const mime = file.mime_type?.toLowerCase() || '';

  // Images
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'].includes(ext)) {
    return '🖼️';
  }
  // PDF
  if (mime === 'application/pdf' || ext === 'pdf') {
    return '📕';
  }
  // Documents
  if (['doc', 'docx', 'odt', 'rtf'].includes(ext) || mime.includes('word')) {
    return '📝';
  }
  // Spreadsheets
  if (['xls', 'xlsx', 'csv', 'ods'].includes(ext) || mime.includes('spreadsheet') || mime.includes('excel')) {
    return '📊';
  }
  // Presentations
  if (['ppt', 'pptx', 'odp'].includes(ext) || mime.includes('presentation')) {
    return '📽️';
  }
  // Archives
  if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2'].includes(ext) || mime.includes('zip') || mime.includes('archive')) {
    return '📦';
  }
  // Code files
  if (['js', 'ts', 'jsx', 'tsx', 'py', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'hpp', 'cs', 'php', 'swift', 'kt'].includes(ext)) {
    return '💻';
  }
  // Config/data files
  if (['json', 'yaml', 'yml', 'xml', 'toml', 'ini', 'env'].includes(ext)) {
    return '⚙️';
  }
  // Markdown/text
  if (['md', 'txt', 'log'].includes(ext) || mime.startsWith('text/')) {
    return '📄';
  }
  // Audio
  if (mime.startsWith('audio/') || ['mp3', 'wav', 'ogg', 'flac', 'm4a'].includes(ext)) {
    return '🎵';
  }
  // Video
  if (mime.startsWith('video/') || ['mp4', 'avi', 'mov', 'mkv', 'webm'].includes(ext)) {
    return '🎬';
  }
  // Default
  return '📄';
}

/**
 * Format a file size in bytes to human readable format
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/**
 * Format a date string to a more readable format
 */
function formatDate(isoString: string): string {
  try {
    const date = new Date(isoString);
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoString;
  }
}

/**
 * Component for rendering attached files from ag3ntum-attached-file tags.
 * Displays a collapsible list of uploaded files with icons and metadata.
 *
 * Security: All file data is sanitized before display to prevent XSS and
 * other injection attacks, even though React escapes by default.
 */
export function Ag3ntumAttachedFilesPlaceholder({ files }: { files: AttachedFileInfo[] }): JSX.Element {
  const [expanded, setExpanded] = React.useState(true);

  // Sanitize all files for safe display
  const safeFiles = React.useMemo(
    () => files.map(sanitizeFileInfo).filter(f => f.name),
    [files]
  );

  if (safeFiles.length === 0) return <></>;

  const toggleExpanded = () => setExpanded(!expanded);

  // Calculate total size if available (use raw size values, they're validated)
  const totalSize = safeFiles.reduce((acc, f) => acc + (f.size || 0), 0);
  const totalSizeFormatted = totalSize > 0 ? formatFileSize(totalSize) : null;

  return (
    <div className="ag3ntum-attached-files">
      <button
        type="button"
        className="ag3ntum-attached-files-header"
        onClick={toggleExpanded}
        aria-expanded={expanded}
      >
        <span className="ag3ntum-attached-files-icon">📎</span>
        <span className="ag3ntum-attached-files-label">
          {safeFiles.length} file{safeFiles.length !== 1 ? 's' : ''} attached
          {totalSizeFormatted && <span className="ag3ntum-attached-files-total-size">({totalSizeFormatted})</span>}
        </span>
        <span className={`ag3ntum-attached-files-chevron ${expanded ? 'expanded' : ''}`}>
          ▶
        </span>
      </button>
      {expanded && (
        <div className="ag3ntum-attached-files-list">
          {safeFiles.map((file, idx) => {
            const icon = getFileIcon(file);
            const displaySize = file.size_formatted || (file.size ? formatFileSize(file.size) : null);
            // Truncate display name if needed (already sanitized)
            const displayName = file.name.length > 50
              ? `${file.name.slice(0, 44)}...${file.name.slice(-6)}`
              : file.name;

            return (
              <div key={idx} className="ag3ntum-attached-file-item">
                <span className="ag3ntum-attached-file-icon">{icon}</span>
                <div className="ag3ntum-attached-file-info">
                  <span className="ag3ntum-attached-file-name" title={file.name}>
                    {displayName}
                  </span>
                  <div className="ag3ntum-attached-file-meta">
                    {displaySize && (
                      <span className="ag3ntum-attached-file-size">{displaySize}</span>
                    )}
                    {file.mime_type && (
                      <span className="ag3ntum-attached-file-mime" title={file.mime_type}>
                        {file.mime_type}
                      </span>
                    )}
                    {file.last_modified && (
                      <span className="ag3ntum-attached-file-date" title={file.last_modified}>
                        {formatDate(file.last_modified)}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
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
 * Renders markdown content to a JSX element (optionally wrapped in a container).
 * Delegates to renderMarkdownElements for the actual parsing.
 */
export function renderMarkdown(
  content: string,
  options: MarkdownRenderOptions = {}
): JSX.Element {
  const { classPrefix = 'md', wrapInContainer = true, containerClass } = options;
  const elements = renderMarkdownElements(content, classPrefix);

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

    // Ag3ntum tags (file/image/attached-file references) - check before other block elements
    const ag3ntumTag = isAg3ntumTagLine(line);
    if (ag3ntumTag) {
      if (ag3ntumTag.type === 'file') {
        elements.push(
          <Ag3ntumFilePlaceholder key={`ag3ntum-file-${i}`} filePath={ag3ntumTag.content} />
        );
      } else if (ag3ntumTag.type === 'image') {
        elements.push(
          <Ag3ntumImagePlaceholder key={`ag3ntum-image-${i}`} imagePath={ag3ntumTag.content} />
        );
      } else if (ag3ntumTag.type === 'attached-file') {
        // Group consecutive attached-file tags together
        const { files, linesConsumed } = parseAttachedFileGroup(lines, i);
        elements.push(
          <Ag3ntumAttachedFilesPlaceholder key={`ag3ntum-attached-${i}`} files={files} />
        );
        i += linesConsumed - 1; // -1 because for loop will increment
      }
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
