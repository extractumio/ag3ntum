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
import React, { useCallback, useEffect, useState, useRef, createContext, useContext } from 'react';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.css';
import { sanitizeFilename } from './FileExplorer';
import { renderMarkdown } from './MarkdownRenderer';
import { getFileContent, downloadFile } from './api';

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
  /** Optional image URL for displaying images (since images are binary and have no content) */
  imageUrl?: string;
}

/**
 * Check if a mime type is an image type
 */
function isImageMimeType(mimeType: string): boolean {
  return mimeType.startsWith('image/');
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

function FolderIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M1 3H6L8 5H15V13H1V3Z" fill="currentColor" />
      </svg>
    </span>
  );
}

function FullscreenIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`action-icon-wrapper ${className}`}>
      <svg
        className="action-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M2 2V6H4V4H6V2H2Z" fill="currentColor" />
        <path d="M14 2H10V4H12V6H14V2Z" fill="currentColor" />
        <path d="M14 14V10H12V12H10V14H14Z" fill="currentColor" />
        <path d="M2 14H6V12H4V10H2V14Z" fill="currentColor" />
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
// Shared Code Content Renderer - Core syntax-highlighted code display
// =============================================================================

export interface CodeContentRendererProps {
  /** The code content to display */
  content: string;
  /** The language for syntax highlighting */
  language: string;
  /** Whether to show line numbers (default: false) */
  showLineNumbers?: boolean;
  /** Maximum lines to display (for collapsed mode) */
  maxLines?: number;
  /** Starting line number offset (default: 1) */
  startLine?: number;
  /** CSS class prefix for styling */
  classPrefix?: string;
}

/**
 * Shared code content renderer with optional line numbers and syntax highlighting.
 * Used by both FileViewer (modal) and InlineFileViewer.
 */
export function CodeContentRenderer({
  content,
  language,
  showLineNumbers = false,
  maxLines,
  startLine = 1,
  classPrefix = 'code-content',
}: CodeContentRendererProps): JSX.Element {
  const codeRef = useRef<HTMLElement>(null);

  const lines = content.split('\n');
  const displayLines = maxLines ? lines.slice(0, maxLines) : lines;
  const displayContent = displayLines.join('\n');

  // Re-apply syntax highlighting when content or maxLines changes
  useEffect(() => {
    if (codeRef.current) {
      codeRef.current.removeAttribute('data-highlighted');
      hljs.highlightElement(codeRef.current);
    }
  }, [displayContent, language]);

  if (showLineNumbers) {
    return (
      <div className={`${classPrefix}-wrapper`}>
        <div className={`${classPrefix}-line-numbers`}>
          {displayLines.map((_, idx) => (
            <span key={idx} className="line-number">{startLine + idx}</span>
          ))}
        </div>
        <pre className={`${classPrefix}-code`}>
          <code ref={codeRef} className={`language-${language}`}>
            {displayContent}
          </code>
        </pre>
      </div>
    );
  }

  return (
    <pre className={`${classPrefix}-code`}>
      <code ref={codeRef} className={`language-${language}`}>
        {displayContent}
      </code>
    </pre>
  );
}

// =============================================================================
// Shared Image Content Renderer - Smart image display with sizing
// =============================================================================

export interface ImageContentRendererProps {
  /** The image URL to display */
  imageUrl: string;
  /** Alt text for the image */
  alt: string;
  /** Maximum height in pixels (default: 400) */
  maxHeight?: number;
  /** Minimum height in pixels (default: 60) */
  minHeight?: number;
  /** Whether to enable click-to-expand modal (default: true) */
  enableModal?: boolean;
  /** Callback when image loads successfully */
  onLoad?: (dimensions: { width: number; height: number }) => void;
  /** Callback when image fails to load */
  onError?: () => void;
  /** CSS class prefix for styling */
  classPrefix?: string;
}

/**
 * Shared image content renderer with smart sizing for various aspect ratios.
 * Handles edge cases like 5000x10, 9000x9000, 1x5600px, etc.
 * Used by both FileViewerModal and InlineImageViewer.
 */
export function ImageContentRenderer({
  imageUrl,
  alt,
  maxHeight = 400,
  minHeight = 60,
  enableModal = true,
  onLoad,
  onError,
  classPrefix = 'image-content',
}: ImageContentRendererProps): JSX.Element {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);

  const handleImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    const dims = { width: img.naturalWidth, height: img.naturalHeight };
    setDimensions(dims);
    setIsLoading(false);
    onLoad?.(dims);
  }, [onLoad]);

  const handleImageError = useCallback(() => {
    setError(true);
    setIsLoading(false);
    onError?.();
  }, [onError]);

  // Calculate container style based on aspect ratio
  const getContainerStyle = (): React.CSSProperties => {
    if (!dimensions) return {};

    const { width, height } = dimensions;
    const aspectRatio = width / height;

    // Handle extreme aspect ratios
    if (aspectRatio > 10) {
      // Very wide image (e.g., 5000x10)
      return { maxHeight: `${Math.max(minHeight, 100)}px` };
    } else if (aspectRatio < 0.1) {
      // Very tall image (e.g., 10x5000)
      return { maxHeight: `${maxHeight}px`, maxWidth: '200px' };
    } else if (width > 4000 || height > 4000) {
      // Very large image
      return { maxHeight: `${maxHeight}px` };
    }

    return { maxHeight: `${maxHeight}px` };
  };

  return (
    <>
      <div className={`${classPrefix}-container`} style={getContainerStyle()}>
        {isLoading && (
          <div className={`${classPrefix}-loading`}>Loading image...</div>
        )}
        {error ? (
          <div className={`${classPrefix}-error`}>Failed to load image</div>
        ) : (
          <img
            src={imageUrl}
            alt={alt}
            className={`${classPrefix}-img ${isLoading ? 'loading' : 'loaded'}`}
            onLoad={handleImageLoad}
            onError={handleImageError}
            onClick={enableModal ? () => setShowModal(true) : undefined}
            style={enableModal ? { cursor: 'pointer' } : undefined}
          />
        )}
      </div>
      {showModal && enableModal && (
        <div className={`${classPrefix}-modal`} onClick={() => setShowModal(false)}>
          <div className={`${classPrefix}-modal-content`} onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className={`${classPrefix}-modal-close`}
              onClick={() => setShowModal(false)}
            >
              {ICONS.close}
            </button>
            <img src={imageUrl} alt={alt} className={`${classPrefix}-modal-img`} />
          </div>
        </div>
      )}
    </>
  );
}

// =============================================================================
// Syntax Highlighted Code Renderer (Legacy wrapper for FileViewer)
// =============================================================================

function CodeRenderer({ content, mimeType, fileName }: RendererProps): JSX.Element {
  const language = getLanguageFromMimeType(mimeType, fileName);
  return (
    <CodeContentRenderer
      content={content}
      language={language}
      classPrefix="file-viewer"
    />
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
  imageUrl,
}: FileViewerProps): JSX.Element | null {
  const [renderMode, setRenderMode] = useState<RenderMode>('rendered');
  const [copied, setCopied] = useState(false);

  // Check if this is an image file
  const isImage = file && isImageMimeType(file.mimeType);

  // Get renderer config for this file (only for non-images)
  const rendererConfig = file && !isImage ? getRenderer(file.mimeType, file.name) : null;

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
                  title="Close (Esc)"
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
        ) : isImage && imageUrl ? (
          // Image file - render using ImageContentRenderer
          <ImageContentRenderer
            imageUrl={imageUrl}
            alt={file?.name || 'Image'}
            classPrefix="file-viewer-image"
            maxHeight={600}
            enableModal={true}
          />
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
  imageUrl,
}: FileViewerModalProps): JSX.Element | null {
  // Handle ESC key to close modal
  useEffect(() => {
    if (!isOpen && !file && !isLoading) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && onClose) {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, file, isLoading, onClose]);

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
          imageUrl={imageUrl}
        />
      </div>
    </div>
  );
}

// =============================================================================
// ImageViewerModal - Unified modal for viewing images
// =============================================================================

export interface ImageViewerModalProps {
  /** The image URL (blob URL) to display */
  imageUrl: string;
  /** The file name */
  fileName: string;
  /** Image dimensions (optional) */
  dimensions?: { width: number; height: number } | null;
  /** Whether the modal is open */
  isOpen: boolean;
  /** Callback to close the modal */
  onClose: () => void;
  /** Callback to download the image */
  onDownload?: () => void;
  /** Callback to show in File Explorer */
  onShowInExplorer?: () => void;
}

export function ImageViewerModal({
  imageUrl,
  fileName,
  dimensions,
  isOpen,
  onClose,
  onDownload,
  onShowInExplorer,
}: ImageViewerModalProps): JSX.Element | null {
  const [copied, setCopied] = useState(false);

  // Handle ESC key to close modal
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // Copy image to clipboard
  const handleCopyImage = useCallback(async () => {
    try {
      const response = await fetch(imageUrl);
      const blob = await response.blob();
      await navigator.clipboard.write([
        new ClipboardItem({ [blob.type]: blob }),
      ]);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy image:', err);
      // Fallback: copy the URL
      try {
        await navigator.clipboard.writeText(imageUrl);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        console.error('Failed to copy image URL');
      }
    }
  }, [imageUrl]);

  if (!isOpen) return null;

  return (
    <div className="image-viewer-modal-overlay" onClick={onClose}>
      <div className="image-viewer-modal" onClick={(e) => e.stopPropagation()}>
        <div className="image-viewer-modal-header">
          <div className="image-viewer-modal-title">
            <ImageIcon />
            <span className="image-viewer-modal-name">{sanitizeFilename(fileName)}</span>
            {dimensions && (
              <span className="image-viewer-modal-meta">
                {dimensions.width} x {dimensions.height}
              </span>
            )}
          </div>
          <div className="image-viewer-modal-actions">
            {/* Copy to clipboard */}
            <button
              type="button"
              className={`image-viewer-modal-action-btn ${copied ? 'copied' : ''}`}
              onClick={handleCopyImage}
              title={copied ? 'Copied!' : 'Copy Image'}
            >
              {copied ? <CheckIcon /> : <CopyIcon />}
            </button>
            {/* Show in File Explorer */}
            {onShowInExplorer && (
              <button
                type="button"
                className="image-viewer-modal-action-btn"
                onClick={onShowInExplorer}
                title="Show in File Explorer"
              >
                <FolderIcon />
              </button>
            )}
            {/* Download */}
            {onDownload && (
              <button
                type="button"
                className="image-viewer-modal-action-btn"
                onClick={onDownload}
                title="Download"
              >
                <DownloadIcon />
              </button>
            )}
            {/* Close */}
            <button
              type="button"
              className="image-viewer-modal-close-btn"
              onClick={onClose}
              title="Close"
            >
              {ICONS.close}
            </button>
          </div>
        </div>
        <div className="image-viewer-modal-content">
          <img src={imageUrl} alt={fileName} className="image-viewer-modal-img" />
        </div>
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

// =============================================================================
// Agent Message Context - For inline file/image viewers in agent messages
// =============================================================================

export interface AgentMessageContextValue {
  sessionId: string;
  baseUrl: string;
  token: string;
  /** Callback to show file in File Explorer and navigate to its folder */
  onShowInExplorer?: (filePath: string) => void;
}

export const AgentMessageContext = createContext<AgentMessageContextValue | null>(null);

export function useAgentMessageContext(): AgentMessageContextValue | null {
  return useContext(AgentMessageContext);
}

// =============================================================================
// Supported File Extensions
// =============================================================================

const SUPPORTED_TEXT_EXTENSIONS = new Set([
  'md', 'txt', 'sh', 'py', 'js', 'ts', 'jsx', 'tsx', 'json', 'yaml', 'yml',
  'html', 'css', 'xml', 'toml', 'ini', 'cfg', 'conf', 'log', 'env', 'gitignore',
  'dockerfile', 'makefile', 'sql', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'hpp',
  'rb', 'php', 'swift', 'kt', 'scala', 'r', 'lua', 'pl', 'pm'
]);

const SUPPORTED_IMAGE_EXTENSIONS = new Set([
  'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico'
]);

export function isSupportedTextFile(filePath: string): boolean {
  const ext = filePath.split('.').pop()?.toLowerCase() || '';
  return SUPPORTED_TEXT_EXTENSIONS.has(ext);
}

export function isSupportedImageFile(filePath: string): boolean {
  const ext = filePath.split('.').pop()?.toLowerCase() || '';
  return SUPPORTED_IMAGE_EXTENSIONS.has(ext);
}

// =============================================================================
// Expand/Collapse Icon
// =============================================================================

function ChevronIcon({ expanded, className = '' }: { expanded: boolean; className?: string }): JSX.Element {
  return (
    <span className={`chevron-icon ${expanded ? 'expanded' : ''} ${className}`}>
      <svg
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        style={{ width: '12px', height: '12px', transition: 'transform 0.2s ease' }}
      >
        <path
          d="M4 6L8 10L12 6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />
      </svg>
    </span>
  );
}

function ImageIcon({ className = '' }: IconProps): JSX.Element {
  return (
    <span className={`file-icon-wrapper ${className}`}>
      <svg
        className="file-icon-svg"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <rect x="1" y="2" width="14" height="12" rx="1" stroke="currentColor" strokeWidth="1.5" fill="none" />
        <circle cx="5" cy="6" r="1.5" fill="currentColor" />
        <path d="M1 11L5 8L8 11L11 7L15 11V13C15 13.5523 14.5523 14 14 14H2C1.44772 14 1 13.5523 1 13V11Z" fill="currentColor" />
      </svg>
    </span>
  );
}

// =============================================================================
// InlineFileViewer Component - For text files in agent messages
// =============================================================================

const DEFAULT_VISIBLE_LINES = 10;
const MAX_TOTAL_LINES = 500;

export interface InlineFileViewerProps {
  filePath: string;
  sessionId?: string;
  baseUrl?: string;
  token?: string;
  /** Callback to show file in File Explorer */
  onShowInExplorer?: (filePath: string) => void;
}

type InlineRenderMode = 'source' | 'rich';

export function InlineFileViewer({
  filePath,
  sessionId: propSessionId,
  baseUrl: propBaseUrl,
  token: propToken,
  onShowInExplorer: propOnShowInExplorer,
}: InlineFileViewerProps): JSX.Element {
  const context = useAgentMessageContext();
  const sessionId = propSessionId || context?.sessionId || '';
  const baseUrl = propBaseUrl || context?.baseUrl || '';
  const token = propToken || context?.token || '';
  const onShowInExplorer = propOnShowInExplorer || context?.onShowInExplorer;

  const [fileData, setFileData] = useState<FileViewerData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [renderMode, setRenderMode] = useState<InlineRenderMode>('rich');

  const fileName = filePath.split('/').pop() || filePath;
  const isMarkdown = fileName.endsWith('.md') || fileData?.mimeType === 'text/markdown';

  useEffect(() => {
    if (!sessionId || !baseUrl || !token) {
      setError('Session context not available');
      setIsLoading(false);
      return;
    }

    let cancelled = false;

    async function fetchFile() {
      try {
        setIsLoading(true);
        setError(null);
        const response = await getFileContent(baseUrl, token, sessionId, filePath);
        if (!cancelled) {
          setFileData(toFileViewerData(response));
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load file');
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    fetchFile();
    return () => { cancelled = true; };
  }, [filePath, sessionId, baseUrl, token]);

  const totalLines = fileData?.content?.split('\n').length || 0;
  const maxLines = expanded ? MAX_TOTAL_LINES : DEFAULT_VISIBLE_LINES;
  const hasMore = totalLines > DEFAULT_VISIBLE_LINES;
  const hiddenCount = totalLines - DEFAULT_VISIBLE_LINES;
  const isTruncatedByLimit = expanded && totalLines > MAX_TOTAL_LINES;
  const language = getLanguageFromMimeType(fileData?.mimeType || 'text/plain', fileName);

  const handleDownload = useCallback(async () => {
    if (sessionId && baseUrl && token) {
      try {
        await downloadFile(baseUrl, token, sessionId, filePath);
      } catch (err) {
        console.error('Download failed:', err);
      }
    }
  }, [sessionId, baseUrl, token, filePath]);

  const handleCopy = useCallback(async () => {
    if (fileData?.content) {
      try {
        await navigator.clipboard.writeText(fileData.content);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // Fallback for older browsers
        const textarea = document.createElement('textarea');
        textarea.value = fileData.content;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    }
  }, [fileData?.content]);

  const handleShowInExplorer = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (onShowInExplorer) {
      onShowInExplorer(filePath);
    }
  }, [onShowInExplorer, filePath]);

  const toggleRenderMode = useCallback(() => {
    setRenderMode((prev) => (prev === 'source' ? 'rich' : 'source'));
  }, []);

  // Get content to display (possibly truncated for collapsed view)
  const displayContent = fileData?.content
    ? (maxLines ? fileData.content.split('\n').slice(0, maxLines).join('\n') : fileData.content)
    : '';

  return (
    <div className="inline-file-viewer">
      <div className="inline-file-viewer-header">
        <div className="inline-file-viewer-title">
          <FileIcon />
          <span className="inline-file-viewer-name">{sanitizeFilename(fileName)}</span>
          {fileData && (
            <span className="inline-file-viewer-meta">
              {totalLines} lines
            </span>
          )}
        </div>
        <div className="inline-file-viewer-actions">
          {/* Markdown source/rich toggle */}
          {isMarkdown && fileData?.content && (
            <button
              type="button"
              className={`inline-file-viewer-action-btn ${renderMode === 'source' ? 'active' : ''}`}
              onClick={toggleRenderMode}
              title={renderMode === 'source' ? 'Show Rich View' : 'Show Source'}
            >
              {renderMode === 'source' ? <MarkdownIcon /> : <TextIcon />}
            </button>
          )}
          {/* Copy to clipboard */}
          {fileData?.content && (
            <button
              type="button"
              className={`inline-file-viewer-action-btn ${copied ? 'copied' : ''}`}
              onClick={handleCopy}
              title={copied ? 'Copied!' : 'Copy to Clipboard'}
            >
              {copied ? <CheckIcon /> : <CopyIcon />}
            </button>
          )}
          {/* Show in File Explorer */}
          {onShowInExplorer && (
            <button
              type="button"
              className="inline-file-viewer-action-btn"
              onClick={handleShowInExplorer}
              title="Show in File Explorer"
            >
              <FolderIcon />
            </button>
          )}
          {/* Download */}
          {fileData?.content && (
            <button
              type="button"
              className="inline-file-viewer-action-btn"
              onClick={handleDownload}
              title="Download"
            >
              <DownloadIcon />
            </button>
          )}
        </div>
      </div>
      <div className={`inline-file-viewer-content ${expanded ? 'expanded' : 'collapsed'}`}>
        {isLoading ? (
          <div className="inline-file-viewer-loading">Loading...</div>
        ) : error ? (
          <div className="inline-file-viewer-error">{error}</div>
        ) : fileData?.isBinary ? (
          <div className="inline-file-viewer-binary">Binary file - cannot preview</div>
        ) : fileData?.content ? (
          <>
            {isMarkdown && renderMode === 'rich' ? (
              <div className="inline-file-viewer-markdown">
                {renderMarkdown(displayContent)}
              </div>
            ) : (
              <CodeContentRenderer
                content={fileData.content}
                language={language}
                showLineNumbers={true}
                maxLines={maxLines}
                classPrefix="inline-file-viewer"
              />
            )}
            {isTruncatedByLimit && (
              <div className="inline-file-viewer-truncated">
                ... truncated ({totalLines - MAX_TOTAL_LINES} more lines)
              </div>
            )}
          </>
        ) : (
          <div className="inline-file-viewer-empty">Empty file</div>
        )}
      </div>
      {hasMore && !isLoading && !error && (
        <button
          type="button"
          className="inline-file-viewer-expand-btn"
          onClick={() => setExpanded(!expanded)}
        >
          <ChevronIcon expanded={expanded} />
          {expanded ? 'Show less' : `Show more (${hiddenCount} more lines)`}
        </button>
      )}
    </div>
  );
}

// =============================================================================
// InlineImageViewer Component - For images in agent messages
// =============================================================================

export interface InlineImageViewerProps {
  imagePath: string;
  sessionId?: string;
  baseUrl?: string;
  token?: string;
  /** Callback to show file in File Explorer */
  onShowInExplorer?: (filePath: string) => void;
}

export function InlineImageViewer({
  imagePath,
  sessionId: propSessionId,
  baseUrl: propBaseUrl,
  token: propToken,
  onShowInExplorer: propOnShowInExplorer,
}: InlineImageViewerProps): JSX.Element {
  const context = useAgentMessageContext();
  const sessionId = propSessionId || context?.sessionId || '';
  const baseUrl = propBaseUrl || context?.baseUrl || '';
  const token = propToken || context?.token || '';
  const onShowInExplorer = propOnShowInExplorer || context?.onShowInExplorer;

  const [imageDimensions, setImageDimensions] = useState<{ width: number; height: number } | null>(null);
  const [showFullscreen, setShowFullscreen] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fileName = imagePath.split('/').pop() || imagePath;

  // Fetch image with authentication and create blob URL
  useEffect(() => {
    if (!sessionId || !baseUrl || !token) {
      setError('Session context not available');
      setIsLoading(false);
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;

    async function fetchImage() {
      setIsLoading(true);
      setError(null);

      try {
        const params = new URLSearchParams({ path: imagePath });
        const url = `${baseUrl}/api/v1/files/${sessionId}/download?${params.toString()}`;

        const response = await fetch(url, {
          headers: { 'Authorization': `Bearer ${token}` },
        });

        if (!response.ok) {
          throw new Error(`Failed to load image: ${response.statusText}`);
        }

        const blob = await response.blob();
        if (!cancelled) {
          objectUrl = URL.createObjectURL(blob);
          setImageUrl(objectUrl);
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load image');
          setIsLoading(false);
        }
      }
    }

    fetchImage();

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [imagePath, sessionId, baseUrl, token]);

  // Clean up blob URL when component unmounts or imageUrl changes
  useEffect(() => {
    return () => {
      if (imageUrl) {
        URL.revokeObjectURL(imageUrl);
      }
    };
  }, [imageUrl]);

  const handleDownload = useCallback(async () => {
    if (sessionId && baseUrl && token) {
      try {
        await downloadFile(baseUrl, token, sessionId, imagePath);
      } catch (err) {
        console.error('Download failed:', err);
      }
    }
  }, [sessionId, baseUrl, token, imagePath]);

  const handleImageLoad = useCallback((dims: { width: number; height: number }) => {
    setImageDimensions(dims);
  }, []);

  const handleShowInExplorer = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (onShowInExplorer) {
      onShowInExplorer(imagePath);
    }
  }, [onShowInExplorer, imagePath]);

  const handleFullscreen = useCallback(() => {
    setShowFullscreen(true);
  }, []);

  return (
    <div className="inline-image-viewer">
      <div className="inline-image-viewer-header">
        <div className="inline-image-viewer-title">
          <ImageIcon />
          <span className="inline-image-viewer-name">{sanitizeFilename(fileName)}</span>
          {imageDimensions && (
            <span className="inline-image-viewer-meta">
              {imageDimensions.width} x {imageDimensions.height}
            </span>
          )}
        </div>
        <div className="inline-image-viewer-actions">
          {/* Open fullscreen */}
          {imageUrl && (
            <button
              type="button"
              className="inline-image-viewer-action-btn"
              onClick={handleFullscreen}
              title="Open Fullscreen"
            >
              <FullscreenIcon />
            </button>
          )}
          {/* Show in File Explorer */}
          {onShowInExplorer && (
            <button
              type="button"
              className="inline-image-viewer-action-btn"
              onClick={handleShowInExplorer}
              title="Show in File Explorer"
            >
              <FolderIcon />
            </button>
          )}
          {/* Download */}
          <button
            type="button"
            className="inline-image-viewer-action-btn"
            onClick={handleDownload}
            title="Download"
          >
            <DownloadIcon />
          </button>
        </div>
      </div>
      {isLoading ? (
        <div className="inline-image-viewer-loading">Loading image...</div>
      ) : error ? (
        <div className="inline-image-viewer-error">{error}</div>
      ) : imageUrl ? (
        <div
          className="inline-image-viewer-clickable"
          onClick={handleFullscreen}
          title="Click to view full size"
        >
          <ImageContentRenderer
            imageUrl={imageUrl}
            alt={fileName}
            classPrefix="inline-image-viewer"
            maxHeight={400}
            enableModal={false}
            onLoad={handleImageLoad}
          />
        </div>
      ) : (
        <div className="inline-image-viewer-error">No image available</div>
      )}
      {/* Image viewer modal */}
      {imageUrl && (
        <ImageViewerModal
          imageUrl={imageUrl}
          fileName={fileName}
          dimensions={imageDimensions}
          isOpen={showFullscreen}
          onClose={() => setShowFullscreen(false)}
          onDownload={handleDownload}
          onShowInExplorer={onShowInExplorer ? () => onShowInExplorer(imagePath) : undefined}
        />
      )}
    </div>
  );
}

export default FileViewer;
