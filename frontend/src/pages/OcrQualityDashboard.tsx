import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, SkeletonLoader, StatCard, StatGrid, Toast, type Tone } from '../components/ui';
import { ApiError, endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/ocr-quality.css';

/* ── Types (exported for testing) ── */

export type ReviewStatus = 'GOOD' | 'UNCERTAIN' | 'REVIEW_RECOMMENDED' | 'FAILED' | 'unscored';

export type CorpusDistribution = {
  total_documents: number;
  review_status_distribution: Record<string, number>;
  overlay_score_decile_distribution: Record<string, number>;
  machine_score_decile_distribution: Record<string, number>;
  scorer_version_distribution: Record<string, number>;
  oldest_assessed_at?: string | null;
  newest_assessed_at?: string | null;
  redacted?: boolean;
};

export type InventoryRun = {
  run_id: string;
  stage: string;
  status: string;
  counts: Record<string, number>;
  throughput_docs_per_second?: number | null;
  seed?: string | null;
  source_run_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

/* ── Helpers (exported for testing) ── */

export function statusTone(status: string): Tone {
  switch (status) {
    case 'GOOD': return 'ok';
    case 'UNCERTAIN': return 'info';
    case 'REVIEW_RECOMMENDED': return 'warn';
    case 'FAILED': return 'err';
    default: return 'muted';
  }
}

export function formatDate(value?: string | null) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

export function runStatusTone(status: string): Tone {
  switch (status) {
    case 'completed': return 'ok';
    case 'running': return 'info';
    case 'failed': return 'err';
    default: return 'muted';
  }
}

const STAGE_LABELS: Record<string, string> = {
  stage_1_corpus_scan: 'Stage 1: Corpus scan',
  stage_2_stratified_sample: 'Stage 2: Stratified sample',
};

function stageLabel(stage: string) {
  return STAGE_LABELS[stage] ?? stage;
}

const DECILE_ORDER = ['0-9', '10-19', '20-29', '30-39', '40-49', '50-59', '60-69', '70-79', '80-89', '90-99', 'unavailable'];

function DecileHistogram({ title, distribution }: { title: string; distribution: Record<string, number> }) {
  const entries = DECILE_ORDER.filter((key) => distribution[key] != null).map((key) => [key, distribution[key]] as const);
  const max = Math.max(...entries.map(([, count]) => count), 1);
  if (entries.length === 0) {
    return (
      <Card title={title}>
        <div className="text-muted">No scored documents yet.</div>
      </Card>
    );
  }
  return (
    <Card title={title}>
      <div className="ocr-histogram">
        {entries.map(([bucket, count]) => (
          <div key={bucket} className="ocr-histogram-row">
            <span className="ocr-histogram-label">{bucket === 'unavailable' ? 'N/A' : bucket}</span>
            <div className="ocr-histogram-bar-track">
              <div className="ocr-histogram-bar" style={{ width: `${Math.max((count / max) * 100, 2)}%` }} />
            </div>
            <span className="ocr-histogram-count">{count}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

type ToastState = { message: string; tone?: 'success' | 'error' } | null;

type ScanFormState = {
  tags: string;
  correspondent: string;
  batchSize: string;
};

type SampleFormState = {
  sampleSize: string;
  seed: string;
  minPerStratum: string;
};

const DEFAULT_SCAN_FORM: ScanFormState = { tags: '', correspondent: '', batchSize: '' };
const DEFAULT_SAMPLE_FORM: SampleFormState = { sampleSize: '', seed: '', minPerStratum: '' };

function scanFormBody(form: ScanFormState) {
  const body: { tags?: string[]; correspondent?: string; batch_size?: number } = {};
  const tags = form.tags.split(',').map((t) => t.trim()).filter(Boolean);
  if (tags.length) body.tags = tags;
  if (form.correspondent.trim()) body.correspondent = form.correspondent.trim();
  if (form.batchSize.trim()) body.batch_size = Number(form.batchSize);
  return body;
}

function sampleFormBody(form: SampleFormState) {
  const body: { sample_size?: number; seed?: string; min_per_stratum?: number } = {};
  if (form.sampleSize.trim()) body.sample_size = Number(form.sampleSize);
  if (form.seed.trim()) body.seed = form.seed.trim();
  if (form.minPerStratum.trim()) body.min_per_stratum = Number(form.minPerStratum);
  return body;
}

/**
 * Manual-trigger UI for the Stage-1 corpus scan / Stage-2 stratified sample
 * background runs (issue #30, Phase 7 slice). Polls `GET /runs` every 3s
 * while any listed run is still `running`.
 */
export function RunsPanel() {
  const [runs, setRuns] = useState<InventoryRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [scanForm, setScanForm] = useState<ScanFormState>(DEFAULT_SCAN_FORM);
  const [sampleTargetRunId, setSampleTargetRunId] = useState<string | null>(null);
  const [sampleForm, setSampleForm] = useState<SampleFormState>(DEFAULT_SAMPLE_FORM);
  const pollRef = useRef<number | null>(null);

  const loadRuns = useCallback(async () => {
    try {
      const data = (await endpoints.ocrQuality.runs(20)) as { runs: InventoryRun[] };
      setRuns(data.runs ?? []);
      setRunsError(null);
    } catch (err) {
      setRunsError(err instanceof Error ? err.message : 'Failed to load runs.');
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    const hasActiveRun = runs.some((r) => r.status === 'running');
    if (!hasActiveRun) {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return undefined;
    }
    if (pollRef.current === null) {
      pollRef.current = window.setInterval(() => void loadRuns(), 3000);
    }
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [runs, loadRuns]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), getToastDuration(toast.tone));
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const execute = async (key: string, operation: () => Promise<unknown>, success: (result: unknown) => string) => {
    setBusy(key);
    try {
      const result = await operation();
      setToast({ message: success(result), tone: 'success' });
      await loadRuns();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const details = err.details as { error?: { run_id?: string }; detail?: { run_id?: string } } | undefined;
        const conflictingRunId = details?.error?.run_id ?? details?.detail?.run_id;
        setToast({
          message: conflictingRunId
            ? `A matching run is already in progress (run_id: ${conflictingRunId}).`
            : err.message,
          tone: 'error',
        });
      } else {
        setToast({ message: err instanceof Error ? err.message : 'Operation failed.', tone: 'error' });
      }
    } finally {
      setBusy(null);
    }
  };

  const startScan = () =>
    execute(
      'start-scan',
      () => endpoints.ocrQuality.startRun(scanFormBody(scanForm)),
      (result) => `Corpus scan started (run_id: ${(result as { run_id: string }).run_id}).`,
    );

  const resumeScan = (runId: string) =>
    execute(
      `resume-${runId}`,
      () => endpoints.ocrQuality.resumeRun(runId, scanFormBody(scanForm)),
      (result) => `Corpus scan resumed (run_id: ${(result as { run_id: string }).run_id}).`,
    );

  const startSample = (sourceRunId: string) =>
    execute(
      `sample-${sourceRunId}`,
      () => endpoints.ocrQuality.sampleRun(sourceRunId, sampleFormBody(sampleForm)),
      (result) => `Stratified sample started (run_id: ${(result as { run_id: string }).run_id}).`,
    );

  return (
    <>
      <Card title="Run new inventory scan">
        <p className="text-muted">
          Starts a Stage-1 corpus scan as a background task on the running OWL service. This does not
          block — the run continues after this request returns; watch its progress below.
        </p>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="ocr-scan-tags">Tags (comma-separated, optional)</label>
            <input
              id="ocr-scan-tags"
              value={scanForm.tags}
              onChange={(event) => setScanForm((current) => ({ ...current, tags: event.target.value }))}
            />
          </div>
          <div className="form-group">
            <label htmlFor="ocr-scan-correspondent">Correspondent (optional)</label>
            <input
              id="ocr-scan-correspondent"
              value={scanForm.correspondent}
              onChange={(event) => setScanForm((current) => ({ ...current, correspondent: event.target.value }))}
            />
          </div>
          <div className="form-group">
            <label htmlFor="ocr-scan-batch-size">Batch size (optional)</label>
            <input
              id="ocr-scan-batch-size"
              type="number"
              min={1}
              max={1000}
              value={scanForm.batchSize}
              onChange={(event) => setScanForm((current) => ({ ...current, batchSize: event.target.value }))}
            />
          </div>
        </div>
        <div className="btn-group">
          <Button variant="primary" onClick={() => void startScan()} disabled={busy !== null}>
            {busy === 'start-scan' ? 'Starting…' : 'Run new inventory scan'}
          </Button>
        </div>
      </Card>

      <Card title="Runs">
        {runsLoading && <SkeletonLoader variant="table" />}
        {!runsLoading && runsError && <ErrorState message={runsError} onRetry={() => void loadRuns()} />}
        {!runsLoading && !runsError && runs.length === 0 && (
          <EmptyState icon="🗂️" title="No runs yet" desc="Start a Stage-1 corpus scan above to begin." />
        )}
        {!runsLoading && !runsError && runs.length > 0 && (
          <div className="ocr-runs-list">
            {runs.map((run) => (
              <div key={run.run_id} className="ocr-run-row">
                <div className="ocr-run-row-header">
                  <span className="ocr-run-id">{run.run_id}</span>
                  <Badge tone={runStatusTone(run.status)}>{run.status}</Badge>
                  <span className="text-muted">{stageLabel(run.stage)}</span>
                </div>
                <div className="text-muted">
                  Started {formatDate(run.started_at)}
                  {run.finished_at ? ` · Finished ${formatDate(run.finished_at)}` : ''}
                  {run.counts?.assessed != null ? ` · Assessed ${run.counts.assessed}` : ''}
                </div>
                <div className="btn-group">
                  {run.status === 'failed' && run.stage === 'stage_1_corpus_scan' && (
                    <Button
                      onClick={() => void resumeScan(run.run_id)}
                      disabled={busy !== null}
                    >
                      {busy === `resume-${run.run_id}` ? 'Resuming…' : 'Resume'}
                    </Button>
                  )}
                  {run.status === 'completed' && run.stage === 'stage_1_corpus_scan' && (
                    <Button
                      onClick={() => setSampleTargetRunId(sampleTargetRunId === run.run_id ? null : run.run_id)}
                      disabled={busy !== null}
                    >
                      Run Stage 2 sample
                    </Button>
                  )}
                </div>
                {sampleTargetRunId === run.run_id && (
                  <div className="ocr-sample-form">
                    <div className="form-row">
                      <div className="form-group">
                        <label htmlFor={`sample-size-${run.run_id}`}>Sample size</label>
                        <input
                          id={`sample-size-${run.run_id}`}
                          type="number"
                          min={1}
                          value={sampleForm.sampleSize}
                          onChange={(event) => setSampleForm((current) => ({ ...current, sampleSize: event.target.value }))}
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor={`sample-seed-${run.run_id}`}>Seed</label>
                        <input
                          id={`sample-seed-${run.run_id}`}
                          value={sampleForm.seed}
                          onChange={(event) => setSampleForm((current) => ({ ...current, seed: event.target.value }))}
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor={`sample-min-${run.run_id}`}>Min per stratum</label>
                        <input
                          id={`sample-min-${run.run_id}`}
                          type="number"
                          min={0}
                          value={sampleForm.minPerStratum}
                          onChange={(event) => setSampleForm((current) => ({ ...current, minPerStratum: event.target.value }))}
                        />
                      </div>
                    </div>
                    <div className="btn-group">
                      <Button
                        variant="primary"
                        onClick={() => void startSample(run.run_id)}
                        disabled={busy !== null}
                      >
                        {busy === `sample-${run.run_id}` ? 'Starting…' : 'Start sample'}
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </>
  );
}

export default function OcrQualityDashboard() {
  const navigate = useNavigate();
  const [distribution, setDistribution] = useState<CorpusDistribution | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    endpoints.ocrQuality
      .distribution()
      .then((data) => setDistribution(data as CorpusDistribution))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load OCR quality distribution.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="ocr-quality-dashboard">
      <PageHeader
        title="OCR Quality"
        desc="Corpus-wide overlay/readability and machine-extraction quality distribution."
        actions={
          <button type="button" className="btn" onClick={() => navigate('/ocr-quality/queue')}>
            Open review queue
          </button>
        }
      />

      <RunsPanel />

      {loading && <SkeletonLoader variant="stat-grid" />}
      {!loading && error && <ErrorState message={error} onRetry={load} />}
      {!loading && !error && distribution && distribution.total_documents === 0 && (
        <EmptyState
          icon="🔍"
          title="No OCR quality assessments yet"
          desc="Run a Stage 1 corpus scan above to populate this dashboard."
        />
      )}
      {!loading && !error && distribution && distribution.total_documents > 0 && (
        <>
          <StatGrid>
            <StatCard title="Assessed documents" metric={distribution.total_documents} />
            {Object.entries(distribution.review_status_distribution).map(([status, count]) => (
              <StatCard
                key={status}
                title={status}
                metric={count}
                status={{ label: `${Math.round((count / distribution.total_documents) * 100)}%`, tone: statusTone(status) }}
              />
            ))}
          </StatGrid>

          <div className="ocr-histogram-grid">
            <DecileHistogram title="Overlay/readability score distribution" distribution={distribution.overlay_score_decile_distribution} />
            <DecileHistogram title="Machine-extraction score distribution" distribution={distribution.machine_score_decile_distribution} />
          </div>

          <Card title="Scorer versions & freshness">
            <div className="ocr-freshness">
              <div>
                <span className="text-muted">Assessed:</span>{' '}
                {formatDate(distribution.oldest_assessed_at)} — {formatDate(distribution.newest_assessed_at)}
              </div>
              <div className="ocr-scorer-versions">
                {Object.entries(distribution.scorer_version_distribution).map(([version, count]) => (
                  <Badge key={version} tone={version === 'unscored' ? 'muted' : 'info'}>
                    {version}: {count}
                  </Badge>
                ))}
              </div>
            </div>
          </Card>

          <Card title="Review by status">
            <div className="ocr-status-links">
              {Object.entries(distribution.review_status_distribution).map(([status, count]) => (
                <button
                  key={status}
                  type="button"
                  className="btn ghost"
                  onClick={() => navigate(`/ocr-quality/queue?review_status=${encodeURIComponent(status)}`)}
                >
                  <Badge tone={statusTone(status)}>{status}</Badge> {count} documents
                </button>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
