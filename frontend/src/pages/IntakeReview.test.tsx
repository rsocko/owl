import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import IntakeReview from './IntakeReview';

const {
  documentsMock,
  documentCoreMock,
  documentSuggestionsMock,
  documentActionsMock,
  documentOcrMock,
  saveDocumentMock,
  refreshMock,
  createTaxonomyMock,
  createActionMock,
  deleteActionMock,
  updateActionMock,
} = vi.hoisted(() => ({
  documentsMock: vi.fn(),
  documentCoreMock: vi.fn(),
  documentSuggestionsMock: vi.fn(),
  documentActionsMock: vi.fn(),
  documentOcrMock: vi.fn(),
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
      documentCore: documentCoreMock,
      documentSuggestions: documentSuggestionsMock,
      documentActions: documentActionsMock,
      documentOcr: documentOcrMock,
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

function core(id: number) {
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
      review_fields: [],
    },
    taxonomies: {
      tags: [{ id: 1, name: 'Inbox' }],
      correspondents: [{ id: 1, name: 'Example Co' }],
      document_types: [{ id: 1, name: 'Bill' }],
      storage_paths: [{ id: 1, name: 'Household' }],
      custom_fields: [],
    },
    preview_url: `/preview/${id}`,
    write_enabled: true,
    monitor_tags: ['Inbox'],
  };
}

function renderPage(entry = '/intake-review') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
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
        { id: 1, title: 'Document 1', created: '2026-10-14', thumbnail_url: '/thumb/1', pending_action_count: 0 },
        { id: 2, title: 'Document 2', created: '2026-10-15', thumbnail_url: '/thumb/2', pending_action_count: 1 },
      ],
      total: 2,
      monitor_tags: ['Inbox'],
    });
    documentCoreMock.mockImplementation((id: number) => Promise.resolve(core(id)));
    documentSuggestionsMock.mockResolvedValue({
      suggestions: [],
      suggestion_sources: {
        paperless_ml: { available: true },
        paperless_ai: { available: true },
      },
      cached: true,
    });
    documentActionsMock.mockResolvedValue({
      actions: [],
      analysis: { available: true },
      ocr_quality: { quality_score: 72, quality_band: 'red', poor_quality: true },
    });
    documentOcrMock.mockResolvedValue({
      quality_score: 72,
      quality_band: 'red',
      poor_quality: true,
      text: 'Searchable text',
    });
  });

  it('moves list focus without changing the active document until Enter', async () => {
    renderPage();

    const first = await screen.findByRole('button', { name: /Document 1/ });
    const second = screen.getByRole('button', { name: /Document 2/ });
    await waitFor(() => expect(documentCoreMock).toHaveBeenCalledWith(1));

    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(second).toHaveFocus();
    expect(documentCoreMock).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(second, { key: 'Enter' });
    await waitFor(() => expect(documentCoreMock).toHaveBeenCalledWith(2));
  });

  it('shows an OCR warning before the OCR tab is opened', async () => {
    renderPage();

    expect(await screen.findByRole('tab', { name: 'OCR, needs review' })).toBeInTheDocument();
    expect(screen.queryByText('Poor OCR can make metadata and action analysis stale or incomplete.')).not.toBeInTheDocument();
  });

  it('selects a document and tab from the query string', async () => {
    renderPage('/intake-review?document=2&tab=actions');

    await waitFor(() => expect(documentCoreMock).toHaveBeenCalledWith(2));
    expect(await screen.findByRole('tab', { name: /Actions/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Document 2', { selector: 'h2' })).toBeInTheDocument();
  });

  it('shows the selected preview while metadata loads progressively', async () => {
    documentCoreMock.mockImplementation(() => new Promise(() => undefined));
    renderPage();

    const preview = await screen.findByTitle('Preview of Document 1');
    expect(preview).toHaveAttribute('src', expect.stringContaining('/api/statements/documents/1/preview'));
    expect(screen.getByText('Document 1', { selector: 'h2' })).toBeInTheDocument();
    expect(documentCoreMock).toHaveBeenCalledWith(1);
  });

  it('filters the queue by documents with pending actions', async () => {
    renderPage();
    await screen.findByRole('button', { name: /Document 1/ });

    fireEvent.click(screen.getByRole('button', { name: 'Action needed' }));
    expect(screen.queryByRole('button', { name: /Document 1/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Document 2/ })).toBeInTheDocument();
  });
});
