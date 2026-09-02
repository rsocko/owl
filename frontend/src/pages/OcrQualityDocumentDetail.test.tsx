import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrQualityDocumentDetail from './OcrQualityDocumentDetail';

const mocks = vi.hoisted(() => ({
  documentDetail: vi.fn(),
  paperlessUrl: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      documentDetail: mocks.documentDetail,
    },
    statements: {
      paperlessUrl: mocks.paperlessUrl,
    },
  },
}));

function renderPage(documentId = '501') {
  return render(
    <MemoryRouter initialEntries={[`/ocr-quality/documents/${documentId}`]}>
      <Routes>
        <Route path="/ocr-quality/documents/:documentId" element={<OcrQualityDocumentDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('OcrQualityDocumentDetail', () => {
  beforeEach(() => {
    mocks.documentDetail.mockReset();
    mocks.paperlessUrl.mockReset();
  });

  it('shows a not-found state for an unknown document', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: 'https://paperless.test' });
    const notFoundError = Object.assign(new Error('Not Found'), { status: 404 });
    mocks.documentDetail.mockRejectedValue(notFoundError);
    renderPage('999');
    await waitFor(() => expect(screen.getByText(/No assessment found/i)).toBeInTheDocument());
  });

  it('renders scores, reasons, page profile, and the not-yet-available stub', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: 'https://paperless.test' });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      document_type: 'Statement',
      correspondent: 'Acme Bank',
      document_created: '2024-05-01',
      overlay_score: 91.2,
      machine_score: 84.0,
      review_status: 'UNCERTAIN',
      downstream_outcome: null,
      quality_scorer_version: '1.0.0',
      assessed_at: '2024-05-02T00:00:00Z',
      preliminary_score: 0.8,
      reasons: [
        { code: 'overlay.low_confidence', message: 'Some pages had low OCR confidence', severity: 'warning', component: 'overlay' },
      ],
      document_profile: {
        page_count: 2,
        dominant_classification: 'scanned_with_overlay',
        content_shape: 'prose',
        pages: [
          { page_number: 1, classification: 'scanned_with_overlay', text_coverage: 0.9, image_coverage: 0.1, char_count: 500, word_count: 90, rotation: 0 },
        ],
      },
    });
    renderPage('501');

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Document #501' })).toBeInTheDocument());
    expect(screen.getByText('UNCERTAIN')).toBeInTheDocument();
    expect(screen.getByText(/Some pages had low OCR confidence/)).toBeInTheDocument();
    expect(screen.getByText(/not yet available/i)).toBeInTheDocument();
    expect(screen.getByText(/Open in Paperless/i).closest('a')).toHaveAttribute(
      'href',
      'https://paperless.test/documents/501/details',
    );
  });

  it('shows an unavailable-signal note when overlay score has not been computed', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 502,
      overlay_score: null,
      machine_score: 70.0,
      review_status: 'GOOD',
      reasons: [],
      document_profile: null,
    });
    renderPage('502');
    await waitFor(() => expect(screen.getByText(/Overlay score is unavailable/i)).toBeInTheDocument());
  });
});
