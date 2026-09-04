import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrQualityDocumentDetail from './OcrQualityDocumentDetail';

const mocks = vi.hoisted(() => ({
  documentDetail: vi.fn(),
  forceStage2: vi.fn(),
  paperlessUrl: vi.fn(),
  metadata: vi.fn(),
  thumbnailUrl: vi.fn(),
  downloadUrl: vi.fn(),
  previewUrl: vi.fn(),
  candidatesList: vi.fn(),
  candidateRegions: vi.fn(),
  candidatePageImageUrl: vi.fn(),
  regions: vi.fn(),
  pageImageUrl: vi.fn(),
  regionsDiff: vi.fn(),
  annotationsList: vi.fn(),
  annotationsCreate: vi.fn(),
  annotationsUpdate: vi.fn(),
  annotationsRemove: vi.fn(),
  metadataGet: vi.fn(),
  metadataCorrect: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      documentDetail: mocks.documentDetail,
      forceStage2: mocks.forceStage2,
      candidates: {
        list: mocks.candidatesList,
        get: vi.fn(),
        text: vi.fn(),
        request: vi.fn(),
        decide: vi.fn(),
        cancel: vi.fn(),
        regions: mocks.candidateRegions,
        pageImageUrl: mocks.candidatePageImageUrl,
      },
      regions: mocks.regions,
      pageImageUrl: mocks.pageImageUrl,
      regionsDiff: mocks.regionsDiff,
      annotations: {
        list: mocks.annotationsList,
        create: mocks.annotationsCreate,
        update: mocks.annotationsUpdate,
        remove: mocks.annotationsRemove,
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
    metadata: {
      get: mocks.metadataGet,
      correct: mocks.metadataCorrect,
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
    mocks.forceStage2.mockReset();
    mocks.paperlessUrl.mockReset();
    mocks.metadata.mockReset();
    mocks.thumbnailUrl.mockReset();
    mocks.downloadUrl.mockReset();
    mocks.previewUrl.mockReset();
    mocks.candidatesList.mockReset();
    mocks.candidatesList.mockResolvedValue({ candidates: [] });
    mocks.candidateRegions.mockReset();
    mocks.candidatePageImageUrl.mockReset();
    mocks.candidatePageImageUrl.mockReturnValue('/candidate-page-image');
    mocks.regionsDiff.mockReset();
    mocks.regions.mockReset();
    mocks.pageImageUrl.mockReset();
    mocks.annotationsList.mockReset();
    mocks.annotationsCreate.mockReset();
    mocks.annotationsUpdate.mockReset();
    mocks.annotationsRemove.mockReset();
    mocks.metadata.mockResolvedValue({ title: 'Statement.pdf', page_count: 2 });
    mocks.thumbnailUrl.mockReturnValue('/thumbnail/501');
    mocks.downloadUrl.mockReturnValue('/download/501');
    mocks.previewUrl.mockReturnValue('/preview/501');
    mocks.regions.mockResolvedValue({ page: 1, page_count: 1, width: 600, height: 800, words: [] });
    mocks.pageImageUrl.mockReturnValue('/page-image/501/1');
    mocks.annotationsList.mockResolvedValue({ annotations: [] });
    mocks.metadataGet.mockReset();
    mocks.metadataCorrect.mockReset();
    mocks.metadataGet.mockResolvedValue({
      extracted_fields: [
        { field_name: 'document_amount', paperless_field: 'document_amount', value: '129.99', has_value: true },
        { field_name: 'invoice_number', paperless_field: 'invoice_number', value: null, has_value: false },
      ],
    });
    mocks.metadataCorrect.mockResolvedValue({ status: 'ok' });
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
    expect(screen.getByText(/rolled back from the candidate detail panel/i)).toBeInTheDocument();
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
    expect(await screen.findByRole('button', { name: /View Document/i })).toBeInTheDocument();
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

  it('shows a Stage 2 analysis-gap callout when this document has never had Stage 2 profiling', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 502,
      overlay_score: null,
      machine_score: 70.0,
      review_status: 'GOOD',
      reasons: [],
      document_profile: null,
      has_stage2_analysis: false,
    });
    renderPage('502');
    await waitFor(() => expect(screen.getByText(/has not had deep Stage 2 analysis yet/i)).toBeInTheDocument());
  });

  it('shows a narrower note when Stage 2 ran but produced no overlay score', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 503,
      overlay_score: null,
      machine_score: 70.0,
      review_status: 'GOOD',
      reasons: [],
      document_profile: null,
      has_stage2_analysis: true,
    });
    renderPage('503');
    await waitFor(() =>
      expect(screen.getByText(/Stage 2 has run for this document, but no overlay score was produced/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/has not had deep Stage 2 analysis yet/i)).not.toBeInTheDocument();
  });

  it('shows no analysis-gap note once Stage 2 has run and produced an overlay score', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 504,
      overlay_score: 88.0,
      machine_score: 70.0,
      review_status: 'GOOD',
      reasons: [],
      document_profile: null,
      has_stage2_analysis: true,
    });
    renderPage('504');
    await screen.findByText('88.0');
    expect(screen.queryByText(/has not had deep Stage 2 analysis yet/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/no overlay score was produced/i)).not.toBeInTheDocument();
  });

  it('fetches region geometry and saved annotations for the region inspection panel', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      review_status: 'UNCERTAIN',
      reasons: [],
      document_profile: null,
    });

    mocks.regions.mockResolvedValue({
      page: 1,
      page_count: 1,
      width: 600,
      height: 800,
      words: [
        { text: 'Hello', x0: 10, top: 10, x1: 50, bottom: 30, confidence: 0.9, flagged: false, flag_reasons: [], matched_reasons: [] },
      ],
    });
    mocks.annotationsList.mockResolvedValue({
      annotations: [
        { id: 1, document_id: 501, page: 1, x0: 5, top: 5, x1: 20, bottom: 20, label: 'wrong', note: 'looks off' },
      ],
    });
    renderPage('501');

    await waitFor(() => expect(screen.getByText('Region inspection')).toBeInTheDocument());
    await waitFor(() => expect(mocks.regions).toHaveBeenCalledWith('501', 1));
    await waitFor(() => expect(mocks.annotationsList).toHaveBeenCalledWith('501'));
    expect(await screen.findByText('looks off')).toBeInTheDocument();
  });

  it('shows current Paperless field values before the reviewer starts a correction', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      review_status: 'UNCERTAIN',
      reasons: [],
      document_profile: null,
    });

    renderPage('501');

    expect(await screen.findByRole('heading', { name: 'Current Paperless fields' })).toBeInTheDocument();
    expect(screen.getByText('Document Amount')).toBeInTheDocument();
    expect(screen.getByText('129.99')).toBeInTheDocument();
    expect(screen.getByText('Invoice Number')).toBeInTheDocument();
    expect(screen.getByText('Missing')).toBeInTheDocument();
    expect(screen.getByText(/Review these values before marking the document/i)).toBeInTheDocument();
  });

  it('forces Stage 2 analysis and refreshes scores/profile in place on success', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      overlay_score: null,
      machine_score: 70.0,
      review_status: 'UNCERTAIN',
      reasons: [],
      document_profile: null,
      has_stage2_analysis: false,
    });
    renderPage('501');
    await waitFor(() => expect(screen.getByText(/has not had deep Stage 2 analysis yet/i)).toBeInTheDocument());

    let resolveForce: (value: unknown) => void = () => {};
    mocks.forceStage2.mockReturnValue(
      new Promise((resolve) => {
        resolveForce = resolve;
      }),
    );
    const button = screen.getByRole('button', { name: /Force Stage 2 analysis/i });
    fireEvent.click(button);

    expect(mocks.forceStage2).toHaveBeenCalledWith('501');
    await waitFor(() => expect(screen.getByRole('button', { name: /Analyzing/i })).toBeDisabled());

    resolveForce({
      document_id: 501,
      overlay_score: 95.0,
      machine_score: 88.0,
      review_status: 'GOOD',
      reasons: [],
      document_profile: { page_count: 1, dominant_classification: 'digital_text' },
      has_stage2_analysis: true,
    });

    await waitFor(() => expect(screen.getByText('GOOD')).toBeInTheDocument());
    expect(screen.queryByText(/has not had deep Stage 2 analysis yet/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Force Stage 2 analysis/i })).not.toBeDisabled();
  });

  it('shows an inline error when forcing Stage 2 analysis fails', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      review_status: 'UNCERTAIN',
      reasons: [],
      document_profile: null,
    });
    mocks.forceStage2.mockRejectedValue(new Error('A forced Stage-2 analysis for document 501 is already running.'));
    renderPage('501');

    const button = await screen.findByRole('button', { name: /Force Stage 2 analysis/i });
    fireEvent.click(button);

    await waitFor(() =>
      expect(screen.getByText(/already running/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /Force Stage 2 analysis/i })).not.toBeDisabled();
  });

  it('draws a region in "Correct metadata" mode and submits it as a real metadata correction (issue #172)', async () => {
    mocks.paperlessUrl.mockResolvedValue({ paperless_url: null });
    mocks.documentDetail.mockResolvedValue({
      document_id: 501,
      review_status: 'UNCERTAIN',
      reasons: [],
      document_profile: null,
    });
    mocks.regions.mockResolvedValue({
      page: 1,
      page_count: 1,
      width: 600,
      height: 800,
      words: [],
    });
    renderPage('501');

    await waitFor(() => expect(screen.getByText('Region inspection')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Correct metadata/i }));
    const canvas = document.querySelector('.region-overlay-canvas') as HTMLElement;
    const img = document.querySelector('img.region-overlay-image') as HTMLImageElement;
    Object.defineProperty(img, 'clientWidth', { value: 600, configurable: true });
    Object.defineProperty(img, 'clientHeight', { value: 800, configurable: true });
    fireEvent.load(img);
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 600, bottom: 800, width: 600, height: 800, x: 0, y: 0, toJSON: () => {},
    });
    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 15, clientY: 25 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 115, clientY: 85 });
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 115, clientY: 85 });

    // Lazily loads the correctable field list from the live-resolved Paperless schema.
    await waitFor(() => expect(mocks.metadataGet).toHaveBeenCalledWith('501'));
    const form = await screen.findByTestId('metadata-correction-form');
    fireEvent.change(within(form).getByLabelText('Field'), { target: { value: 'document_amount' } });
    expect(within(form).getByLabelText('Corrected value')).toHaveValue('129.99');
    fireEvent.change(within(form).getByLabelText('Corrected value'), { target: { value: '129.99' } });
    fireEvent.click(within(form).getByRole('button', { name: /Submit correction/i }));

    await waitFor(() => expect(mocks.metadataCorrect).toHaveBeenCalled());
    expect(mocks.metadataCorrect).toHaveBeenCalledWith('501', {
      field_name: 'document_amount',
      corrected_value: '129.99',
      source_region: { page: 1, x0: 15, top: 25, x1: 115, bottom: 85 },
      notes: 'Submitted from OCR region viewer',
    });
    expect(await screen.findByText(/document_amount corrected from drawn region/i)).toBeInTheDocument();
    expect(screen.queryByTestId('metadata-correction-form')).not.toBeInTheDocument();
  });
});
