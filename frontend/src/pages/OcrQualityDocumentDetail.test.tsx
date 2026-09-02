import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrQualityDocumentDetail from './OcrQualityDocumentDetail';

const mocks = vi.hoisted(() => ({
  documentDetail: vi.fn(),
  paperlessUrl: vi.fn(),
  metadata: vi.fn(),
  thumbnailUrl: vi.fn(),
  downloadUrl: vi.fn(),
  previewUrl: vi.fn(),
  candidatesList: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      documentDetail: mocks.documentDetail,
      candidates: {
        list: mocks.candidatesList,
        get: vi.fn(),
        text: vi.fn(),
        request: vi.fn(),
        decide: vi.fn(),
        cancel: vi.fn(),
      },
    },
    statements: {
      paperlessUrl: mocks.paperlessUrl,
    },
    documents: {
      metadata: mocks.metadata,
      thumbnailUrl: mocks.thumbnailUrl,
      downloadUrl: mocks.downloadUrl,
      previewUrl: mocks.previewUrl,
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
    mocks.metadata.mockReset();
    mocks.thumbnailUrl.mockReset();
    mocks.downloadUrl.mockReset();
    mocks.previewUrl.mockReset();
    mocks.candidatesList.mockReset();
    mocks.candidatesList.mockResolvedValue({ candidates: [] });
    mocks.metadata.mockResolvedValue({ title: 'Statement.pdf', page_count: 2 });
    mocks.thumbnailUrl.mockReturnValue('/thumbnail/501');
    mocks.downloadUrl.mockReturnValue('/download/501');
    mocks.previewUrl.mockReturnValue('/preview/501');
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

  it('renders an inline document preview with a Paperless deep link', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: 'https://paperless.test' });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      review_status: 'UNCERTAIN',
      reasons: [],
      document_profile: null,
    });
    renderPage('501');

    await waitFor(() => expect(screen.getByText('Document preview')).toBeInTheDocument());
    expect(mocks.metadata).toHaveBeenCalledWith(501);
    expect(screen.getByRole('button', { name: /View Document/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /View in Paperless/i })).toHaveAttribute(
      'href',
      'https://paperless.test/documents/501/details',
    );
  });

  it('expands the document preview into the full viewer modal on click', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: 'https://paperless.test' });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      review_status: 'UNCERTAIN',
      reasons: [],
      document_profile: null,
    });
    renderPage('501');

    const viewButton = await screen.findByRole('button', { name: /View Document/i });
    fireEvent.click(viewButton);

    const iframe = await screen.findByTitle('Statement.pdf');
    expect(iframe).toHaveAttribute('src', '/preview/501');
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
