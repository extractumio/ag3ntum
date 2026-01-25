/**
 * Collapsible output component
 *
 * Renders long output content with expand/collapse functionality.
 * Extracted from App.tsx for better modularity.
 */

import React, { useState, useMemo } from 'react';
import { COLLAPSED_LINE_COUNT } from '../../constants';
import { formatOutputAsYaml } from '../../utils';

export interface CollapsibleOutputProps {
  output: string;
  className?: string;
}

export function CollapsibleOutput({
  output,
  className
}: CollapsibleOutputProps): JSX.Element {
  const [isExpanded, setIsExpanded] = useState(false);

  const { formatted, isYaml } = useMemo(() => formatOutputAsYaml(output), [output]);
  const lines = useMemo(() => formatted.split('\n'), [formatted]);
  const totalLines = lines.length;
  const needsCollapse = totalLines > COLLAPSED_LINE_COUNT;

  const displayedContent = useMemo(() => {
    if (!needsCollapse || isExpanded) {
      return formatted;
    }
    return lines.slice(0, COLLAPSED_LINE_COUNT).join('\n');
  }, [formatted, lines, needsCollapse, isExpanded]);

  const formatBadge = isYaml ? ' · YAML' : '';

  return (
    <div className={`collapsible-output ${className || ''}`}>
      <pre className="tool-section-body tool-output">
        {displayedContent}
      </pre>
      {needsCollapse && (
        <button
          className="output-expand-toggle"
          onClick={() => setIsExpanded(!isExpanded)}
          type="button"
        >
          {isExpanded
            ? `▲ Collapse${formatBadge}`
            : `▼ Expand All (${totalLines} lines)${formatBadge}`
          }
        </button>
      )}
      {!needsCollapse && isYaml && (
        <span className="output-format-badge">YAML</span>
      )}
    </div>
  );
}
