import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrQualityDashboard from './OcrQualityDashboard';

const mocks = vi.hoisted(() => ({
  distribution: vi.fn(),
  calibrationSummary: vi.fn(),
  runs: vi.fn(),
  startRun: vi.fn(),
  resumeRun: vi.fn(),
  sampleRun: vi.fn(),
}));

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    endpoints: {
      ocrQuality: {
        distribution: mocks.distribution,
        calibrationSummary: mocks.calibrationSummary,
        runs: mocks.runs,
        startRun: mocks.startRun,
        resumeRun: mocks.resumeRun,
        sampleRun: mocks.sampleRun,
      },
    },
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <OcrQualityDashboard />
    </MemoryRouter>,
  );
}

describe('OcrQualityDashboard', () => {
  beforeEach(() => {
    mocks.distribution.mockReset();
    mocks.calibrationSummary.mockReset().mockResolvedValue({
      decided_count: 0,
      agreement_count: 0,
      agreement_rate: null,
      false_positive_count: 0,
      false_positive_rate: null,
      false_negative_count: 0,
      false_negative_rate: null,
      uncertain_count: 0,
      uncertain_rate: null,
      uncertain_accepted_count: 0,
      uncertain_rejected_count: 0,
    });
    mocks.runs.mockReset().mockResolvedValue({ runs: [] });
    mocks.startRun.mockReset();
    mocks.resumeRun.mockReset();
    mocks.sampleRun.mockReset();
  });

  it('shows a loading state before data resolves', () => {
    mocks.distribution.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.querySelector('.skeleton, .skeleton-loader, [class*="skeleton"]')).toBeTruthy();
  });

  it('shows an empty state when the corpus has not been scanned', async () => {
    mocks.distribution.mockResolvedValue({
      total_documents: 0,
      review_status_distribution: {},
      overlay_score_decile_distribution: {},
      machine_score_decile_distribution: {},
      scorer_version_distribution: {},
      oldest_assessed_at: null,
      newest_assessed_at: null,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/No OCR quality assessments yet/i)).toBeInTheDocument());
  });

  it('renders an error state and retries', async () => {
    mocks.distribution.mockRejectedValueOnce(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });

  it('renders review-status stats and score histograms when data is present', async () => {
    mocks.distribution.mockResolvedValue({
      total_documents: 42,
      review_status_distribution: { GOOD: 30, UNCERTAIN: 8, REVIEW_RECOMMENDED: 3, FAILED: 1 },
      overlay_score_decile_distribution: { '90-99': 20, '80-89': 10, unavailable: 12 },
      machine_score_decile_distribution: { '90-99': 25, '70-79': 17 },
      scorer_version_distribution: { '1.0.0': 42 },
      oldest_assessed_at: '2024-01-01T00:00:00Z',
      newest_assessed_at: '2024-06-01T00:00:00Z',
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Assessed documents')).toBeInTheDocument());
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getAllByText('GOOD').length).toBeGreaterThan(0);
    expect(screen.getByText('Overlay/readability score distribution')).toBeInTheDocument();
    expect(screen.getByText('Machine-extraction score distribution')).toBeInTheDocument();
  });
});

describe('OcrQualityDashboard — Calibration summary (issue #167)', () => {
  beforeEach(() => {
    mocks.distribution.mockReset().mockResolvedValue({
      total_documents: 0,
      review_status_distribution: {},
      overlay_score_decile_distribution: {},
      machine_score_decile_distribution: {},
      scorer_version_distribution: {},
      oldest_assessed_at: null,
      newest_assessed_at: null,
    });
    mocks.calibrationSummary.mockReset();
    mocks.runs.mockReset().mockResolvedValue({ runs: [] });
    mocks.startRun.mockReset();
    mocks.resumeRun.mockReset();
    mocks.sampleRun.mockReset();
  });

  it('shows a "not enough data" fallback when decided_count is small', async () => {
    mocks.calibrationSummary.mockResolvedValue({
      decided_count: 3,
      agreement_count: 2,
      agreement_rate: 0.667,
      false_positive_count: 1,
      false_positive_rate: 0.333,
      false_negative_count: 0,
      false_negative_rate: 0,
      uncertain_count: 0,
      uncertain_rate: 0,
      uncertain_accepted_count: 0,
      uncertain_rejected_count: 0,
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/Not enough decided candidates yet to draw conclusions/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText('Agreement rate:')).not.toBeInTheDocument();
  });

  it('renders counts and rates once enough candidates are decided', async () => {
    mocks.calibrationSummary.mockResolvedValue({
      decided_count: 20,
      agreement_count: 15,
      agreement_rate: 0.75,
      false_positive_count: 2,
      false_positive_rate: 0.1,
      false_negative_count: 1,
      false_negative_rate: 0.05,
      uncertain_count: 2,
      uncertain_rate: 0.1,
      uncertain_accepted_count: 1,
      uncertain_rejected_count: 1,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Calibration (issue #17 gate)')).toBeInTheDocument());
    expect(screen.getByText(/Agreement rate:/i)).toBeInTheDocument();
    expect(screen.getByText(/75%/)).toBeInTheDocument();
    expect(screen.getByText(/15 of 20/)).toBeInTheDocument();
  });
});

describe('OcrQualityDashboard — Runs panel (manual trigger, issue #30)', () => {
  beforeEach(() => {
    mocks.distribution.mockReset().mockResolvedValue({
      total_documents: 0,
      review_status_distribution: {},
      overlay_score_decile_distribution: {},
      machine_score_decile_distribution: {},
      scorer_version_distribution: {},
      oldest_assessed_at: null,
      newest_assessed_at: null,
    });
    mocks.calibrationSummary.mockReset().mockResolvedValue({
      decided_count: 0,
      agreement_count: 0,
      agreement_rate: null,
      false_positive_count: 0,
      false_positive_rate: null,
      false_negative_count: 0,
      false_negative_rate: null,
      uncertain_count: 0,
      uncertain_rate: null,
      uncertain_accepted_count: 0,
      uncertain_rejected_count: 0,
    });
    mocks.runs.mockReset().mockResolvedValue({ runs: [] });
    mocks.startRun.mockReset();
    mocks.resumeRun.mockReset();
    mocks.sampleRun.mockReset();
  });

  it('shows an empty runs state before any scan has been started', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('No runs yet')).toBeInTheDocument());
  });

  it('starts a new corpus scan and shows the returned run_id', async () => {
    mocks.startRun.mockResolvedValue({ run_id: 'run-123', stage: 'stage_1_corpus_scan', status: 'running' });
    renderPage();
    await waitFor(() => expect(screen.getByText('No runs yet')).toBeInTheDocument());

    screen.getByRole('button', { name: /Run new inventory scan/i }).click();

    await waitFor(() => expect(mocks.startRun).toHaveBeenCalledWith({}));
    await waitFor(() => expect(screen.getByText(/Corpus scan started \(run_id: run-123\)/i)).toBeInTheDocument());
  });

  it('shows the conflicting run_id when a duplicate scan is already running (409)', async () => {
    const { ApiError } = await import('../lib/api');
    mocks.startRun.mockRejectedValue(
      new ApiError('Conflict', 409, {
        error: { code: 'run_already_in_progress', message: 'already running', run_id: 'already-running-1' },
      }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText('No runs yet')).toBeInTheDocument());

    screen.getByRole('button', { name: /Run new inventory scan/i }).click();

    await waitFor(() =>
      expect(screen.getByText(/already in progress \(run_id: already-running-1\)/i)).toBeInTheDocument(),
    );
  });

  it('shows a generic error toast when starting a scan fails for another reason', async () => {
    mocks.startRun.mockRejectedValue(new Error('network down'));
    renderPage();
    await waitFor(() => expect(screen.getByText('No runs yet')).toBeInTheDocument());

    screen.getByRole('button', { name: /Run new inventory scan/i }).click();

    await waitFor(() => expect(screen.getByText('network down')).toBeInTheDocument());
  });

  it('offers a resume action for a failed Stage-1 run and resumes it', async () => {
    mocks.runs.mockResolvedValue({
      runs: [
        {
          run_id: 'run-failed-1',
          stage: 'stage_1_corpus_scan',
          status: 'failed',
          counts: {},
          started_at: '2024-01-01T00:00:00Z',
          finished_at: '2024-01-01T00:05:00Z',
        },
      ],
    });
    mocks.resumeRun.mockResolvedValue({ run_id: 'run-failed-1', stage: 'stage_1_corpus_scan', status: 'running' });
    renderPage();
    await waitFor(() => expect(screen.getByText('run-failed-1')).toBeInTheDocument());

    screen.getByRole('button', { name: /Resume/i }).click();

    await waitFor(() => expect(mocks.resumeRun).toHaveBeenCalledWith('run-failed-1', {}));
    await waitFor(() => expect(screen.getByText(/Corpus scan resumed \(run_id: run-failed-1\)/i)).toBeInTheDocument());
  });

  it('offers a Stage 2 sample action for a completed Stage-1 run and starts a sample', async () => {
    mocks.runs.mockResolvedValue({
      runs: [
        {
          run_id: 'run-done-1',
          stage: 'stage_1_corpus_scan',
          status: 'completed',
          counts: { assessed: 100 },
          started_at: '2024-01-01T00:00:00Z',
          finished_at: '2024-01-01T00:10:00Z',
        },
      ],
    });
    mocks.sampleRun.mockResolvedValue({ run_id: 'sample-1', stage: 'stage_2_stratified_sample', status: 'running' });
    renderPage();
    await waitFor(() => expect(screen.getByText('run-done-1')).toBeInTheDocument());

    screen.getByRole('button', { name: /Run Stage 2 sample/i }).click();
    const sampleSizeInput = await screen.findByLabelText('Sample size');
    fireEvent.change(sampleSizeInput, { target: { value: '50' } });

    screen.getByRole('button', { name: /Start sample/i }).click();

    await waitFor(() => expect(mocks.sampleRun).toHaveBeenCalledWith('run-done-1', { sample_size: 50 }));
    await waitFor(() => expect(screen.getByText(/Stratified sample started \(run_id: sample-1\)/i)).toBeInTheDocument());
  });
});
