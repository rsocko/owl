import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { type OcrSummaryState } from '../hooks/useOcrSummary';
import OcrSummaryPanel from './OcrSummaryPanel';

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      documentDetail: vi.fn(),
      candidates: { list: vi.fn() },
    },
    intake: {
      documentOcr: vi.fn(),
    },
  },
}));

function summary(overrides: Partial<OcrSummaryState> = {}): OcrSummaryState {
  return {
    assessment: {
      document_id: 42,
      overlay_score: 91,
      machine_score: 84,
      review_status: 'GOOD',
      quality_scorer_version: '2.1.0',
      assessed_at: '2026-10-17T12:00:00Z',
      reasons: [],
      document_profile: {
        page_count: 2,
        dominant_classification: 'scanned_with_overlay',
        content_shape: 'prose',
        language_hint: 'en',
      },
      has_accepted_ocr_candidate: false,
      accepted_candidate_pending_invalidation: false,
    },
    candidates: [],
    text: 'Searchable OCR text',
    loading: false,
    assessmentError: null,
    candidateError: null,
    textError: null,
    reload: vi.fn(),
    ...overrides,
  };
}

describe('OcrSummaryPanel', () => {
  it('shows the authoritative multidimensional assessment without a legacy score', () => {
    render(<OcrSummaryPanel documentId={42} state={summary()} />);

    expect(screen.getByRole('heading', { name: 'OCR assessment' })).toBeInTheDocument();
    expect(screen.getByText('Good')).toBeInTheDocument();
    expect(screen.getByText('91')).toBeInTheDocument();
    expect(screen.getByText('Overlay / readability')).toBeInTheDocument();
    expect(screen.getByText('84')).toBeInTheDocument();
    expect(screen.getByText('Machine extraction')).toBeInTheDocument();
    expect(screen.getByText(/2 pages · scanned_with_overlay · prose · en/)).toBeInTheDocument();
    expect(screen.getByText('Current OCR only')).toBeInTheDocument();
    expect(screen.queryByText('OCR quality')).not.toBeInTheDocument();
  });

  it('marks accepted OCR with pending invalidation as stale and unresolved downstream', () => {
    render(
      <OcrSummaryPanel
        documentId={42}
        state={summary({
          assessment: {
            ...summary().assessment!,
            review_status: 'REVIEW_RECOMMENDED',
            assessed_at: '2026-10-16T12:00:00Z',
            has_accepted_ocr_candidate: true,
            accepted_candidate_at: '2026-10-17T12:00:00Z',
            accepted_candidate_pending_invalidation: true,
            reasons: [{
              code: 'overlay.low_confidence',
              message: 'Text placement is inconsistent on several pages.',
              severity: 'warning',
              component: 'overlay',
            }],
          },
          candidates: [{
            candidate_id: 'candidate-1',
            state: 'accepted_pending_invalidation',
            engine: 'ocrmypdf-tesseract-5',
          }],
        })}
      />,
    );

    expect(screen.getByText('Accepted OCR is still propagating')).toBeInTheDocument();
    expect(screen.getByText('Accepted · updates pending')).toBeInTheDocument();
    expect(screen.getByText('Text placement is inconsistent on several pages.')).toBeInTheDocument();
  });

  it('shows a no-assessment state while preserving searchable text', () => {
    render(
      <OcrSummaryPanel
        documentId={42}
        state={summary({ assessment: null, text: 'Text exists before assessment.' })}
      />,
    );

    expect(screen.getByText('No OCR assessment yet')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Text exists before assessment.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Inspect OCR/ })).toHaveAttribute(
      'href',
      '#/ocr-quality/documents/42',
    );
  });

  it('surfaces assessment failures with a retry action', () => {
    const reload = vi.fn();
    render(
      <OcrSummaryPanel
        documentId={42}
        state={summary({ assessment: null, assessmentError: 'Service unavailable', reload })}
      />,
    );

    expect(screen.getByText(/OCR assessment unavailable: Service unavailable/)).toBeInTheDocument();
    screen.getByRole('button', { name: /Retry/i }).click();
    expect(reload).toHaveBeenCalledOnce();
  });
});
