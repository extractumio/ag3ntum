/**
 * Result section component
 *
 * Displays result comments and files in a collapsible format.
 * Extracted from App.tsx for better modularity.
 */

import React from 'react';
import { renderMarkdown } from '../../MarkdownRenderer';
import { EyeIcon, DownloadIcon, FolderIcon } from '../icons';

export interface ResultSectionProps {
  comments?: string;
  commentsExpanded?: boolean;
  onToggleComments?: () => void;
  files?: string[];
  filesExpanded?: boolean;
  onToggleFiles?: () => void;
  onFileAction?: (filePath: string, mode: 'view' | 'download') => void;
  onShowInExplorer?: (filePath: string) => void;
}

export function ResultSection({
  comments,
  commentsExpanded,
  onToggleComments,
  files,
  filesExpanded,
  onToggleFiles,
  onFileAction,
  onShowInExplorer,
}: ResultSectionProps): JSX.Element | null {
  const hasComments = Boolean(comments);
  const hasFiles = Boolean(files && files.length > 0);

  if (!hasComments && !hasFiles) {
    return null;
  }

  return (
    <div className="result-section">
      <div className="result-title">Result</div>
      {hasComments && comments && (
        <div className="result-item">
          <div className="result-item-header" onClick={onToggleComments} role="button">
            <span className="result-tree">└──</span>
            <span className="result-toggle">{commentsExpanded ? '▼' : '▶'}</span>
            <span className="result-label">Comments</span>
            <span className="result-count">({comments.length})</span>
          </div>
          {commentsExpanded && (
            <div className="result-item-body md-container">
              {renderMarkdown(comments)}
            </div>
          )}
        </div>
      )}
      {hasFiles && files && (
        <div className="result-item">
          <div className="result-item-header" onClick={onToggleFiles} role="button">
            <span className="result-tree">└──</span>
            <span className="result-toggle">{filesExpanded ? '▼' : '▶'}</span>
            <span className="result-label">Files</span>
            <span className="result-count">({files.length})</span>
          </div>
          {filesExpanded && (
            <div className="result-item-body result-files-list">
              {files.map((file) => (
                <div key={file} className="result-file-item">
                  <span className="result-file-icon">📄</span>
                  <span className="result-file-name">{file}</span>
                  <div className="result-file-actions">
                    {onFileAction && (
                      <>
                        <button
                          type="button"
                          className="result-file-action"
                          onClick={() => onFileAction(file, 'view')}
                          title="View file"
                        >
                          <EyeIcon />
                        </button>
                        <button
                          type="button"
                          className="result-file-action"
                          onClick={() => onFileAction(file, 'download')}
                          title="Download file"
                        >
                          <DownloadIcon />
                        </button>
                      </>
                    )}
                    {onShowInExplorer && (
                      <button
                        type="button"
                        className="result-file-action"
                        onClick={() => onShowInExplorer(file)}
                        title="Show in File Explorer"
                      >
                        <FolderIcon />
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
