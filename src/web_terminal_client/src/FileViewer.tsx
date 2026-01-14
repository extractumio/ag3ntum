/**
 * FileViewer Component
 *
 * A standalone, reusable file viewer widget with extensible renderer architecture.
 * Can be used in FileExplorer, conversation history, or any other component.
 *
 * Features:
 * - Extensible renderer system based on mime-type
 * - Markdown rendering with source/rendered toggle
 * - Syntax highlighting for code files via highlight.js
 * - Copy to clipboard functionality
 * - Download support
 */
import React, { useCallback, useEffect, useState, useRef } from 'react';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.css';
import { sanitizeFilename } from './FileExplorer';

// =============================================================================
// Types
// =============================================================================

export interface FileViewerData {
  name: string;
  path?: string;
  content: string | null;
  mimeType: string;
  size: number;
  isBinary?: boolean;
  isTruncated?: boolean;
  error?: string;
}

export interface FileViewerProps {
  file: FileViewerData | null;
  isLoading?: boolean;
  onClose?: () => void;
  onDownload?: () => void;
  className?: string;
  showHeader?: boolean;
  showActions?: boolean;
}

type RenderMode = 'rendered' | 'source';

interface RendererProps {
  content: string;
  mimeType: string;
  fileName: string;
  renderMode: RenderMode;
}

type RendererComponent = React.FC<RendererProps>;

// =============================================================================
// SVG Icon Components
// =============================================================================

interface IconProps {
  className?: string;
}

function FileIcon({ className = '' }: IconProps): JSX.Element {
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
    </span>
  );
}

function CopyIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M10 0H6V3H10V0Z" fill="currentColor" />
        <path d="M4 2H2V16H14V2H12V5H4V2Z" fill="currentColor" />
      </svg>
    </span>
  );
}

function CheckIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M2 8L6 12L14 4" stroke="currentColor" strokeWidth="2" fill="none" />
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

function MarkdownIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M1 3H15V13H1V3Z" stroke="currentColor" strokeWidth="1.5" fill="none" />
        <path d="M3 10V6L5 8L7 6V10" stroke="currentColor" strokeWidth="1.2" fill="none" />
        <path d="M10 10V6L13 9V6" stroke="currentColor" strokeWidth="1.2" fill="none" />
      </svg>
    </span>
  );
}

function TextIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M2 3H14V5H9V13H7V5H2V3Z" fill="currentColor" />
      </svg>
    </span>
  );
}

const ICONS = {
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

// =============================================================================
// Mime Type to Highlight.js Language Mapping
// =============================================================================

const MIME_TO_LANGUAGE: Record<string, string> = {
  // JavaScript/TypeScript
  'text/javascript': 'javascript',
  'application/javascript': 'javascript',
  'text/typescript': 'typescript',
  'application/typescript': 'typescript',

  // Python
  'text/x-python': 'python',
  'application/x-python': 'python',

  // HTML/CSS
  'text/html': 'html',
  'text/css': 'css',
  'text/scss': 'scss',
  'text/less': 'less',

  // Data formats
  'application/json': 'json',
  'text/yaml': 'yaml',
  'application/x-yaml': 'yaml',
  'text/xml': 'xml',
  'application/xml': 'xml',

  // Shell
  'text/x-shellscript': 'bash',
  'application/x-sh': 'bash',

  // Other languages
  'text/x-c': 'c',
  'text/x-c++': 'cpp',
  'text/x-java': 'java',
  'text/x-go': 'go',
  'text/x-rust': 'rust',
  'text/x-ruby': 'ruby',
  'text/x-php': 'php',
  'text/x-sql': 'sql',

  // Markdown
  'text/markdown': 'markdown',

  // Plain text
  'text/plain': 'plaintext',
};

// Extension to language mapping for fallback
const EXT_TO_LANGUAGE: Record<string, string> = {
  // JavaScript/TypeScript
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  mjs: 'javascript',
  cjs: 'javascript',

  // Python
  py: 'python',
  pyw: 'python',

  // Web
  html: 'html',
  htm: 'html',
  css: 'css',
  scss: 'scss',
  sass: 'scss',
  less: 'less',

  // Data
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  xml: 'xml',
  toml: 'ini',

  // Shell
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',

  // Other
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  hpp: 'cpp',
  java: 'java',
  go: 'go',
  rs: 'rust',
  rb: 'ruby',
  php: 'php',
  sql: 'sql',

  // Markdown
  md: 'markdown',
  markdown: 'markdown',

  // Config
  dockerfile: 'dockerfile',
  makefile: 'makefile',
  gitignore: 'plaintext',
  env: 'plaintext',
};

function getLanguageFromMimeType(mimeType: string, fileName: string): string {
  // Try mime type first
  const langFromMime = MIME_TO_LANGUAGE[mimeType.toLowerCase()];
  if (langFromMime && langFromMime !== 'plaintext') {
    return langFromMime;
  }

  // Fallback to file extension
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  const langFromExt = EXT_TO_LANGUAGE[ext];
  if (langFromExt) {
    return langFromExt;
  }

  // Check for special filenames
  const lowerName = fileName.toLowerCase();
  if (lowerName === 'dockerfile') return 'dockerfile';
  if (lowerName === 'makefile') return 'makefile';

  return 'plaintext';
}

// =============================================================================
// Markdown Renderer
// =============================================================================

function renderMarkdown(content: string): JSX.Element {
  const lines = content.split('\n');
  const elements: JSX.Element[] = [];
  let inCodeBlock = false;
  let codeBlockContent: string[] = [];
  let codeBlockLang = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code blocks
    if (line.startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <pre key={`code-${i}`} className="md-code-block" data-lang={codeBlockLang}>
            <code>{codeBlockContent.join('\n')}</code>
          </pre>
        );
        codeBlockContent = [];
        codeBlockLang = '';
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
        codeBlockLang = line.slice(3).trim();
      }
      continue;
    }

    if (inCodeBlock) {
      codeBlockContent.push(line);
      continue;
    }

    // Headers
    if (line.startsWith('# ')) {
      elements.push(<h1 key={i} className="md-h1">{line.slice(2)}</h1>);
    } else if (line.startsWith('## ')) {
      elements.push(<h2 key={i} className="md-h2">{line.slice(3)}</h2>);
    } else if (line.startsWith('### ')) {
      elements.push(<h3 key={i} className="md-h3">{line.slice(4)}</h3>);
    } else if (line.startsWith('#### ')) {
      elements.push(<h4 key={i} className="md-h4">{line.slice(5)}</h4>);
    }
    // Horizontal rule
    else if (line.match(/^(-{3,}|\*{3,}|_{3,})$/)) {
      elements.push(<hr key={i} className="md-hr" />);
    }
    // Unordered list
    else if (line.match(/^[\s]*[-*+]\s/)) {
      const indent = line.match(/^(\s*)/)?.[1].length || 0;
      const text = line.replace(/^[\s]*[-*+]\s/, '');
      elements.push(
        <div key={i} className="md-li" style={{ marginLeft: `${indent * 8}px` }}>
          • {renderInlineMarkdown(text)}
        </div>
      );
    }
    // Ordered list
    else if (line.match(/^[\s]*\d+\.\s/)) {
      const indent = line.match(/^(\s*)/)?.[1].length || 0;
      const match = line.match(/^[\s]*(\d+)\.\s(.*)$/);
      if (match) {
        elements.push(
          <div key={i} className="md-li" style={{ marginLeft: `${indent * 8}px` }}>
            {match[1]}. {renderInlineMarkdown(match[2])}
          </div>
        );
      }
    }
    // Blockquote
    else if (line.startsWith('> ')) {
      elements.push(
        <blockquote key={i} className="md-blockquote">
          {renderInlineMarkdown(line.slice(2))}
        </blockquote>
      );
    }
    // Empty line
    else if (line.trim() === '') {
      elements.push(<div key={i} className="md-spacer" />);
    }
    // Regular paragraph
    else {
      elements.push(<p key={i} className="md-p">{renderInlineMarkdown(line)}</p>);
    }
  }

  return <div className="md-content">{elements}</div>;
}

function renderInlineMarkdown(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let remaining = text;
  let keyIndex = 0;

  while (remaining.length > 0) {
    // Inline code
    const codeMatch = remaining.match(/^`([^`]+)`/);
    if (codeMatch) {
      parts.push(<code key={keyIndex++} className="md-inline-code">{codeMatch[1]}</code>);
      remaining = remaining.slice(codeMatch[0].length);
      continue;
    }

    // Bold
    const boldMatch = remaining.match(/^\*\*([^*]+)\*\*/);
    if (boldMatch) {
      parts.push(<strong key={keyIndex++}>{boldMatch[1]}</strong>);
      remaining = remaining.slice(boldMatch[0].length);
      continue;
    }

    // Italic
    const italicMatch = remaining.match(/^\*([^*]+)\*/);
    if (italicMatch) {
      parts.push(<em key={keyIndex++}>{italicMatch[1]}</em>);
      remaining = remaining.slice(italicMatch[0].length);
      continue;
    }

    // Link
    const linkMatch = remaining.match(/^\[([^\]]+)\]\(([^)]+)\)/);
    if (linkMatch) {
      parts.push(
        <a key={keyIndex++} href={linkMatch[2]} target="_blank" rel="noopener noreferrer" className="md-link">
          {linkMatch[1]}
        </a>
      );
      remaining = remaining.slice(linkMatch[0].length);
      continue;
    }

    // Regular text
    const nextSpecial = remaining.search(/[`*\[]/);
    if (nextSpecial === -1) {
      parts.push(remaining);
      break;
    } else if (nextSpecial === 0) {
      parts.push(remaining[0]);
      remaining = remaining.slice(1);
    } else {
      parts.push(remaining.slice(0, nextSpecial));
      remaining = remaining.slice(nextSpecial);
    }
  }

  return parts.length === 1 ? parts[0] : <>{parts}</>;
}

// =============================================================================
// Syntax Highlighted Code Renderer
// =============================================================================

function CodeRenderer({ content, mimeType, fileName }: RendererProps): JSX.Element {
  const codeRef = useRef<HTMLElement>(null);
  const language = getLanguageFromMimeType(mimeType, fileName);

  useEffect(() => {
    if (codeRef.current) {
      // Reset and re-highlight
      codeRef.current.removeAttribute('data-highlighted');
      hljs.highlightElement(codeRef.current);
    }
  }, [content, language]);

  return (
    <pre className="file-viewer-code">
      <code ref={codeRef} className={`language-${language}`}>
        {content}
      </code>
    </pre>
  );
}

// =============================================================================
// Markdown Renderer Component with Toggle
// =============================================================================

function MarkdownRenderer({ content, renderMode }: RendererProps): JSX.Element {
  if (renderMode === 'rendered') {
    return (
      <div className="file-viewer-markdown">
        {renderMarkdown(content)}
      </div>
    );
  }

  // Source mode - use highlight.js for markdown syntax
  return <CodeRenderer content={content} mimeType="text/markdown" fileName="file.md" renderMode={renderMode} />;
}

// =============================================================================
// Renderer Registry
// =============================================================================

interface RendererConfig {
  component: RendererComponent;
  supportsToggle: boolean;
  defaultMode: RenderMode;
}

const RENDERER_REGISTRY: Record<string, RendererConfig> = {
  // Markdown files
  'text/markdown': {
    component: MarkdownRenderer,
    supportsToggle: true,
    defaultMode: 'rendered',
  },
};

function getRenderer(mimeType: string, fileName: string): RendererConfig {
  // Check exact mime type match
  if (RENDERER_REGISTRY[mimeType]) {
    return RENDERER_REGISTRY[mimeType];
  }

  // Check file extension for markdown
  const ext = fileName.split('.').pop()?.toLowerCase();
  if (ext === 'md' || ext === 'markdown') {
    return RENDERER_REGISTRY['text/markdown'];
  }

  // Default to code renderer for all other text files
  return {
    component: CodeRenderer,
    supportsToggle: false,
    defaultMode: 'source',
  };
}

// =============================================================================
// FileViewer Component
// =============================================================================

export function FileViewer({
  file,
  isLoading = false,
  onClose,
  onDownload,
  className = '',
  showHeader = true,
  showActions = true,
}: FileViewerProps): JSX.Element | null {
  const [renderMode, setRenderMode] = useState<RenderMode>('rendered');
  const [copied, setCopied] = useState(false);

  // Get renderer config for this file
  const rendererConfig = file ? getRenderer(file.mimeType, file.name) : null;

  // Reset render mode when file changes
  useEffect(() => {
    if (rendererConfig) {
      setRenderMode(rendererConfig.defaultMode);
    }
  }, [file?.path, file?.name]);

  // Handle copy to clipboard
  const handleCopy = useCallback(async () => {
    if (file?.content) {
      try {
        await navigator.clipboard.writeText(file.content);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // Fallback for older browsers
        const textArea = document.createElement('textarea');
        textArea.value = file.content;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    }
  }, [file?.content]);

  // Toggle render mode
  const toggleRenderMode = useCallback(() => {
    setRenderMode((prev) => (prev === 'rendered' ? 'source' : 'rendered'));
  }, []);

  if (!file && !isLoading) return null;

  const Renderer = rendererConfig?.component || CodeRenderer;
  const supportsToggle = rendererConfig?.supportsToggle || false;

  return (
    <div className={`file-viewer ${className}`}>
      {showHeader && (
        <div className="file-viewer-header">
          <div className="file-viewer-title">
            <span className="file-viewer-icon"><FileIcon /></span>
            <span className="file-viewer-name">{file ? sanitizeFilename(file.name) : 'Loading...'}</span>
            {file && (
              <span className="file-viewer-meta">
                {formatFileSize(file.size)} | {file.mimeType}
              </span>
            )}
          </div>
          {showActions && (
            <div className="file-viewer-actions">
              {/* Render mode toggle - only for files that support it */}
              {supportsToggle && !file?.isBinary && !file?.error && (
                <button
                  type="button"
                  className={`file-viewer-action-btn ${renderMode === 'rendered' ? 'active' : ''}`}
                  onClick={toggleRenderMode}
                  title={renderMode === 'rendered' ? 'Show source' : 'Show rendered'}
                >
                  {renderMode === 'rendered' ? <TextIcon /> : <MarkdownIcon />}
                </button>
              )}
              {/* Copy button - only for text files */}
              {!file?.isBinary && !file?.error && file?.content && (
                <button
                  type="button"
                  className={`file-viewer-action-btn ${copied ? 'copied' : ''}`}
                  onClick={handleCopy}
                  title={copied ? 'Copied!' : 'Copy to clipboard'}
                >
                  {copied ? <CheckIcon /> : <CopyIcon />}
                </button>
              )}
              {onDownload && (
                <button
                  type="button"
                  className="file-viewer-action-btn"
                  onClick={onDownload}
                  title="Download"
                >
                  <DownloadIcon />
                </button>
              )}
              {onClose && (
                <button
                  type="button"
                  className="file-viewer-close-btn"
                  onClick={onClose}
                  title="Close"
                >
                  {ICONS.close}
                </button>
              )}
            </div>
          )}
        </div>
      )}
      <div className="file-viewer-content">
        {isLoading ? (
          <div className="file-viewer-loading">Loading file content...</div>
        ) : file?.isBinary ? (
          <div className="file-viewer-binary">
            <p>This file is binary and cannot be previewed.</p>
            {onDownload && (
              <button
                type="button"
                className="file-viewer-download-btn"
                onClick={onDownload}
              >
                Download File
              </button>
            )}
          </div>
        ) : file?.error ? (
          <div className="file-viewer-error">{file.error}</div>
        ) : file?.content ? (
          <>
            <Renderer
              content={file.content}
              mimeType={file.mimeType}
              fileName={file.name}
              renderMode={renderMode}
            />
            {file.isTruncated && (
              <div className="file-viewer-truncated">
                ... (file truncated, download to see full content)
              </div>
            )}
          </>
        ) : (
          <div className="file-viewer-empty">No content</div>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// FileViewerModal Component - For modal/overlay usage
// =============================================================================

export interface FileViewerModalProps extends FileViewerProps {
  isOpen?: boolean;
}

export function FileViewerModal({
  file,
  isLoading = false,
  onClose,
  onDownload,
  className = '',
  isOpen = true,
}: FileViewerModalProps): JSX.Element | null {
  if (!isOpen && !file && !isLoading) return null;

  return (
    <div className="file-viewer-overlay" onClick={onClose}>
      <div className="file-viewer-modal" onClick={(e) => e.stopPropagation()}>
        <FileViewer
          file={file}
          isLoading={isLoading}
          onClose={onClose}
          onDownload={onDownload}
          className={className}
          showHeader={true}
          showActions={true}
        />
      </div>
    </div>
  );
}

// =============================================================================
// Utility function to convert API response to FileViewerData
// =============================================================================

export function toFileViewerData(apiResponse: {
  name: string;
  path?: string;
  content?: string | null;
  mime_type?: string;
  size?: number;
  is_binary?: boolean;
  is_truncated?: boolean;
  error?: string | null;
}): FileViewerData {
  return {
    name: apiResponse.name,
    path: apiResponse.path,
    content: apiResponse.content ?? null,
    mimeType: apiResponse.mime_type || 'text/plain',
    size: apiResponse.size || 0,
    isBinary: apiResponse.is_binary,
    isTruncated: apiResponse.is_truncated,
    error: apiResponse.error ?? undefined,
  };
}

export default FileViewer;
