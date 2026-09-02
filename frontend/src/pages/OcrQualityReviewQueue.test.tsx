import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrQualityReviewQueue from './OcrQualityReviewQueue';

const mocks = vi.hoisted(() => ({
  documents: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      documents: mocks.documents,
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
});
