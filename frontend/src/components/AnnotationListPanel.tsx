import { useState } from 'react';
import { Badge, Button } from './ui';
import type { Annotation } from './RegionOverlayViewer';

interface AnnotationListPanelProps {
  annotations: Annotation[];
  onUpdate?: (annotationId: number, updates: Partial<Pick<Annotation, 'label' | 'note'>>) => void | Promise<void>;
  onDelete?: (annotationId: number) => void | Promise<void>;
  onSelectPage?: (page: number) => void;
}

/**
 * List/manage panel for reviewer-drawn annotations on a document (issue
 * #134, Part 2). Shown alongside `RegionOverlayViewer`, which renders the
 * same annotations as boxes on the page image.
 */
export default function AnnotationListPanel({
  annotations,
  onUpdate,
  onDelete,
  onSelectPage,
}: AnnotationListPanelProps) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editLabel, setEditLabel] = useState('');
  const [editNote, setEditNote] = useState('');

  if (annotations.length === 0) {
    return <div className="text-muted">No manual annotations yet. Draw a box on the page image to add one.</div>;
  }

  const startEdit = (ann: Annotation) => {
    setEditingId(ann.id);
    setEditLabel(ann.label);
    setEditNote(ann.note ?? '');
  };

  const saveEdit = async (id: number) => {
    if (onUpdate) {
      await onUpdate(id, { label: editLabel, note: editNote.trim() ? editNote.trim() : null });
    }
    setEditingId(null);
  };

  return (
    <ul className="ocr-annotation-list" data-testid="annotation-list">
      {annotations.map((ann) => (
        <li key={ann.id} className="ocr-annotation-item">
          {editingId === ann.id ? (
            <div className="ocr-annotation-edit-form">
              <select value={editLabel} onChange={(e) => setEditLabel(e.target.value)}>
                {['wrong', 'key_data', 'table_region', 'other'].map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <textarea rows={2} value={editNote} onChange={(e) => setEditNote(e.target.value)} />
              <div>
                <Button size="sm" onClick={() => saveEdit(ann.id)}>
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <>
              <Badge tone="warn">{ann.label}</Badge>
              <button
                type="button"
                className="ocr-annotation-page-link"
                onClick={() => onSelectPage?.(ann.page)}
              >
                Page {ann.page}
              </button>
              {ann.note && <span className="ocr-annotation-note">{ann.note}</span>}
              {ann.created_by && <span className="text-muted">by {ann.created_by}</span>}
              <div className="ocr-annotation-actions">
                {onUpdate && (
                  <Button size="sm" variant="ghost" onClick={() => startEdit(ann)}>
                    Edit
                  </Button>
                )}
                {onDelete && (
                  <Button size="sm" variant="ghost" onClick={() => onDelete(ann.id)}>
                    Delete
                  </Button>
                )}
              </div>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
