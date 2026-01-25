/**
 * Common/shared components
 *
 * Reusable UI components extracted from App.tsx.
 */

import React, { useState } from 'react';
import { copyAsRichText, copyAsMarkdown } from '../../utils';
import { CopyIconSvg, CheckIconSvg } from '../icons';

export interface CopyButtonsProps {
  contentRef: React.RefObject<HTMLElement | null>;
  markdown: string;
  className?: string;
}

export function CopyButtons({
  contentRef,
  markdown,
  className = '',
}: CopyButtonsProps): JSX.Element {
  const [copiedRich, setCopiedRich] = useState(false);
  const [copiedMd, setCopiedMd] = useState(false);

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
    <div className={`copy-buttons ${className}`}>
      <button
        type="button"
        className={`copy-icon-btn ${copiedRich ? 'copied' : ''}`}
        onClick={handleCopyRich}
        title="Copy as rich text (with formatting)"
      >
        {copiedRich ? <CheckIconSvg /> : <CopyIconSvg />}
        <span className="copy-icon-label">R</span>
      </button>
      <button
        type="button"
        className={`copy-icon-btn ${copiedMd ? 'copied' : ''}`}
        onClick={handleCopyMd}
        title="Copy as markdown"
      >
        {copiedMd ? <CheckIconSvg /> : <CopyIconSvg />}
        <span className="copy-icon-label">M</span>
      </button>
    </div>
  );
}
