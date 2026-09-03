import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrCandidatesPanel from './OcrCandidatesPanel';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  text: vi.fn(),
  request: vi.fn(),
  decide: vi.fn(),
  cancel: vi.fn(),
  rollback: vi.fn(),
  retryInvalidation: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      rollback: mocks.rollback,
      candidates: {
        list: mocks.list,
        get: mocks.get,
        text: mocks.text,
        request: mocks.request,
        decide: mocks.decide,
        cancel: mocks.cancel,
        retryInvalidation: mocks.retryInvalidation,
      },
    },
  },
}));

const readyCandidate = {
  candidate_id: 'cand-123456',
  document_id: 501,
  state: 'ready',
  engine: 'ocrmypdf-tesseract-5',
  model_version: 'ocrmypdf-16',
  overlay_score: 92.0,
  machine_score: 88.0,
  page_count: 2,
  requested_at: '2024-05-01T00:00:00Z',
  completed_at: '2024-05-01T00:01:00Z',
  decision: null,
  expires_at: '2024-06-01T00:00:00Z',
};

const readyCandidateDetail = {
  ...readyCandidate,
  comparison: {
    comparison_id: 'cmp-1',
    blocking_findings: [],
    text_diff_summary: { similarity: 0.98 },
    overlay_score_delta: 2.5,
    machine_score_delta: 1.0,
    performed_at: '2024-05-01T00:01:00Z',
  },
  failure_reason: null,
  decision_reason: null,
  decided_at: null,
  actor: null,
};

describe('OcrCandidatesPanel', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockReset());
    mocks.list.mockResolvedValue({ candidates: [] });
    window.localStorage.clear();
  });

  function setActorName(name: string) {
    const input = screen.getByPlaceholderText(/e\.g\. jsmith/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: name } });
  }

  it('shows an empty state and a generation control when there are no candidates', async () => {
    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText(/No candidates yet/i)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Generate candidates/i })).toBeInTheDocument();
    expect(mocks.list).toHaveBeenCalledWith({ document_id: 501 });
  });

  it('requests generation for the selected engines and never touches Paperless directly', async () => {
    mocks.request.mockResolvedValue({ candidate_ids: ['cand-123456'], count: 1 });
    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: /Generate candidates/i }));

    await waitFor(() =>
      expect(mocks.request).toHaveBeenCalledWith({
        document_ids: [501],
        engines: ['ocrmypdf-tesseract-5'],
      }),
    );
    // Generation is requested through the candidate API only; this component
    // never imports or calls a Paperless client directly.
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
  });

  it('lists candidates and shows their comparison + side-by-side text on selection', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    mocks.get.mockResolvedValue(readyCandidateDetail);
    mocks.text.mockResolvedValue({ current_text: 'old text', candidate_text: 'new text' });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText('OCRmyPDF / Tesseract 5')).toBeInTheDocument());
    expect(screen.getByText('ready')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() => expect(screen.getByText(/old text/)).toBeInTheDocument());
    expect(screen.getByText(/new text/)).toBeInTheDocument();
    expect(screen.getByText(/No blocking findings/i)).toBeInTheDocument();
    expect(screen.getByText(/informational only/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  it('requires an actor name before recording an accept decision, then sends it', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    mocks.get.mockResolvedValue(readyCandidateDetail);
    mocks.text.mockResolvedValue({ current_text: 'old', candidate_text: 'new' });
    mocks.decide.mockResolvedValue({ ...readyCandidateDetail, state: 'accepted', decision: 'accepted' });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));
    await waitFor(() => screen.getByRole('button', { name: 'Accept' }));

    // Without a name entered, accepting must not call the API at all.
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(screen.getByText(/enter your name/i)).toBeInTheDocument());
    expect(mocks.decide).not.toHaveBeenCalled();

    setActorName('jsmith');
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));

    await waitFor(() =>
      expect(mocks.decide).toHaveBeenCalledWith('cand-123456', { decision: 'accepted', actor: 'jsmith' }),
    );
  });

  it('remembers the actor name across remounts via localStorage', async () => {
    mocks.list.mockResolvedValue({ candidates: [] });
    const { unmount } = render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByPlaceholderText(/e\.g\. jsmith/i));
    setActorName('adoe');
    unmount();

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() =>
      expect((screen.getByPlaceholderText(/e\.g\. jsmith/i) as HTMLInputElement).value).toBe('adoe'),
    );
  });

  it('shows blocking findings prominently and never treats a higher score as authorization', async () => {
    const flagged = {
      ...readyCandidateDetail,
      comparison: {
        ...readyCandidateDetail.comparison,
        blocking_findings: ['pages_missing', 'machine_regression'],
      },
    };
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    mocks.get.mockResolvedValue(flagged);
    mocks.text.mockResolvedValue({ current_text: 'old', candidate_text: 'new' });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() => expect(screen.getByText('pages_missing')).toBeInTheDocument());
    expect(screen.getByText('machine_regression')).toBeInTheDocument();
    // Accept/reject are still both offered — findings inform, they don't decide.
    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument();
  });

  it('shows a Cancel button for a RUNNING candidate and cancels it', async () => {
    const running = { ...readyCandidate, state: 'running', overlay_score: null, machine_score: null };
    mocks.list.mockResolvedValue({ candidates: [running] });
    mocks.cancel.mockResolvedValue({ candidate_id: 'cand-123456', state: 'rejected' });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText('running')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(mocks.cancel).toHaveBeenCalledWith('cand-123456'));
    // Cancellation only calls the candidate-cancel endpoint; it has no
    // Paperless-facing side effect.
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
  });

  it('does not show a Cancel button once a candidate is READY', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText('ready')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it('surfaces a failure reason for a FAILED candidate', async () => {
    const failed = { ...readyCandidate, state: 'failed', overlay_score: null, machine_score: null };
    const failedDetail = { ...readyCandidateDetail, ...failed, comparison: null, failure_reason: 'ocrmypdf binary not found' };
    mocks.list.mockResolvedValue({ candidates: [failed] });
    mocks.get.mockResolvedValue(failedDetail);
    mocks.text.mockResolvedValue({ current_text: null, candidate_text: null });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByText('failed'));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() => expect(screen.getByText(/ocrmypdf binary not found/i)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument();
  });

  it('shows a "Content accuracy looks improved" suggested read when content score improves and there are no blocking findings', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    const improved = {
      ...readyCandidateDetail,
      comparison: { ...readyCandidateDetail.comparison, content_score_delta: 5.0, overlay_score_delta: 0 },
    };
    mocks.get.mockResolvedValue(improved);
    mocks.text.mockResolvedValue({ current_text: 'old text', candidate_text: 'new text' });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText('OCRmyPDF / Tesseract 5')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() => expect(screen.getByText(/Content accuracy looks improved/i)).toBeInTheDocument());
    // Still non-authoritative — never a one-click "accept recommended" action.
    expect(screen.queryByRole('button', { name: /accept recommended/i })).not.toBeInTheDocument();
  });

  it('shows a "Box/highlight placement may be less precise" suggested read when overlay score drops meaningfully', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    const declined = {
      ...readyCandidateDetail,
      comparison: { ...readyCandidateDetail.comparison, overlay_score_delta: -6.0, content_score_delta: 0 },
    };
    mocks.get.mockResolvedValue(declined);
    mocks.text.mockResolvedValue({ current_text: 'old text', candidate_text: 'new text' });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() => expect(screen.getByText(/Box\/highlight placement may be less precise/i)).toBeInTheDocument());
  });

  it('shows a distinct "Needs careful review" suggested read when there are blocking findings', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    const flagged = {
      ...readyCandidateDetail,
      comparison: { ...readyCandidateDetail.comparison, blocking_findings: ['pages_missing'] },
    };
    mocks.get.mockResolvedValue(flagged);
    mocks.text.mockResolvedValue({ current_text: 'old text', candidate_text: 'new text' });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() => expect(screen.getByText(/Needs careful review/i)).toBeInTheDocument());
  });

  it('shows an analysis-gap note in the comparison view when the document has no Stage 2 baseline', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    mocks.get.mockResolvedValue(readyCandidateDetail);
    mocks.text.mockResolvedValue({ current_text: 'old text', candidate_text: 'new text' });

    render(<OcrCandidatesPanel documentId={501} hasStage2Analysis={false} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() =>
      expect(screen.getByText(/hasn't had Stage 2 analysis, so the overlay comparison below may be incomplete/i)).toBeInTheDocument(),
    );
  });

  it('shows relative badges distinguishing two ready candidates from each other, not just vs current', async () => {
    // Both candidates improve over current (matches the reported UX gap: two
    // identical "improved" badges give no way to tell them apart) — one
    // engine has the higher overlay score, the other has the higher machine
    // score, so each should get its own distinct relative badge.
    const azureCandidate = {
      ...readyCandidate,
      candidate_id: 'cand-azure01',
      engine: 'azure-prebuilt-layout',
      overlay_score: 80.45,
      machine_score: 70.34,
      content_score: 72.0,
    };
    const ocrmypdfCandidate = {
      ...readyCandidate,
      candidate_id: 'cand-ocrmy01',
      engine: 'ocrmypdf-tesseract-5',
      overlay_score: 99.48,
      machine_score: 68.28,
      content_score: 69.0,
    };
    mocks.list.mockResolvedValue({ candidates: [azureCandidate, ocrmypdfCandidate] });

    render(<OcrCandidatesPanel documentId={501} currentOverlayScore={69.8} currentContentScore={64.0} />);
    await waitFor(() => expect(screen.getByText('Azure Document Intelligence (prebuilt-layout)')).toBeInTheDocument());

    expect(screen.getByText('Highest overlay score of ready candidates')).toBeInTheDocument();
    expect(screen.getByText('Highest content accuracy of ready candidates')).toBeInTheDocument();
  });

  it('does not show relative badges when only one candidate is ready', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    render(<OcrCandidatesPanel documentId={501} currentOverlayScore={69.8} />);
    await waitFor(() => expect(screen.getByText('OCRmyPDF / Tesseract 5')).toBeInTheDocument());
    expect(screen.queryByText(/Highest overlay score of ready candidates/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Highest content accuracy of ready candidates/i)).not.toBeInTheDocument();
  });

  it('pins a "Current (Paperless)" reference row showing the live scores', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    render(
      <OcrCandidatesPanel
        documentId={501}
        currentOverlayScore={69.8}
        currentMachineScore={72.5}
      />,
    );
    await waitFor(() => expect(screen.getByText('OCRmyPDF / Tesseract 5')).toBeInTheDocument());

    expect(screen.getByText('Current (Paperless)')).toBeInTheDocument();
    expect(screen.getByText('69.8')).toBeInTheDocument();
    expect(screen.getByText('72.5')).toBeInTheDocument();
  });

  it('omits the current-value reference row when no live scores are available', async () => {
    mocks.list.mockResolvedValue({ candidates: [readyCandidate] });
    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText('OCRmyPDF / Tesseract 5')).toBeInTheDocument());
    expect(screen.queryByText('Current (Paperless)')).not.toBeInTheDocument();
  });

  it('relabels the reference row as a prior baseline once a candidate is accepted', async () => {
    const accepted = { ...readyCandidate, state: 'accepted', decision: 'accepted' };
    mocks.list.mockResolvedValue({ candidates: [accepted] });
    render(
      <OcrCandidatesPanel
        documentId={501}
        currentOverlayScore={69.8}
        currentMachineScore={72.5}
      />,
    );
    await waitFor(() => expect(screen.getByText('OCRmyPDF / Tesseract 5')).toBeInTheDocument());

    expect(screen.queryByText('Current (Paperless)')).not.toBeInTheDocument();
    expect(screen.getByText('Original (pre-acceptance baseline)')).toBeInTheDocument();
    expect(screen.getByText(/not refreshed after acceptance/i)).toBeInTheDocument();
  });

  it('shows non-stale apply/rollback messaging', async () => {
    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText(/applies it to the live Paperless document/i)).toBeInTheDocument());
    expect(screen.queryByText(/never modifies Paperless/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/not yet built/i)).not.toBeInTheDocument();
  });

  it('shows a Roll back button for an ACCEPTED candidate, confirms, and requires+sends an actor', async () => {
    const accepted = { ...readyCandidate, state: 'accepted', decision: 'accepted' };
    const acceptedDetail = { ...readyCandidateDetail, state: 'accepted', decision: 'accepted', actor: 'jsmith' };
    mocks.list.mockResolvedValue({ candidates: [accepted] });
    mocks.get.mockResolvedValue(acceptedDetail);
    mocks.text.mockResolvedValue({ current_text: 'old', candidate_text: 'new' });
    mocks.rollback.mockResolvedValue({ document_id: 501, current_version_id: 9, invalidation_recorded: true, status: 'rolled_back' });

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));
    await waitFor(() => screen.getByRole('button', { name: 'Roll back' }));

    // No actor yet — must not call rollback.
    fireEvent.click(screen.getByRole('button', { name: 'Roll back' }));
    await waitFor(() => expect(screen.getByText(/enter your name/i)).toBeInTheDocument());
    expect(mocks.rollback).not.toHaveBeenCalled();

    setActorName('jsmith');
    fireEvent.click(screen.getByRole('button', { name: 'Roll back' }));

    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() =>
      expect(mocks.rollback).toHaveBeenCalledWith(501, { target_candidate_id: 'cand-123456', actor: 'jsmith' }),
    );
    confirmSpy.mockRestore();
  });

  it('does not roll back when the confirmation dialog is dismissed', async () => {
    const accepted = { ...readyCandidate, state: 'accepted', decision: 'accepted' };
    const acceptedDetail = { ...readyCandidateDetail, state: 'accepted', decision: 'accepted', actor: 'jsmith' };
    mocks.list.mockResolvedValue({ candidates: [accepted] });
    mocks.get.mockResolvedValue(acceptedDetail);
    mocks.text.mockResolvedValue({ current_text: 'old', candidate_text: 'new' });

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));
    await waitFor(() => screen.getByRole('button', { name: 'Roll back' }));
    setActorName('jsmith');

    fireEvent.click(screen.getByRole('button', { name: 'Roll back' }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(mocks.rollback).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('shows a distinct pending-invalidation badge and a Retry invalidation action for ACCEPTED_PENDING_INVALIDATION', async () => {
    const pending = { ...readyCandidate, state: 'accepted_pending_invalidation', decision: 'accepted' };
    const pendingDetail = {
      ...readyCandidateDetail,
      state: 'accepted_pending_invalidation',
      decision: 'accepted',
      actor: 'jsmith',
    };
    mocks.list.mockResolvedValue({ candidates: [pending] });
    mocks.get.mockResolvedValue(pendingDetail);
    mocks.text.mockResolvedValue({ current_text: 'old', candidate_text: 'new' });
    mocks.retryInvalidation.mockResolvedValue({ candidate_id: 'cand-123456', state: 'accepted', invalidation_recorded: true });

    render(<OcrCandidatesPanel documentId={501} />);
    await waitFor(() => screen.getByRole('button', { name: /View/i }));
    fireEvent.click(screen.getByRole('button', { name: /View/i }));

    await waitFor(() => expect(screen.getByText(/downstream refresh pending/i)).toBeInTheDocument());
    expect(screen.getByText(/downstream search\/analysis/i)).toBeInTheDocument();

    setActorName('jsmith');
    fireEvent.click(screen.getByRole('button', { name: /Retry invalidation/i }));

    await waitFor(() => expect(mocks.retryInvalidation).toHaveBeenCalledWith('cand-123456', { actor: 'jsmith' }));
  });
});
