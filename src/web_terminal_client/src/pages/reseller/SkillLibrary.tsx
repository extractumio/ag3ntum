import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../AuthContext';
import { getSkillLibrary, uploadSkill, deleteLibrarySkill } from '../../adminApi';
import { DataTable, ConfirmDialog, FormField } from '../../components/dashboard';
import type { Column } from '../../components/dashboard';
import type { SkillInfo } from '../../types/admin';

const MAX_CONTENT_BYTES = 51200;

interface UploadForm {
  name: string;
  description: string;
  content: string;
}

const EMPTY_FORM: UploadForm = { name: '', description: '', content: '' };

export function SkillLibrary() {
  const { token, baseUrl } = useAuth();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [form, setForm] = useState<UploadForm>(EMPTY_FORM);
  const [formErrors, setFormErrors] = useState<Partial<UploadForm>>({});
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const fetchSkills = useCallback(() => {
    if (!token || !baseUrl) return;
    getSkillLibrary(baseUrl, token)
      .then((r) => setSkills(r.skills))
      .catch((e: Error) => setError(e.message));
  }, [token, baseUrl]);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const validateForm = (): boolean => {
    const errors: Partial<UploadForm> = {};
    if (!form.name.trim()) {
      errors.name = 'Required';
    } else if (form.name.length > 100) {
      errors.name = 'Maximum 100 characters';
    }
    if (form.description.length > 1000) {
      errors.description = 'Maximum 1000 characters';
    }
    if (!form.content.trim()) {
      errors.content = 'Required';
    } else if (new Blob([form.content]).size > MAX_CONTENT_BYTES) {
      errors.content = 'Content exceeds 50KB limit';
    }
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleUpload = async () => {
    if (!validateForm() || !token || !baseUrl) return;
    setUploading(true);
    setUploadError(null);
    try {
      await uploadSkill(baseUrl, token, {
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        content: form.content,
      });
      setShowUpload(false);
      setForm(EMPTY_FORM);
      setFormErrors({});
      fetchSkills();
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async () => {
    if (!token || !baseUrl || !confirmDelete) return;
    try {
      await deleteLibrarySkill(baseUrl, token, confirmDelete);
      setConfirmDelete(null);
      fetchSkills();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const columns: Column<Record<string, unknown>>[] = [
    { key: 'name', header: 'Name', sortable: true },
    { key: 'source', header: 'Source', sortable: true },
    {
      key: 'content_hash', header: 'Content Hash',
      render: (r) => <code style={{ fontSize: '0.75rem' }}>{(r.content_hash as string).slice(0, 12)}...</code>,
    },
    {
      key: 'created_at', header: 'Created', sortable: true,
      render: (r) => r.created_at ? new Date(r.created_at as string).toLocaleDateString() : '—',
    },
    {
      key: 'actions', header: '',
      render: (r) => (
        <button
          className="dash-btn dash-btn-sm dash-btn-danger"
          onClick={(e) => { e.stopPropagation(); setConfirmDelete(r.name as string); }}
        >
          Delete
        </button>
      ),
    },
  ];

  if (error) return <div className="dash-error">{error}</div>;

  return (
    <div>
      <div className="dash-page-header">
        <h2 className="dash-page-title">Skill Library</h2>
        <button
          className="dash-btn dash-btn-primary"
          onClick={() => { setShowUpload(!showUpload); setForm(EMPTY_FORM); setFormErrors({}); setUploadError(null); }}
        >
          {showUpload ? 'Cancel' : 'Upload Skill'}
        </button>
      </div>

      {showUpload && (
        <div className="dash-section" style={{ marginBottom: '1rem' }}>
          <h3 className="dash-section-title">Upload Skill</h3>
          {uploadError && <div className="dash-error">{uploadError}</div>}

          <FormField label="Name" required error={formErrors.name} hint="1-100 characters">
            <input
              className="dash-form-input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="my-skill"
            />
          </FormField>

          <FormField label="Description" error={formErrors.description} hint="Optional, max 1000 characters">
            <textarea
              className="dash-form-input"
              rows={3}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="What does this skill do?"
            />
          </FormField>

          <FormField label="Content" required error={formErrors.content} hint="Max 50KB">
            <textarea
              className="dash-form-input"
              rows={12}
              value={form.content}
              onChange={(e) => setForm({ ...form, content: e.target.value })}
              placeholder="Paste skill content here..."
              spellCheck={false}
              style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}
            />
          </FormField>

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className="dash-btn dash-btn-primary"
              onClick={handleUpload}
              disabled={uploading}
            >
              {uploading ? 'Uploading...' : 'Upload'}
            </button>
            <button
              className="dash-btn dash-btn-secondary"
              onClick={() => { setShowUpload(false); setForm(EMPTY_FORM); setFormErrors({}); }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <DataTable
        columns={columns}
        data={skills as unknown as Record<string, unknown>[]}
        keyField="name"
        emptyMessage="No skills in library"
      />

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete Skill"
        message={`Delete skill "${confirmDelete}"? This cannot be undone.`}
        confirmLabel="Delete"
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
