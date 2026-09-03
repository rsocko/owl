import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AnnotationListPanel from './AnnotationListPanel';
import type { Annotation } from './RegionOverlayViewer';

const annotations: Annotation[] = [
  { id: 1, document_id: 501, page: 1, x0: 1, top: 1, x1: 2, bottom: 2, label: 'wrong', note: 'looks off', created_by: 'reviewer1' },
  { id: 2, document_id: 501, page: 2, x0: 3, top: 3, x1: 4, bottom: 4, label: 'key_data', note: null },
];

describe('AnnotationListPanel', () => {
  it('shows an empty state when there are no annotations', () => {
    render(<AnnotationListPanel annotations={[]} />);
    expect(screen.getByText(/No manual annotations yet/i)).toBeInTheDocument();
  });

  it('lists each annotation with label, page, note, and author', () => {
    render(<AnnotationListPanel annotations={annotations} />);
    expect(screen.getByText('wrong')).toBeInTheDocument();
    expect(screen.getByText('looks off')).toBeInTheDocument();
    expect(screen.getByText('by reviewer1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Page 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Page 2' })).toBeInTheDocument();
  });

  it('calls onSelectPage when a page link is clicked', () => {
    const onSelectPage = vi.fn();
    render(<AnnotationListPanel annotations={annotations} onSelectPage={onSelectPage} />);
    fireEvent.click(screen.getByRole('button', { name: 'Page 2' }));
    expect(onSelectPage).toHaveBeenCalledWith(2);
  });

  it('calls onDelete when Delete is clicked', () => {
    const onDelete = vi.fn();
    render(<AnnotationListPanel annotations={annotations} onDelete={onDelete} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    expect(onDelete).toHaveBeenCalledWith(1);
  });

  it('supports editing a label and note inline', async () => {
    const onUpdate = vi.fn().mockResolvedValue(undefined);
    render(<AnnotationListPanel annotations={annotations} onUpdate={onUpdate} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]);
    const select = screen.getAllByRole('combobox')[0];
    fireEvent.change(select, { target: { value: 'table_region' } });
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, { target: { value: 'updated note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onUpdate).toHaveBeenCalledWith(1, { label: 'table_region', note: 'updated note' });
  });
});
