import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Badge, Button, Card, DataTable, ErrorState, FilterPills, PageHeader, SkeletonLoader, StatCard, StatGrid } from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/history.css';

type EobRun = {
  id: number | string;
  started_at?: string | null;
  finished_at?: string | null;
  documents_scanned?: number | null;
  eobs_found?: number | null;
  bills_found?: number | null;
  matches_found?: number | null;
  high_confidence?: number | null;
  medium_confidence?: number | null;
  low_confidence?: number | null;
  tags_filter?: string | null;
  correspondent_filter?: string | null;
};

type EobRunsResponse = {
  runs?: EobRun[];
};

type QueueStatus = {
  status?: string;
  started_at?: string | null;
  finished_at?: string | null;
  dry_run?: boolean;
  limit?: number | null;
  read_only?: boolean;
  database?: {
    pending?: number;
    completed?: number;
    dismissed?: number;
    total?: number;
  };
  result?: {
    documents_scanned?: number;
    actions_created?: number;
  };
};

type HistoryRow = {
  id: string;
  type: 'eob' | 'action_queue';
  startedAt?: string | null;
  finishedAt?: string | null;
  title: string;
  status: string;
  tone: 'success' | 'warning' | 'muted' | 'info';
  summary: string;
  metrics: string;
  href: string;
};

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatDuration(start?: string | null, end?: string | null) {
  if (!start || !end) return '—';
  const started = new Date(start).getTime();
  const finished = new Date(end).getTime();
  if (Number.isNaN(started) || Number.isNaN(finished) || finished < started) return '—';
  const totalSeconds = Math.round((finished - started) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  return `${minutes}m ${seconds}s`;
}

/* ── Run range filter options ── */
const RANGE_OPTIONS = [
  { key: '10', label: 'Last 10 runs' },
  { key: '30', label: 'Last 30 runs' },
  { key: 'all', label: 'All runs' },
] as const;

type RangeKey = (typeof RANGE_OPTIONS)[number]['key'];

/* ── Chart data derivation (exported for testing) ── */

type MatchRatePoint = { label: string; rate: number; runId: string | number };
type ConfidencePoint = { label: string; high: number; medium: number; low: number; total: number };

export function computeMatchRates(eobRuns: EobRun[]): MatchRatePoint[] {
  return eobRuns
    .filter((r) => (r.documents_scanned ?? 0) > 0)
    .map((r) => {
      const scanned = r.documents_scanned ?? 0;
      const matched = r.matches_found ?? 0;
      const rate = scanned > 0 ? Math.round((matched / scanned) * 100) : 0;
      const label = r.started_at ? formatShortDate(r.started_at) : `#${r.id}`;
      return { label, rate, runId: r.id };
    });
}

export function computeConfidence(eobRuns: EobRun[]): ConfidencePoint[] {
  return eobRuns
    .filter((r) => ((r.high_confidence ?? 0) + (r.medium_confidence ?? 0) + (r.low_confidence ?? 0)) > 0)
    .map((r) => {
      const high = r.high_confidence ?? 0;
      const medium = r.medium_confidence ?? 0;
      const low = r.low_confidence ?? 0;
      const total = high + medium + low;
      const label = r.started_at ? formatShortDate(r.started_at) : `#${r.id}`;
      return { label, high, medium, low, total };
    });
}

export function formatShortDate(value: string) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function rateClass(rate: number): string {
  if (rate >= 90) return 'excellent';
  if (rate >= 75) return 'good';
  if (rate >= 50) return 'fair';
  return 'poor';
}

/* ── Chart sub-components ── */

function MatchRateChart({ data }: { data: MatchRatePoint[] }) {
  if (data.length === 0) return <div className="history-chart-empty">No match rate data available</div>;
  return (
    <div className="match-rate-chart">
      {data.map((point) => (
        <div key={point.runId} className="match-rate-row">
          <span className="match-rate-label">{point.label}</span>
          <div className="match-rate-bar-bg">
            <div
              className={`match-rate-bar ${rateClass(point.rate)}`}
              style={{ width: `${Math.max(point.rate, 3)}%` }}
            >
              {point.rate}%
            </div>
          </div>
        </div>
      ))}
      <div className="match-rate-note">📈 Match rate = matches found / documents scanned</div>
    </div>
  );
}

function ConfidenceChart({ data }: { data: ConfidencePoint[] }) {
  if (data.length === 0) return <div className="history-chart-empty">No confidence data available</div>;
  return (
    <div className="confidence-chart">
      {data.map((point, i) => {
        const hPct = point.total > 0 ? (point.high / point.total) * 100 : 0;
        const mPct = point.total > 0 ? (point.medium / point.total) * 100 : 0;
        const lPct = point.total > 0 ? (point.low / point.total) * 100 : 0;
        return (
          <div key={i} className="confidence-row">
            <span className="confidence-label">{point.label}</span>
            <div className="confidence-bar-bg" title={`High: ${point.high}  Med: ${point.medium}  Low: ${point.low}`}>
              {hPct > 0 && <div className="confidence-seg high" style={{ width: `${hPct}%` }}>{point.high}</div>}
              {mPct > 0 && <div className="confidence-seg medium" style={{ width: `${mPct}%` }}>{point.medium}</div>}
              {lPct > 0 && <div className="confidence-seg low" style={{ width: `${lPct}%` }}>{point.low}</div>}
            </div>
          </div>
        );
      })}
      <div className="confidence-legend">
        <span className="confidence-legend-item"><span className="confidence-legend-dot high" /> High</span>
        <span className="confidence-legend-item"><span className="confidence-legend-dot medium" /> Medium</span>
        <span className="confidence-legend-item"><span className="confidence-legend-dot low" /> Low</span>
      </div>
    </div>
  );
}

export default function History() {
  const [runs, setRuns] = useState<EobRun[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState('all');
  const [rangeFilter, setRangeFilter] = useState<RangeKey>('10');

  const loadHistory = async () => {
    setLoading(true);
    setError(null);
    try {
      const [runsResponse, queueResponse] = await Promise.all([
        endpoints.eob.runs() as Promise<EobRunsResponse>,
        endpoints.actionQueue.status() as Promise<QueueStatus>,
      ]);
      setRuns(Array.isArray(runsResponse.runs) ? runsResponse.runs : []);
      setQueueStatus(queueResponse);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadHistory();
  }, []);

  const rows = useMemo<HistoryRow[]>(() => {
    const eobRows: HistoryRow[] = runs.map((run) => ({
      id: `eob-${run.id}`,
      type: 'eob',
      startedAt: run.started_at,
      finishedAt: run.finished_at,
      title: `EOB matching run #${run.id}`,
      status: run.finished_at ? 'Completed' : 'In progress',
      tone: run.finished_at ? 'success' : 'warning',
      summary: `${run.matches_found ?? 0} matches · ${run.eobs_found ?? 0} EOBs · ${run.bills_found ?? 0} bills`,
      metrics: `${run.documents_scanned ?? 0} docs scanned · high/med/low ${run.high_confidence ?? 0}/${run.medium_confidence ?? 0}/${run.low_confidence ?? 0}`,
      href: '/eob',
    }));

    const queueRows: HistoryRow[] = [];
    if (queueStatus?.started_at || queueStatus?.finished_at || queueStatus?.status === 'running') {
      queueRows.push({
        id: 'action-queue-latest',
        type: 'action_queue',
        startedAt: queueStatus.started_at,
        finishedAt: queueStatus.finished_at,
        title: queueStatus.dry_run ? 'Action queue dry run' : 'Action queue run',
        status: queueStatus.status === 'running' ? 'Running' : queueStatus.status === 'ok' ? 'Completed' : queueStatus.status ?? 'Unknown',
        tone: queueStatus.status === 'running' ? 'warning' : queueStatus.status === 'ok' ? 'success' : 'muted',
        summary: `${queueStatus.database?.pending ?? 0} pending · ${queueStatus.database?.completed ?? 0} completed · ${queueStatus.database?.dismissed ?? 0} dismissed`,
        metrics: `${queueStatus.result?.documents_scanned ?? 0} docs scanned · limit ${queueStatus.limit ?? '—'}${queueStatus.read_only ? ' · read-only' : ''}`,
        href: '/action-queue',
      });
    }

    return [...eobRows, ...queueRows].sort((a, b) => {
      const aTime = a.startedAt ? new Date(a.startedAt).getTime() : 0;
      const bTime = b.startedAt ? new Date(b.startedAt).getTime() : 0;
      return bTime - aTime;
    });
  }, [queueStatus, runs]);

  const filteredRows = useMemo(() => {
    if (typeFilter === 'all') return rows;
    return rows.filter((row) => row.type === typeFilter);
  }, [rows, typeFilter]);

  /* ── Ranged EOB runs for charts ── */
  const sortedEobRuns = useMemo(() => {
    return [...runs]
      .filter((r) => r.started_at)
      .sort((a, b) => new Date(a.started_at!).getTime() - new Date(b.started_at!).getTime());
  }, [runs]);

  const rangedRuns = useMemo(() => {
    if (rangeFilter === 'all') return sortedEobRuns;
    const limit = Number(rangeFilter);
    return sortedEobRuns.slice(-limit);
  }, [sortedEobRuns, rangeFilter]);

  const matchRateData = useMemo(() => computeMatchRates(rangedRuns), [rangedRuns]);
  const confidenceData = useMemo(() => computeConfidence(rangedRuns), [rangedRuns]);

  /* ── Summary stats ── */
  const avgMatchRate = useMemo(() => {
    if (matchRateData.length === 0) return 0;
    return Math.round(matchRateData.reduce((s, d) => s + d.rate, 0) / matchRateData.length);
  }, [matchRateData]);

  const bestMatchRate = useMemo(() => {
    if (matchRateData.length === 0) return 0;
    return Math.max(...matchRateData.map((d) => d.rate));
  }, [matchRateData]);

  const avgConfidence = useMemo(() => {
    const totals = rangedRuns.reduce(
      (acc, r) => {
        acc.high += r.high_confidence ?? 0;
        acc.total += (r.high_confidence ?? 0) + (r.medium_confidence ?? 0) + (r.low_confidence ?? 0);
        return acc;
      },
      { high: 0, total: 0 },
    );
    return totals.total > 0 ? Math.round((totals.high / totals.total) * 100) : 0;
  }, [rangedRuns]);

  return (
    <>
      <PageHeader
        title="History"
        desc="Review recent pipeline activity across the rebuilt hub. EOB runs are fully historical today, while action queue history is synthesized from its latest status endpoint."
        actions={
          <Button onClick={() => void loadHistory()} disabled={loading}>
            Refresh
          </Button>
        }
      />

      {loading ? (
        <><SkeletonLoader variant="stat-grid" /><div className="section"><SkeletonLoader variant="table" /></div></>
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadHistory()} />
      ) : (
        <>
          <StatGrid>
            <StatCard title="Total runs" metric={runs.length} desc="EOB matching runs recorded in the history endpoint." />
            <StatCard title="Avg match rate" metric={`${avgMatchRate}%`} desc="Average matches / documents across selected runs." />
            <StatCard title="Best match rate" metric={`${bestMatchRate}%`} desc="Highest single-run match rate achieved." />
            <StatCard title="Avg high confidence" metric={`${avgConfidence}%`} desc="Percentage of matches classified as high confidence." />
          </StatGrid>

          {/* ── Range filter ── */}
          <div className="history-range-filter">
            {RANGE_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                className={`history-range-btn${rangeFilter === opt.key ? ' active' : ''}`}
                aria-pressed={rangeFilter === opt.key}
                onClick={() => setRangeFilter(opt.key)}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* ── Charts ── */}
          <div className="history-charts">
            <div className="history-chart-card">
              <div className="history-chart-title">🎯 Match Rate Over Time</div>
              <MatchRateChart data={matchRateData} />
            </div>
            <div className="history-chart-card">
              <div className="history-chart-title">📊 Confidence Distribution</div>
              <ConfidenceChart data={confidenceData} />
            </div>
          </div>

          <div className="section" style={{ marginTop: 20 }}>
            <Card title="History coverage">
              <div style={{ display: 'grid', gap: 10 }}>
                <div className="text-muted" style={{ fontSize: '0.84rem' }}>
                  EOB matching exposes a true <code>/api/eob/runs</code> history stream. The action queue only exposes its latest run status today, so this page renders that as a synthetic history row.
                </div>
                <div className="text-muted" style={{ fontSize: '0.84rem' }}>
                  TODO: add statement discovery history here once the backend publishes a dedicated runs endpoint instead of only live trigger/stream APIs.
                </div>
              </div>
            </Card>
          </div>

          <div className="section">
            <Card title="Run filters">
              <FilterPills
                active={typeFilter}
                onChange={setTypeFilter}
                options={[
                  { key: 'all', label: 'All sources' },
                  { key: 'eob', label: 'EOB matching' },
                  { key: 'action_queue', label: 'Action queue' },
                ]}
              />
            </Card>
          </div>

          <div className="section">
            <Card title="Pipeline runs" actions={<Badge tone="info">{filteredRows.length} rows</Badge>}>
              <DataTable
                rows={filteredRows}
                rowKey={(row) => row.id}
                emptyLabel="No runs are available for the selected source."
                columns={[
                  {
                    key: 'started',
                    header: 'Started',
                    width: '170px',
                    render: (row) => formatDateTime(row.startedAt),
                  },
                  {
                    key: 'pipeline',
                    header: 'Pipeline',
                    width: '210px',
                    render: (row) => (
                      <div>
                        <div style={{ fontWeight: 700 }}>{row.title}</div>
                        <div className="text-muted" style={{ fontSize: '0.8rem', marginTop: 4 }}>
                          {row.type === 'eob' ? 'Scoring, extraction, and match generation' : 'Action generation and follow-up triage'}
                        </div>
                      </div>
                    ),
                  },
                  {
                    key: 'status',
                    header: 'Outcome',
                    width: '120px',
                    render: (row) => <Badge tone={row.tone}>{row.status}</Badge>,
                  },
                  {
                    key: 'duration',
                    header: 'Duration',
                    width: '110px',
                    render: (row) => formatDuration(row.startedAt, row.finishedAt),
                  },
                  {
                    key: 'counts',
                    header: 'Counts',
                    render: (row) => (
                      <div>
                        <div>{row.summary}</div>
                        <div className="text-muted" style={{ fontSize: '0.8rem', marginTop: 4 }}>{row.metrics}</div>
                      </div>
                    ),
                  },
                  {
                    key: 'details',
                    header: 'Details',
                    width: '120px',
                    render: (row) => (
                      <Link to={row.href} style={{ color: 'var(--accent)', fontWeight: 600 }}>
                        Open view →
                      </Link>
                    ),
                  },
                ]}
              />
            </Card>
          </div>
        </>
      )}
    </>
  );
}

