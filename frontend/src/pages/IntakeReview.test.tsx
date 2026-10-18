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
  documentRelationshipsMock,
  relationshipCandidatesMock,
  createRelationshipMock,
  removeRelationshipMock,
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
  documentRelationshipsMock: vi.fn(),
  relationshipCandidatesMock: vi.fn(),
  createRelationshipMock: vi.fn(),
  removeRelationshipMock: vi.fn(),
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
      documentRelationships: documentRelationshipsMock,
      relationshipCandidates: relationshipCandidatesMock,
      saveDocument: saveDocumentMock,
      refresh: refreshMock,
      createTaxonomy: createTaxonomyMock,
      createAction: createActionMock,
      deleteAction: deleteActionMock,
    },
    actionQueue: {
      updateAction: updateActionMock,
    },
    relationships: {
      create: createRelationshipMock,
      remove: removeRelationshipMock,
    },
    documents: {
      previewUrl: (id: number) => `/api/statements/documents/${id}/preview`,
      downloadUrl: (id: number) => `/api/documents/${id}/download`,
      monarchDelivery: {
        status: vi.fn().mockResolvedValue(null),
        send: vi.fn(),
        retry: vi.fn(),
        reconcile: vi.fn(),
      },
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
    paperless_url: `https://paperless.test/documents/${id}/details`,
    write_enabled: true,
  };
}

function renderPage(entry = '/inbox-review') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/inbox-review" element={<IntakeReview />} />
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
      next_cursor: null,
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
    documentRelationshipsMock.mockResolvedValue({
      relationships: [],
      count: 0,
    });
    relationshipCandidatesMock.mockResolvedValue({
      candidates: [],
      query: '',
    });
    saveDocumentMock.mockResolvedValue({ filed: false });
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
    renderPage('/inbox-review?document=2&tab=actions');

    await waitFor(() => expect(documentCoreMock).toHaveBeenCalledWith(2));
    expect(await screen.findByRole('tab', { name: /Actions/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Document 2', { selector: 'h2' })).toBeInTheDocument();
  });

  it('shows only fields relevant to a review action', async () => {
    documentActionsMock.mockResolvedValue({
      actions: [{
        id: 11,
        document_id: 1,
        action_type: 'REVIEW',
        title: 'Review policy changes',
        summary: 'Decide whether the new terms are acceptable.',
        payee_or_owner: 'Should not be shown',
        amount: 125,
        due_date: '2026-10-31',
        reference: 'Should not be shown',
        status: 'pending',
        version: 1,
        origin: 'inferred',
        confidence: 95,
      }],
      analysis: { available: true },
      ocr_quality: { quality_score: 90, quality_band: 'green', poor_quality: false },
    });

    renderPage('/inbox-review?document=1&tab=actions');

    expect(await screen.findByLabelText('Details')).toHaveValue(
      'Decide whether the new terms are acceptable.',
    );
    expect(screen.getByLabelText('Review by')).toHaveValue('2026-10-31');
    expect(screen.queryByLabelText('Amount')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Payee')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Invoice or reference')).not.toBeInTheDocument();
  });

  it('clears fields that do not apply when an action type changes', async () => {
    const action = {
      id: 12,
      document_id: 1,
      action_type: 'PAY',
      title: 'Pay utility bill',
      summary: null,
      payee_or_owner: 'Utility Co',
      amount: 84.2,
      due_date: '2026-10-25',
      reference: 'INV-42',
      status: 'pending',
      version: 1,
      origin: 'inferred',
      confidence: 95,
    };
    documentActionsMock.mockResolvedValue({
      actions: [action],
      analysis: { available: true },
      ocr_quality: { quality_score: 90, quality_band: 'green', poor_quality: false },
    });
    updateActionMock.mockResolvedValue({
      ...action,
      action_type: 'REVIEW',
      summary: 'Check the disputed charge.',
      payee_or_owner: null,
      amount: null,
      reference: null,
      version: 2,
    });

    renderPage('/inbox-review?document=1&tab=actions');

    fireEvent.change(await screen.findByLabelText('Type'), { target: { value: 'REVIEW' } });
    fireEvent.change(screen.getByLabelText('Details'), {
      target: { value: 'Check the disputed charge.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save action' }));

    await waitFor(() => expect(updateActionMock).toHaveBeenCalledWith('12', {
      version: 1,
      action_type: 'REVIEW',
      title: 'Pay utility bill',
      summary: 'Check the disputed charge.',
      payee_or_owner: null,
      amount: null,
      due_date: '2026-10-25',
      reference: null,
    }));
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

  it('searches the full inbox through the paged API', async () => {
    renderPage();
    await screen.findByRole('button', { name: /Document 1/ });

    fireEvent.change(screen.getByRole('textbox', { name: 'Search intake documents' }), {
      target: { value: 'utility' },
    });

    await waitFor(() => expect(documentsMock).toHaveBeenLastCalledWith({
      pageSize: 100,
      cursor: null,
      search: 'utility',
      filter: 'all',
    }));
  });

  it('shows suggested related documents and opens their PDF without leaving the workspace', async () => {
    relationshipCandidatesMock.mockResolvedValue({
      candidates: [{
        document: {
          id: 7,
          title: 'September utility statement',
          created: '2026-09-14',
          correspondent_name: 'Example Co',
          document_type_name: 'Bill',
          thumbnail_url: '/thumb/7',
          preview_url: '/preview/7',
        },
        suggested: true,
        recommendation: {
          source_document_id: 1,
          target_document_id: 7,
          relationship_type: 'same_sequence',
          confidence: 0.58,
          reason_codes: ['correspondent_match', 'document_type_match'],
          priority_adjustment: 0,
          priority_explanation: 'No relationship priority adjustment',
        },
      }],
      query: '',
    });
    renderPage('/inbox-review?document=1&tab=related');

    expect(await screen.findByText('September utility statement')).toBeInTheDocument();
    expect(screen.getAllByText('Suggested')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Preview September utility statement' }));
    expect(await screen.findByTitle('September utility statement')).toHaveAttribute(
      'src',
      '/api/statements/documents/7/preview',
    );
  });

  it('adds a suggested relationship using its recommended type', async () => {
    relationshipCandidatesMock.mockResolvedValue({
      candidates: [{
        document: {
          id: 7,
          title: 'September utility statement',
          created: '2026-09-14',
          correspondent_name: 'Example Co',
          document_type_name: 'Bill',
          thumbnail_url: '/thumb/7',
          preview_url: '/preview/7',
        },
        suggested: true,
        recommendation: {
          source_document_id: 1,
          target_document_id: 7,
          relationship_type: 'same_sequence',
          confidence: 0.58,
          reason_codes: ['correspondent_match', 'document_type_match'],
          priority_adjustment: 0,
          priority_explanation: 'No relationship priority adjustment',
        },
      }],
      query: '',
    });
    createRelationshipMock.mockResolvedValue({ created: true });
    renderPage('/inbox-review?document=1&tab=related');

    fireEvent.click(await screen.findByRole('button', { name: 'Add link' }));

    await waitFor(() => expect(createRelationshipMock).toHaveBeenCalledWith(
      expect.objectContaining({
        source_document_id: 1,
        target_document_id: 7,
        relationship_type: 'same_sequence',
        provenance: 'user',
      }),
    ));
  });

  it('uses Paperless tag colors inside one combined tag control', async () => {
    documentCoreMock.mockResolvedValue({
      ...core(1),
      taxonomies: {
        ...core(1).taxonomies,
        tags: [{ id: 1, name: 'Inbox', colour: '#1f6feb' }],
      },
    });
    renderPage();

    const tag = await screen.findByText('Inbox');
    expect(tag).toHaveStyle({ backgroundColor: '#1f6feb', color: '#ffffff' });
    expect(tag.closest('.intake-tag-control')).not.toBeNull();
    expect(screen.getByRole('combobox', { name: 'Add tag' })).toBeInTheDocument();
  });

  it('formats document amounts as currency', async () => {
    documentCoreMock.mockResolvedValue({
      ...core(1),
      document: {
        ...core(1).document,
        review_fields: [{
          field: 10,
          name: 'Document Amount',
          value: 1234.5,
          data_type: 'float',
          editable: true,
          audit: false,
        }],
      },
    });
    renderPage();

    expect(await screen.findByLabelText('Document Amount')).toHaveValue('1,234.50');
  });

  it('shows and saves the full editable account identifier with its type', async () => {
    documentCoreMock.mockResolvedValue({
      ...core(1),
      document: {
        ...core(1).document,
        review_fields: [{
          field: 9,
          field_key: 'account_identifier',
          name: 'Account Identifier',
          value: 'ACCT-2025-8675309',
          data_type: 'string',
          editable: true,
          audit: false,
        }],
      },
    });
    renderPage();

    const identifier = await screen.findByLabelText('Account Identifier');
    expect(identifier).toHaveValue('ACCT-2025-8675309');
    fireEvent.change(identifier, { target: { value: 'ACCT-2025-1234567' } });
    fireEvent.change(screen.getByLabelText('Identifier type'), {
      target: { value: 'provider_account' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(saveDocumentMock).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        metadata: expect.objectContaining({
          custom_fields: [{
            field: 9,
            value: 'ACCT-2025-1234567',
            identifier_class: 'provider_account',
          }],
        }),
      }),
    ));
  });

  it('opens the document preview full screen and exposes external links', async () => {
    renderPage();

    await screen.findByRole('link', { name: /Open in Paperless/ });
    fireEvent.click(await screen.findByRole('button', { name: 'Expand' }));
    expect(screen.getByRole('dialog', { name: 'Expanded preview of Document 1' })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Paperless/ })[0]).toHaveAttribute(
      'href',
      'https://paperless.test/documents/1/details',
    );
    expect(screen.getAllByRole('link', { name: /New tab/ })[0]).toHaveAttribute(
      'href',
      expect.stringContaining('/preview/1'),
    );
  });

  it('keeps the active document selected after it stops matching the missing-type filter', async () => {
    documentsMock.mockImplementation(() => Promise.resolve(
      saveDocumentMock.mock.calls.length === 0 ? {
        documents: [
          { id: 1, title: 'Document 1', created: '2026-10-14', thumbnail_url: '/thumb/1', pending_action_count: 0 },
          { id: 2, title: 'Document 2', created: '2026-10-15', thumbnail_url: '/thumb/2', pending_action_count: 0 },
        ],
        total: 2,
        next_cursor: null,
      } : {
        documents: [
          { id: 1, title: 'Document 1', created: '2026-10-14', thumbnail_url: '/thumb/1', pending_action_count: 0, document_type_name: 'Bill' },
          { id: 2, title: 'Document 2', created: '2026-10-15', thumbnail_url: '/thumb/2', pending_action_count: 0 },
        ],
        total: 2,
        next_cursor: null,
      },
    ));
    documentCoreMock.mockImplementation((id: number) => Promise.resolve({
      ...core(id),
      document: { ...core(id).document, document_type: id === 1 ? null : 1 },
    }));
    documentSuggestionsMock.mockResolvedValue({
      suggestions: [{ field: 'document_type', source: 'paperless_ml', id: 1, name: 'Bill' }],
      suggestion_sources: { paperless_ml: { available: true }, paperless_ai: { available: true } },
      cached: true,
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Missing type' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Bill' }));
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(documentCoreMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('button', { name: /Document 1/ })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByText('Document 1', { selector: 'h2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Document 2/ })).toBeInTheDocument();
  });

  it('shows the full Paperless total and progressively loads another queue page', async () => {
    documentsMock
      .mockResolvedValueOnce({
        documents: [
          { id: 1, title: 'Document 1', created: '2026-10-14', thumbnail_url: '/thumb/1', pending_action_count: 0 },
        ],
        total: 213,
        next_cursor: '2',
      })
      .mockResolvedValueOnce({
        documents: [
          { id: 2, title: 'Document 2', created: '2026-10-15', thumbnail_url: '/thumb/2', pending_action_count: 0 },
        ],
        total: 213,
        next_cursor: '3',
      });

    renderPage();

    expect(await screen.findByText('213 awaiting review')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load more (1 of 213 loaded)' }));

    expect(await screen.findByRole('button', { name: /Document 2/ })).toBeInTheDocument();
    expect(documentsMock).toHaveBeenNthCalledWith(2, {
      pageSize: 100,
      cursor: '2',
      search: '',
      filter: 'all',
    });
  });
});
