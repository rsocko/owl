import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrFocusInspector from './OcrFocusInspector';

const mocks = vi.hoisted(() => ({
  documentDetail: vi.fn(),
  assessDocument: vi.fn(),
  forceStage2: vi.fn(),
  candidatesList: vi.fn(),
  candidateGet: vi.fn(),
  candidateText: vi.fn(),
  candidateRequest: vi.fn(),
  candidateDecide: vi.fn(),
  candidateCancel: vi.fn(),
  candidateRetry: vi.fn(),
  candidateRegions: vi.fn(),
  rollback: vi.fn(),
  regions: vi.fn(),
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
      assessDocument: mocks.assessDocument,
      forceStage2: mocks.forceStage2,
      rollback: mocks.rollback,
      regions: mocks.regions,
      pageImageUrl: (documentId: number, page: number) => `/documents/${documentId}/pages/${page}`,
      regionsDiff: mocks.regionsDiff,
      candidates: {
        list: mocks.candidatesList,
        get: mocks.candidateGet,
        text: mocks.candidateText,
        request: mocks.candidateRequest,
        decide: mocks.candidateDecide,
        cancel: mocks.candidateCancel,
        retryInvalidation: mocks.candidateRetry,
        regions: mocks.candidateRegions,
        pageImageUrl: (candidateId: string, page: number) => `/candidates/${candidateId}/pages/${page}`,
      },
      annotations: {
        list: mocks.annotationsList,
        create: mocks.annotationsCreate,
        update: mocks.annotationsUpdate,
        remove: mocks.annotationsRemove,
      },
    },
    metadata: {
      get: mocks.metadataGet,
      correct: mocks.metadataCorrect,
    },
  },
}));

const readyCandidate = {
  candidate_id: 'candidate-ready',
  document_id: 501,
  state: 'ready',
  engine: 'ocrmypdf-tesseract-5',
  model_version: '5.4',
  overlay_score: 88,
  machine_score: 91,
  content_score: 93,
  page_count: 1,
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    document_id: 501,
    document_type: 'Statement',
    correspondent: 'Acme Bank',
    document_created: '2026-10-01',
    overlay_score: 76,
    machine_score: 80,
    review_status: 'REVIEW_RECOMMENDED',
    quality_scorer_version: '2.0',
    assessed_at: '2026-10-17T12:00:00Z',
    reasons: [],
    document_profile: { page_count: 1, pages: [] },
    has_stage2_analysis: true,
    ...overrides,
  };
}

function candidateDetail(overrides: Record<string, unknown> = {}) {
  return {
    ...readyCandidate,
    comparison: {
      comparison_id: 'comparison-1',
      blocking_findings: [],
      text_diff_summary: {},
      overlay_score_delta: 12,
      machine_score_delta: 11,
      content_score_delta: 8,
    },
    ...overrides,
  };
}

describe('OcrFocusInspector', () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.documentDetail.mockResolvedValue(detail());
    mocks.assessDocument.mockResolvedValue(detail());
    mocks.candidatesList.mockResolvedValue({ candidates: [] });
    mocks.regions.mockResolvedValue({ page: 1, page_count: 1, width: 600, height: 800, words: [] });
    mocks.candidateRegions.mockResolvedValue({ page: 1, page_count: 1, width: 600, height: 800, words: [] });
    mocks.regionsDiff.mockResolvedValue({ removed_from_b: [], added_in_b: [], shifted: [] });
    mocks.annotationsList.mockResolvedValue({ annotations: [] });
    mocks.metadataGet.mockResolvedValue({ extracted_fields: [] });
  });

  it('opens current-only focus mode and returns without changing the review outcome', async () => {
    const onReturn = vi.fn();
    render(<OcrFocusInspector documentId={501} contextLabel="Inbox review" onReturn={onReturn} />);

    expect(await screen.findByRole('heading', { name: 'OCR focus inspector' })).toBeInTheDocument();
    expect(screen.getByText(/Inbox review · Paperless #501/)).toBeInTheDocument();
    expect(await screen.findByText(/No candidates yet/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Return to review' })[0]);
    expect(onReturn).toHaveBeenCalledOnce();
  });

  it('creates the text assessment when the compatibility route opens an unscanned document', async () => {
    const notFound = Object.assign(new Error('Not found'), { status: 404 });
    mocks.documentDetail.mockRejectedValueOnce(notFound);
    mocks.assessDocument.mockResolvedValueOnce(detail({ has_stage2_analysis: false }));

    render(<OcrFocusInspector documentId={501} />);

    expect(await screen.findByRole('heading', { name: 'OCR focus inspector' })).toBeInTheDocument();
    expect(mocks.assessDocument).toHaveBeenCalledWith(501);
  });

  it('shows synchronized candidate comparison and text diff for a selected ready candidate', async () => {
    mocks.candidatesList.mockResolvedValue({ candidates: [readyCandidate] });
    mocks.candidateGet.mockResolvedValue(candidateDetail());
    mocks.candidateText.mockResolvedValue({ current_text: 'old text', candidate_text: 'new text' });
    render(<OcrFocusInspector documentId={501} />);

    fireEvent.click(await screen.findByRole('button', { name: 'View' }));
    expect(await screen.findByText(/Current document/)).toBeInTheDocument();
    expect(screen.getAllByText(/OCRmyPDF \/ Tesseract 5/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'Text diff' }));
    expect(await screen.findByText('old text')).toBeInTheDocument();
    expect(screen.getByText('new text')).toBeInTheDocument();
  });

  it('blocks acceptance when comparison findings are blocking', async () => {
    mocks.candidatesList.mockResolvedValue({ candidates: [readyCandidate] });
    mocks.candidateGet.mockResolvedValue(candidateDetail({
      comparison: {
        comparison_id: 'comparison-1',
        blocking_findings: ['candidate_page_count_mismatch'],
        text_diff_summary: {},
        overlay_score_delta: 12,
        machine_score_delta: 11,
        content_score_delta: 8,
      },
    }));
    mocks.candidateText.mockResolvedValue({ current_text: '', candidate_text: '' });
    window.localStorage.setItem('owl_ocr_actor_name', 'reviewer');
    render(<OcrFocusInspector documentId={501} />);

    fireEvent.click(await screen.findByRole('button', { name: 'View' }));
    expect(await screen.findByText('Acceptance blocked')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept' })).toBeDisabled();
    expect(mocks.candidateDecide).not.toHaveBeenCalled();
  });

  it('retries pending downstream invalidation without silently re-running analysis', async () => {
    const pending = { ...readyCandidate, state: 'accepted_pending_invalidation' };
    mocks.documentDetail.mockResolvedValue(detail({
      has_accepted_ocr_candidate: true,
      accepted_candidate_pending_invalidation: true,
    }));
    mocks.candidatesList.mockResolvedValue({ candidates: [pending] });
    mocks.candidateGet.mockResolvedValue(candidateDetail({ state: 'accepted_pending_invalidation' }));
    mocks.candidateText.mockResolvedValue({ current_text: '', candidate_text: '' });
    mocks.candidateRetry.mockResolvedValue({});
    window.localStorage.setItem('owl_ocr_actor_name', 'reviewer');
    render(<OcrFocusInspector documentId={501} />);

    fireEvent.click(await screen.findByRole('button', { name: 'View' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Retry invalidation' }));

    await waitFor(() => expect(mocks.candidateRetry).toHaveBeenCalledWith('candidate-ready', { actor: 'reviewer' }));
    expect(mocks.forceStage2).not.toHaveBeenCalled();
  });
});
