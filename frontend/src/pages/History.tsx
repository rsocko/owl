import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Badge, Button, Card, DataTable, ErrorState, FilterPills, LoadingState, PageHeader, SkeletonLoader, StatCard, StatGrid } from '../components/ui';
import { endpoints } from '../lib/api';

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

export default function History() {
  const [runs, setRuns] = useState<EobRun[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState('all');

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

  const totalDocs = useMemo(() => runs.reduce((sum, run) => sum + (run.documents_scanned ?? 0), 0), [runs]);
  const latestRun = rows[0];

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
            <StatCard title="Visible runs" metric={rows.length} desc="Combined history rows from available backend sources." />
            <StatCard title="Latest activity" metric={latestRun ? latestRun.title : '—'} desc={latestRun ? formatDateTime(latestRun.startedAt) : 'No pipeline activity yet.'} />
            <StatCard title="EOB docs scanned" metric={totalDocs} desc="Aggregate document volume across the EOB run history endpoint." />
            <StatCard title="Action queue state" metric={queueStatus?.status ?? 'idle'} desc={`${queueStatus?.database?.pending ?? 0} pending actions currently tracked.`} status={{ label: queueStatus?.read_only ? 'Read-only' : 'Writable', tone: queueStatus?.read_only ? 'warning' : 'success' }} />
          </StatGrid>

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

