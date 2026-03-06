import React, { useState } from 'react';

// ---------------------------------------------------------------------------
// FormField — labeled wrapper with error display
// ---------------------------------------------------------------------------

interface FormFieldProps {
  label: string;
  error?: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}

export function FormField({ label, error, required, hint, children }: FormFieldProps) {
  return (
    <div className="dash-form-group">
      <label className="dash-form-label">
        {label}{required && <span className="dash-form-required"> *</span>}
      </label>
      {children}
      {hint && !error && <div className="dash-form-hint">{hint}</div>}
      {error && <div className="dash-form-error">{error}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReadonlyField — display-only field with lock icon
// ---------------------------------------------------------------------------

interface ReadonlyFieldProps {
  label: string;
  value: string | number | null | undefined;
}

export function ReadonlyField({ label, value }: ReadonlyFieldProps) {
  return (
    <div className="dash-form-group">
      <label className="dash-form-label">{label}</label>
      <div className="dash-readonly-field">
        <span className="dash-lock-icon">&#x1f512;</span>
        <span>{value ?? '—'}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpendingBar — progress bar with threshold coloring
// ---------------------------------------------------------------------------

interface SpendingBarProps {
  current: number;
  limit?: number | null;
  alertThreshold?: number;
  label?: string;
}

export function SpendingBar({ current, limit, alertThreshold = 80, label }: SpendingBarProps) {
  if (!limit) {
    return (
      <div className="dash-spending-bar-wrap">
        {label && <div className="dash-form-label">{label}</div>}
        <div className="dash-spending-bar">
          <div className="dash-spending-text">${current.toFixed(2)} (no limit)</div>
        </div>
      </div>
    );
  }
  const pct = Math.min((current / limit) * 100, 100);
  const color = pct >= 90 ? 'var(--color-error, #f87171)'
    : pct >= alertThreshold ? 'var(--color-warning, #facc15)'
    : 'var(--color-success, #4ade80)';

  return (
    <div className="dash-spending-bar-wrap">
      {label && <div className="dash-form-label">{label}</div>}
      <div className="dash-spending-bar">
        <div className="dash-spending-fill" style={{ width: `${pct}%`, background: color }} />
        <div className="dash-spending-text">
          ${current.toFixed(2)} / ${limit.toFixed(2)} ({pct.toFixed(0)}%)
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CheckboxGroup — multi-select checkboxes
// ---------------------------------------------------------------------------

interface CheckboxGroupProps {
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
  columns?: number;
}

export function CheckboxGroup({ options, selected, onChange, columns = 2 }: CheckboxGroupProps) {
  const toggle = (val: string) => {
    onChange(
      selected.includes(val)
        ? selected.filter((s) => s !== val)
        : [...selected, val],
    );
  };

  return (
    <div className="dash-checkbox-group" style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}>
      {options.map((opt) => (
        <label key={opt.value} className="dash-checkbox-item">
          <input
            type="checkbox"
            checked={selected.includes(opt.value)}
            onChange={() => toggle(opt.value)}
          />
          <span>{opt.label}</span>
        </label>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TagInput — add/remove string tags
// ---------------------------------------------------------------------------

interface TagInputProps {
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  validate?: (value: string) => string | null;
}

export function TagInput({ tags, onChange, placeholder = 'Add...', validate }: TagInputProps) {
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);

  const add = () => {
    const val = input.trim();
    if (!val) return;
    if (validate) {
      const err = validate(val);
      if (err) { setError(err); return; }
    }
    if (!tags.includes(val)) {
      onChange([...tags, val]);
    }
    setInput('');
    setError(null);
  };

  const remove = (val: string) => onChange(tags.filter((t) => t !== val));

  return (
    <div>
      <div className="dash-tag-input">
        <input
          className="dash-form-input"
          value={input}
          onChange={(e) => { setInput(e.target.value); setError(null); }}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          placeholder={placeholder}
        />
        <button className="dash-btn dash-btn-sm dash-btn-primary" onClick={add} type="button">Add</button>
      </div>
      {error && <div className="dash-form-error">{error}</div>}
      {tags.length > 0 && (
        <div className="dash-tag-list">
          {tags.map((tag) => (
            <span key={tag} className="dash-tag">
              {tag}
              <button className="dash-tag-remove" onClick={() => remove(tag)} type="button">&times;</button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// JsonEditor — textarea with JSON validation
// ---------------------------------------------------------------------------

interface JsonEditorProps {
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  rows?: number;
}

export function JsonEditor({ value, onChange, rows = 6 }: JsonEditorProps) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const [prevValue, setPrevValue] = useState(value);

  // Sync text when value prop changes externally (not from our own onChange)
  if (prevValue !== value) {
    setPrevValue(value);
    setText(JSON.stringify(value, null, 2));
    setParseError(null);
  }

  const handleChange = (newText: string) => {
    setText(newText);
    try {
      const parsed = JSON.parse(newText);
      if (typeof parsed === 'object' && parsed !== null) {
        setPrevValue(parsed);
        onChange(parsed);
        setParseError(null);
      } else {
        setParseError('Must be a JSON object');
      }
    } catch {
      setParseError('Invalid JSON');
    }
  };

  return (
    <div>
      <textarea
        className="dash-form-input dash-json-editor"
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        rows={rows}
        spellCheck={false}
      />
      {parseError && <div className="dash-form-error">{parseError}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ImpactConfirmDialog — destructive action with impact preview + name typing
// ---------------------------------------------------------------------------

interface ImpactItem {
  label: string;
  count: number;
}

interface ImpactConfirmDialogProps {
  open: boolean;
  title: string;
  entityName: string;
  impact: ImpactItem[];
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ImpactConfirmDialog({
  open, title, entityName, impact, confirmLabel = 'Delete', onConfirm, onCancel,
}: ImpactConfirmDialogProps) {
  const [typed, setTyped] = useState('');
  const [prevOpen, setPrevOpen] = useState(open);

  if (prevOpen !== open) {
    setPrevOpen(open);
    if (open) setTyped('');
  }

  if (!open) return null;

  const canConfirm = typed === entityName;

  const handleConfirm = () => {
    if (canConfirm) {
      setTyped('');
      onConfirm();
    }
  };

  const handleCancel = () => {
    setTyped('');
    onCancel();
  };

  return (
    <div className="dash-dialog-overlay" onClick={handleCancel}>
      <div className="dash-dialog" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 450 }}>
        <h3 className="dash-dialog-title">{title}</h3>
        {impact.length > 0 && (
          <div className="dash-impact-list">
            <p style={{ fontSize: '0.85rem', opacity: 0.8, margin: '0 0 0.5rem' }}>
              This action will affect:
            </p>
            <ul style={{ margin: '0 0 1rem', paddingLeft: '1.25rem', fontSize: '0.85rem' }}>
              {impact.map((item) => (
                <li key={item.label}>
                  <strong>{item.count}</strong> {item.label}
                </li>
              ))}
            </ul>
          </div>
        )}
        <p style={{ fontSize: '0.85rem', opacity: 0.8, margin: '0 0 0.5rem' }}>
          Type <strong>{entityName}</strong> to confirm:
        </p>
        <input
          className="dash-form-input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={entityName}
          autoFocus
        />
        <div className="dash-dialog-actions" style={{ marginTop: '1rem' }}>
          <button className="dash-btn dash-btn-secondary" onClick={handleCancel}>Cancel</button>
          <button
            className="dash-btn dash-btn-danger"
            onClick={handleConfirm}
            disabled={!canConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
