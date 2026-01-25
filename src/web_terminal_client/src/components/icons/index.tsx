/**
 * Icon components
 *
 * SVG icon components extracted from App.tsx for reusability.
 */

import React from 'react';

// Copy button icons (matching FileViewer style)
export function CopyIconSvg(): JSX.Element {
  return (
    <span className="copy-icon-wrapper">
      <svg className="copy-icon-svg" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M10 0H6V3H10V0Z" fill="currentColor" />
        <path d="M4 2H2V16H14V2H12V5H4V2Z" fill="currentColor" />
      </svg>
    </span>
  );
}

export function CheckIconSvg(): JSX.Element {
  return (
    <span className="copy-icon-wrapper">
      <svg className="copy-icon-svg" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M2 8L6 12L14 4" stroke="currentColor" strokeWidth="2" fill="none" />
      </svg>
    </span>
  );
}

// Result file action icons (matching FileExplorer style)
export function EyeIcon(): JSX.Element {
  return (
    <span className="action-icon-wrapper">
      <svg className="action-icon-svg" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
        <path
          d="M0 16q0.064 0.128 0.16 0.352t0.48 0.928 0.832 1.344 1.248 1.536 1.664 1.696 2.144 1.568 2.624 1.344 3.136 0.896 3.712 0.352 3.712-0.352 3.168-0.928 2.592-1.312 2.144-1.6 1.664-1.632 1.248-1.6 0.832-1.312 0.48-0.928l0.16-0.352q-0.032-0.128-0.16-0.352t-0.48-0.896-0.832-1.344-1.248-1.568-1.664-1.664-2.144-1.568-2.624-1.344-3.136-0.896-3.712-0.352-3.712 0.352-3.168 0.896-2.592 1.344-2.144 1.568-1.664 1.664-1.248 1.568-0.832 1.344-0.48 0.928zM10.016 16q0-2.464 1.728-4.224t4.256-1.76 4.256 1.76 1.76 4.224-1.76 4.256-4.256 1.76-4.256-1.76-1.728-4.256zM12 16q0 1.664 1.184 2.848t2.816 1.152 2.816-1.152 1.184-2.848-1.184-2.816-2.816-1.184-2.816 1.184l2.816 2.816h-4z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

export function DownloadIcon(): JSX.Element {
  return (
    <span className="action-icon-wrapper">
      <svg className="action-icon-svg" viewBox="0 -0.5 21 21" xmlns="http://www.w3.org/2000/svg">
        <path
          d="M11.55,11 L11.55,4 L9.45,4 L9.45,11 L5.9283,11 L10.38345,16.243 L15.1263,11 L11.55,11 Z M12.6,0 L12.6,2 L18.9,2 L18.9,8 L21,8 L21,0 L12.6,0 Z M18.9,18 L12.6,18 L12.6,20 L21,20 L21,12 L18.9,12 L18.9,18 Z M2.1,12 L0,12 L0,20 L8.4,20 L8.4,18 L2.1,18 L2.1,12 Z M2.1,8 L0,8 L0,0 L8.4,0 L8.4,2 L2.1,2 L2.1,8 Z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

export function FolderIcon(): JSX.Element {
  return (
    <span className="action-icon-wrapper">
      <svg className="action-icon-svg" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M0 1H6L9 4H16V14H0V1Z" fill="currentColor" />
      </svg>
    </span>
  );
}
