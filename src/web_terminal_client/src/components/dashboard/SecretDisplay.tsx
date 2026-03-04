import React, { useState } from 'react';

interface SecretDisplayProps {
  value: string;
  label?: string;
}

export function SecretDisplay({ value, label = 'Secret' }: SecretDisplayProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Fallback for non-HTTPS contexts
      const el = document.createElement('textarea');
      el.value = value;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="dash-secret">
      <div className="dash-secret-label">{label}</div>
      <div className="dash-secret-value">
        <code>{value}</code>
        <button className="dash-btn dash-btn-sm" onClick={handleCopy}>
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <div className="dash-secret-warning">
        Save this value now — it will not be shown again.
      </div>
    </div>
  );
}
