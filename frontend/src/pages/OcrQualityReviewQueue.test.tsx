import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrQualityReviewQueue from './OcrQualityReviewQueue';

const mocks = vi.hoisted(() => ({
  documents: vi.fn(),
  downstreamOutcomes: vi.fn(),
  documentTypes: vi.fn(),
  metadataCorrespondents: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      documents: mocks.documents,
      downstreamOutcomes: mocks.downstreamOutcomes,
    },
    admin: {
      documentTypes: mocks.documentTypes,
    },
    actionQueue: {
      metadataCorrespondents: mocks.metadataCorrespondents,
    },
  },
}));

function renderPage(initialEntries: string[] = ['/ocr-quality/queue']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/ocr-quality/queue" element={<OcrQualityReviewQueue />} />
        <Route path="/ocr-quality/documents/:documentId" element={<div>Detail page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('OcrQualityReviewQueue', () => {
  beforeEach(() => {
    mocks.documents.mockReset();
    mocks.downstreamOutcomes.mockReset();
    mocks.documentTypes.mockReset();
    mocks.metadataCorrespondents.mockReset();
    mocks.downstreamOutcomes.mockResolvedValue({ downstream_outcomes: ['action_created', 'no_action_needed'] });
    mocks.documentTypes.mockResolvedValue({
      types: [
        { id: 3, name: 'Bank Statement' },
        { id: 2, name: 'Invoice' },
      ],
    });
    mocks.metadataCorrespondents.mockResolvedValue({
      correspondents: [
        { id: 12, name: 'Acme Corp' },
        { id: 391, name: 'Contoso LLC' },
      ],
    });
  });

  it('shows an empty state when no documents match', async () => {
    mocks.documents.mockResolvedValue({ documents: [], total: 0, limit: 25, offset: 0 });
    renderPage();
    await waitFor(() => expect(screen.getByText(/No documents match these filters/i)).toBeInTheDocument());
  });

  it('renders an error state and retries', async () => {
    mocks.documents.mockRejectedValueOnce(new Error('network down'));
    renderPage();
    await waitFor(() => expect(screen.getByText('network down')).toBeInTheDocument());
  });

  it('renders documents in a table and navigates to detail on row click', async () => {
    mocks.documents.mockResolvedValue({
      documents: [
        {
          document_id: 501,
          document_type: 'Statement',
          correspondent: 'Acme Bank',
          document_created: '2024-05-01',
          overlay_score: 92.5,
          machine_score: 88.1,
          review_status: 'GOOD',
          downstream_outcome: 'reviewed',
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('#501')).toBeInTheDocument());
    expect(screen.getByText('Acme Bank')).toBeInTheDocument();
    const row = screen.getByText('#501').closest('tr')!;
    expect(within(row).getByText('GOOD')).toBeInTheDocument();

    fireEvent.click(screen.getByText('#501'));
    await waitFor(() => expect(screen.getByText('Detail page')).toBeInTheDocument());
  });

  it('applies the review_status filter from the URL on initial load', async () => {
    mocks.documents.mockResolvedValue({ documents: [], total: 0, limit: 25, offset: 0 });
    renderPage(['/ocr-quality/queue?review_status=FAILED']);
    await waitFor(() => expect(mocks.documents).toHaveBeenCalled());
    const calledWith = mocks.documents.mock.calls[0][0] as string;
    expect(calledWith).toContain('review_status=FAILED');
  });

  it('resolves document type and correspondent IDs to human-readable names', async () => {
    mocks.documents.mockResolvedValue({
      documents: [
        {
          document_id: 9704,
          document_type: '3',
          correspondent: '12',
          document_created: '2026-07-16',
          overlay_score: 100,
          machine_score: 77.4,
          review_status: 'UNCERTAIN',
          downstream_outcome: null,
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Bank Statement')).toBeInTheDocument());
    expect(screen.getByText('Acme Corp')).toBeInTheDocument();
    expect(screen.queryByText('3')).not.toBeInTheDocument();
    expect(screen.queryByText('12')).not.toBeInTheDocument();
  });

  it('falls back to showing the raw ID when a name cannot be resolved', async () => {
    mocks.documents.mockResolvedValue({
      documents: [
        {
          document_id: 42,
          document_type: '999',
          correspondent: '888',
          document_created: '2026-01-01',
          overlay_score: 50,
          machine_score: 50,
          review_status: 'GOOD',
          downstream_outcome: null,
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('#42')).toBeInTheDocument());
    expect(screen.getByText('999')).toBeInTheDocument();
    expect(screen.getByText('888')).toBeInTheDocument();
  });

  it('sorts by clicking a column header and toggles direction on repeated clicks', async () => {
    mocks.documents.mockResolvedValue({
      documents: [
        {
          document_id: 501,
          document_type: '3',
          correspondent: '12',
          document_created: '2024-05-01',
          overlay_score: 92.5,
          machine_score: 88.1,
          review_status: 'GOOD',
          downstream_outcome: null,
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
    });
    renderPage();
    await waitFor(() => expect(mocks.documents).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /Type/i }));
    await waitFor(() => expect(mocks.documents).toHaveBeenCalledTimes(2));
    let calledWith = mocks.documents.mock.calls[1][0] as string;
    expect(calledWith).toContain('sort_by=document_type');
    expect(calledWith).toContain('sort_dir=desc');

    fireEvent.click(screen.getByRole('button', { name: /Type/i }));
    await waitFor(() => expect(mocks.documents).toHaveBeenCalledTimes(3));
    calledWith = mocks.documents.mock.calls[2][0] as string;
    expect(calledWith).toContain('sort_by=document_type');
    expect(calledWith).toContain('sort_dir=asc');

    fireEvent.click(screen.getByRole('button', { name: /Type/i }));
    await waitFor(() => expect(mocks.documents).toHaveBeenCalledTimes(4));
    calledWith = mocks.documents.mock.calls[3][0] as string;
    expect(calledWith).not.toContain('sort_by');
  });

  it('renders searchable dropdowns for document type and correspondent filters', async () => {
    mocks.documents.mockResolvedValue({ documents: [], total: 0, limit: 25, offset: 0 });
    renderPage();
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Document type' })).toBeInTheDocument());
    expect(screen.getByRole('combobox', { name: 'Correspondent' })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('combobox', { name: 'Document type' }), { target: { value: 'Bank' } });
    await waitFor(() => expect(screen.getByRole('option', { name: 'Bank Statement' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('option', { name: 'Bank Statement' }));

    await waitFor(() => {
      const calledWith = mocks.documents.mock.calls.at(-1)?.[0] as string;
      expect(calledWith).toContain('document_type=3');
    });
  });

  it('labels the Action Queue outcome and filters by it', async () => {
    mocks.documents.mockResolvedValue({ documents: [], total: 0, limit: 25, offset: 0 });
    renderPage();
    await waitFor(() => expect(mocks.downstreamOutcomes).toHaveBeenCalled());
    const select = await screen.findByLabelText('Action Queue outcome') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'no_action_needed' } });
    await waitFor(() => {
      const calledWith = mocks.documents.mock.calls.at(-1)?.[0] as string;
      expect(calledWith).toContain('downstream_outcome=no_action_needed');
    });
  });

  it('filters by OCR resolution status', async () => {
    mocks.documents.mockResolvedValue({ documents: [], total: 0, limit: 25, offset: 0 });
    renderPage();
    const select = await screen.findByLabelText('OCR resolution') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'true' } });
    await waitFor(() => {
      const calledWith = mocks.documents.mock.calls.at(-1)?.[0] as string;
      expect(calledWith).toContain('resolved=true');
    });
  });

  it('shows OCR resolution in its own column with the acceptance date', async () => {
    mocks.documents.mockResolvedValue({
      documents: [
        {
          document_id: 501,
          review_status: 'FAILED',
          has_accepted_ocr_candidate: true,
          accepted_candidate_at: '2026-09-04T15:00:00',
        },
        {
          document_id: 502,
          review_status: 'FAILED',
          has_accepted_ocr_candidate: false,
        },
      ],
      total: 2,
      limit: 25,
      offset: 0,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('#501')).toBeInTheDocument());

    expect(screen.getByRole('columnheader', { name: 'OCR resolution' })).toBeInTheDocument();
    const resolvedRow = screen.getByText('#501').closest('tr')!;
    expect(within(resolvedRow).getByText('Resolved')).toBeInTheDocument();
    expect(within(resolvedRow).getByText('2026-09-04')).toBeInTheDocument();
    const unresolvedRow = screen.getByText('#502').closest('tr')!;
    expect(within(unresolvedRow).getByText('Unresolved')).toBeInTheDocument();
  });
});
