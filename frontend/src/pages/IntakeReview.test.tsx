import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import IntakeReview from './IntakeReview';

const {
  documentsMock,
  documentMock,
  saveDocumentMock,
  refreshMock,
  createTaxonomyMock,
  createActionMock,
  deleteActionMock,
  updateActionMock,
} = vi.hoisted(() => ({
  documentsMock: vi.fn(),
  documentMock: vi.fn(),
  saveDocumentMock: vi.fn(),
  refreshMock: vi.fn(),
  createTaxonomyMock: vi.fn(),
  createActionMock: vi.fn(),
  deleteActionMock: vi.fn(),
  updateActionMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    intake: {
      documents: documentsMock,
      document: documentMock,
      saveDocument: saveDocumentMock,
      refresh: refreshMock,
      createTaxonomy: createTaxonomyMock,
      createAction: createActionMock,
      deleteAction: deleteActionMock,
    },
    actionQueue: {
      updateAction: updateActionMock,
    },
  },
}));

function detail(id: number) {
  return {
    document: {
      id,
      title: `Document ${id}`,
      created: '2026-10-14',
      modified: `revision-${id}`,
      correspondent: 1,
      document_type: 1,
      storage_path: 1,
      tags: [1],
      custom_fields: [],
    },
    taxonomies: {
      tags: [{ id: 1, name: 'Inbox' }],
      correspondents: [{ id: 1, name: 'Example Co' }],
      document_types: [{ id: 1, name: 'Bill' }],
      storage_paths: [{ id: 1, name: 'Household' }],
      custom_fields: [],
    },
    suggestions: [],
    suggestion_sources: {
      paperless_ml: { available: true },
      paperless_ai: { available: true },
    },
    actions: [],
    ocr: {
      quality_score: 72,
      quality_band: 'red',
      poor_quality: true,
      text: 'Searchable text',
    },
    analysis: { available: true, source_modified: `revision-${id}` },
    preview_url: `/preview/${id}`,
    write_enabled: true,
    monitor_tags: ['Inbox'],
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/intake-review']}>
      <Routes>
        <Route path="/intake-review" element={<IntakeReview />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('IntakeReview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    documentsMock.mockResolvedValue({
      documents: [
        { id: 1, title: 'Document 1', created: '2026-10-14', document_summary: {} },
        { id: 2, title: 'Document 2', created: '2026-10-15', document_summary: {} },
      ],
      total: 2,
      monitor_tags: ['Inbox'],
    });
    documentMock.mockImplementation((id: number) => Promise.resolve(detail(id)));
  });

  it('moves list focus without changing the active document until Enter', async () => {
    renderPage();

    const first = await screen.findByRole('button', { name: /Document 1/ });
    const second = screen.getByRole('button', { name: /Document 2/ });
    await waitFor(() => expect(documentMock).toHaveBeenCalledWith(1));

    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(second).toHaveFocus();
    expect(documentMock).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(second, { key: 'Enter' });
    await waitFor(() => expect(documentMock).toHaveBeenCalledWith(2));
  });

  it('shows an OCR warning before the OCR tab is opened', async () => {
    renderPage();

    expect(await screen.findByRole('tab', { name: 'OCR, needs review' })).toBeInTheDocument();
    expect(screen.queryByText('Poor OCR can make metadata and action analysis stale or incomplete.')).not.toBeInTheDocument();
  });
});
