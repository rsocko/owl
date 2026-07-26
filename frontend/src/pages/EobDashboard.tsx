import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  ConfidenceBar,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonLoader,
  StatCard,
  StatGrid,
  Toast,
  confidenceTone,
} from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/eob-pages.css';

interface AlertItem {
  id: number | string;
  alert_type?: string | null;
  severity?: string | null;
  module?: string | null;
  title?: string | null;
  description?: string | null;
  action_url?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
}

interface AlertsResponse {
  alerts?: AlertItem[];
  total?: number;
}

interface EobRun {
  id: number;
  started_at?: string | null;
  finished_at?: string | null;
  documents_scanned?: number | null;
  eobs_found?: number | null;
  bills_found?: number | null;
  matches_found?: number | null;
  high_confidence?: number | null;
  medium_confidence?: number | null;
  low_confidence?: number | null;
}

interface EobMatch {
  id: number;
  run_id?: number | null;
  eob_document_id?: number | null;
  bill_document_id?: number | null;
  score?: number | null;
  confidence?: string | null;
  breakdown?: {
    date?: number | null;
    provider?: number | null;
    patient?: number | null;
    amount?: number | null;
    procedures?: number | null;
  } | null;
  status?: string | null;
  linked_in_paperless?: boolean | null;
  eob_preview_url?: string | null;
  bill_preview_url?: string | null;
  created_at?: string | null;
  confirmed_at?: string | null;
}

interface EobResultsResponse {
  status?: string;
  message?: string;
  run?: EobRun | null;
  matches?: EobMatch[];
}

interface EobRunsResponse {
  runs?: EobRun[];
}

interface EobMatchesResponse {
  matches?: EobMatch[];
}

interface EobUnmatchedItem {
  id: string;
  provider?: string | null;
  amount?: number | null;
  date_of_service?: string | null;
  patient_responsibility?: number | null;
  document_url?: string | null;
  created_at?: string | null;
}

interface EobCheckResponse {
  read_only?: boolean;
  write_to_paperless?: boolean;
}

interface CoverageBreakdownRow {
  name: string;
  count: number;
  total_billed: number;
  total_plan_pays: number;
  total_patient_responsibility: number;
  coverage_pct: number;
}

interface CoverageSummary {
  record_count: number;
  total_billed: number;
  total_allowed: number;
  total_plan_pays: number;
  total_patient_responsibility: number;
  coverage_pct: number;
}

interface CoverageResponse {
  status?: string;
  summary?: CoverageSummary;
  by_insurance_company?: CoverageBreakdownRow[];
  by_provider?: CoverageBreakdownRow[];
  by_month?: CoverageBreakdownRow[];
}

type ToastState = { message: string; tone: 'success' | 'error' } | null;

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatCurrency(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

function formatPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return `${Math.round(value)}%`;
}

function statusTone(status?: string | null): 'success' | 'warning' | 'danger' | 'muted' {
  switch ((status || '').toLowerCase()) {
    case 'confirmed':
      return 'success';
    case 'rejected':
      return 'danger';
    case 'candidate':
    case 'pending':
      return 'warning';
    default:
      return 'muted';
  }
}

function statusLabel(status?: string | null) {
  switch ((status || '').toLowerCase()) {
    case 'confirmed':
      return 'Confirmed';
    case 'rejected':
      return 'Rejected';
    case 'candidate':
      return 'Pending';
    default:
      return status || 'Unknown';
  }
}

function confidenceLabel(score?: number | null) {
  if (typeof score !== 'number') return 'No score';
  const tone = confidenceTone(score);
  return tone === 'high' ? 'High confidence' : tone === 'medium' ? 'Review' : 'Low confidence';
}

function scoreBadgeTone(score?: number | null): 'success' | 'warning' | 'danger' {
  const tone = confidenceTone(score ?? 0);
  return tone === 'high' ? 'success' : tone === 'medium' ? 'warning' : 'danger';
}

export default function EobDashboard() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isClassifying, setIsClassifying] = useState(false);
  const [results, setResults] = useState<EobResultsResponse | null>(null);
  const [runs, setRuns] = useState<EobRun[]>([]);
  const [matches, setMatches] = useState<EobMatch[]>([]);
  const [unmatched, setUnmatched] = useState<EobUnmatchedItem[]>([]);
  const [check, setCheck] = useState<EobCheckResponse | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [alertBusyId, setAlertBusyId] = useState<string | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [coverageTab, setCoverageTab] = useState<'insurance_company' | 'provider' | 'month'>('insurance_company');

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [resultsRes, runsRes, matchesRes, unmatchedRes, checkRes, alertsRes] = await Promise.all([
        endpoints.eob.results() as Promise<EobResultsResponse>,
        endpoints.eob.runs() as Promise<EobRunsResponse>,
        endpoints.eob.matches('limit=8') as Promise<EobMatchesResponse>,
        endpoints.eob.unmatched() as Promise<EobUnmatchedItem[]>,
        endpoints.eob.check() as Promise<EobCheckResponse>,
        endpoints.alerts.list('module=eob&resolved=false&limit=5') as Promise<AlertsResponse>,
      ]);
      setResults(resultsRes);
      setRuns(runsRes.runs ?? []);
      setMatches(matchesRes.matches ?? []);
      setUnmatched(Array.isArray(unmatchedRes) ? unmatchedRes : []);
      setCheck(checkRes);
      setAlerts(Array.isArray(alertsRes.alerts) ? alertsRes.alerts : []);
      // Coverage is supplementary — don't let it block the dashboard
      try {
        const coverageRes = await (endpoints.eob.coverage() as Promise<CoverageResponse>);
        setCoverage(coverageRes);
      } catch {
        setCoverage(null);
      }
      const latestRun = runsRes.runs?.[0] ?? resultsRes?.run;
      setLastSyncedAt(latestRun?.finished_at ?? latestRun?.started_at ?? null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load EOB dashboard.';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  const matchedCount = useMemo(
    () => results?.run?.matches_found ?? results?.matches?.length ?? matches.length,
    [matches.length, results],
  );
  const pendingCount = useMemo(
    () => matches.filter((match) => (match.status || 'candidate').toLowerCase() === 'candidate').length,
    [matches],
  );
  const confirmedCount = useMemo(
    () => matches.filter((match) => (match.status || '').toLowerCase() === 'confirmed').length,
    [matches],
  );
  const avgConfidence = useMemo(() => {
    if (!matches.length) return null;
    const validScores = matches
      .map((match) => match.score)
      .filter((score): score is number => typeof score === 'number' && !Number.isNaN(score));
    if (!validScores.length) return null;
    return validScores.reduce((sum, score) => sum + score, 0) / validScores.length;
  }, [matches]);

  const totalDue = useMemo(() => {
    if (!unmatched.length) return 0;
    return unmatched.reduce((sum, item) => {
      const amount = item.patient_responsibility ?? item.amount ?? 0;
      return sum + (typeof amount === 'number' ? amount : 0);
    }, 0);
  }, [unmatched]);

  const handleRun = useCallback(async () => {
    setIsRunning(true);
    try {
      const response = (await endpoints.eob.run({ limit: 50, verbose: false })) as {
        summary?: { matches?: number; unmatched_eobs?: number; linked_in_paperless?: number };
      };
      setToast({
        message: `Pipeline completed. ${response.summary?.matches ?? 0} matches, ${response.summary?.unmatched_eobs ?? 0} unmatched EOBs.`,
        tone: 'success',
      });
      await loadDashboard();
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Pipeline run failed.',
        tone: 'error',
      });
    } finally {
      setIsRunning(false);
    }
  }, [loadDashboard]);

  const handleClassify = useCallback(async () => {
    setIsClassifying(true);
    try {
      const response = (await endpoints.eob.classify({ limit: 50 })) as {
        documents_scanned?: number;
        summary?: Record<string, number>;
      };
      const eobCount = response.summary?.eob ?? 0;
      const billCount = response.summary?.bill ?? 0;
      setToast({
        message: `Classified ${response.documents_scanned ?? 0} docs (${eobCount} EOBs, ${billCount} bills).`,
        tone: 'success',
      });
      await loadDashboard();
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Classification failed.',
        tone: 'error',
      });
    } finally {
      setIsClassifying(false);
    }
  }, [loadDashboard]);

  const handleAlertResolve = useCallback(
    async (alertId: number | string) => {
      const key = `resolve:${alertId}`;
      setAlertBusyId(key);
      try {
        await endpoints.alerts.resolve(String(alertId));
        setToast({ message: 'Alert resolved.', tone: 'success' });
        await loadDashboard();
      } catch (err) {
        setToast({ message: err instanceof Error ? err.message : 'Failed to resolve alert.', tone: 'error' });
      } finally {
        setAlertBusyId(null);
      }
    },
    [loadDashboard],
  );

  const handleAlertAcknowledge = useCallback(
    async (alertId: number | string) => {
      const key = `ack:${alertId}`;
      setAlertBusyId(key);
      try {
        await endpoints.alerts.acknowledge(String(alertId));
        setToast({ message: 'Alert acknowledged.', tone: 'success' });
        await loadDashboard();
      } catch (err) {
        setToast({ message: err instanceof Error ? err.message : 'Failed to acknowledge alert.', tone: 'error' });
      } finally {
        setAlertBusyId(null);
      }
    },
    [loadDashboard],
  );

  const alertTone = useCallback((severity?: string | null): 'danger' | 'warning' | 'info' => {
    switch ((severity ?? '').toLowerCase()) {
      case 'critical':
      case 'high':
        return 'danger';
      case 'info':
        return 'info';
      default:
        return 'warning';
    }
  }, []);

  const alertIcon = useCallback((severity?: string | null) => {
    switch ((severity ?? '').toLowerCase()) {
      case 'critical':
      case 'high':
        return '🔴';
      default:
        return '⚠️';
    }
  }, []);

  // Derive unmatched bills count from matches without a confirmed bill
  const unmatchedBillsCount = useMemo(
    () => matches.filter((m) => (m.status || 'candidate').toLowerCase() !== 'confirmed').length,
    [matches],
  );


  if (loading) {
    return (
      <>
        <PageHeader title="EOB Matching" desc="Run the classification and matching pipeline for Explanation of Benefits documents." />
        <SkeletonLoader variant="stat-grid" />
        <div className="section"><SkeletonLoader variant="cards" /></div>
      </>
    );
  }

  if (error) {
    return (
      <>
        <PageHeader title="EOB Matching" desc="Run the classification and matching pipeline for Explanation of Benefits documents." />
        <ErrorState message={error} onRetry={() => void loadDashboard()} />
      </>
    );
  }

  const lastRun = results?.run ?? runs[0] ?? null;

  return (
    <>
      <PageHeader
        title="EOB Matching"
        desc={
          <div className="eob-meta-row">
            <span>Review candidate EOB ↔ bill matches, rerun the matcher, and work the unmatched queue.</span>
            <Badge tone={check?.read_only ? 'warning' : 'success'}>
              {check?.read_only ? 'Read-only mode' : 'Paperless writeback enabled'}
            </Badge>
            {lastSyncedAt && (
              <span className="eob-table-secondary">Last synced: {formatDateTime(lastSyncedAt)}</span>
            )}
          </div>
        }
        actions={
          <div className="btn-group">
            <Button variant="ghost" onClick={() => void loadDashboard()}>
              🔄 Sync
            </Button>
            <Button variant="ghost" onClick={() => navigate('/eob/unmatched')}>
              Unmatched queue
              <Badge tone={unmatched.length ? 'warning' : 'muted'}>{unmatched.length}</Badge>
            </Button>
            <Button variant="primary" onClick={() => void handleRun()} disabled={isRunning}>
              {isRunning ? 'Running…' : 'Run pipeline'}
            </Button>
            <Button onClick={() => void handleClassify()} disabled={isClassifying}>
              {isClassifying ? 'Classifying…' : 'Classify recent docs'}
            </Button>
          </div>
        }
      />

      <div className="eob-page-stack">
        <StatGrid>
          <StatCard
            title="Matched pairs"
            metric={matchedCount}
            desc={`${confirmedCount} confirmed in the current review list`}
            status={{ label: results?.status === 'ok' ? 'Latest run' : 'Idle', tone: results?.status === 'ok' ? 'success' : 'muted' }}
          />
          <StatCard
            title="Pending review"
            metric={pendingCount}
            desc="Candidate matches awaiting confirmation or rejection"
            status={{ label: pendingCount ? 'Needs review' : 'Clear', tone: pendingCount ? 'warning' : 'success' }}
          />
          <StatCard
            title="Unmatched EOBs"
            metric={unmatched.length}
            desc="Manual follow-up queue for documents without a confirmed bill"
            status={{ label: unmatched.length ? 'Open items' : 'None', tone: unmatched.length ? 'warning' : 'success' }}
          />
          <StatCard
            title="Average confidence"
            metric={formatPercent(avgConfidence)}
            desc="Average score across recent candidate matches"
          />
          <StatCard
            title="Total due"
            metric={formatCurrency(totalDue || null) === '—' ? '$0' : formatCurrency(totalDue)}
            desc={`Across ${unmatched.length} unmatched bill${unmatched.length === 1 ? '' : 's'}`}
            status={{ label: totalDue > 0 ? 'Outstanding' : 'Clear', tone: totalDue > 0 ? 'warning' : 'success' }}
          />
          <StatCard
            title="Alerts"
            metric={alerts.length}
            desc="Unacknowledged EOB alerts needing attention"
            status={{ label: alerts.length ? 'Needs attention' : 'Clear', tone: alerts.length ? 'danger' : 'success' }}
          />
          <StatCard
            title="Last run"
            metric={lastRun?.started_at ? formatDate(lastRun.started_at) : 'Never'}
            desc={lastRun?.finished_at ? `Finished ${formatDateTime(lastRun.finished_at)}` : 'Run the pipeline to refresh matches'}
          />
        </StatGrid>

        {/* Coverage Analysis */}
        {coverage?.summary && coverage.summary.record_count > 0 && (
          <Card
            title="Coverage Analysis"
            actions={
              <div className="btn-group">
                {(['insurance_company', 'provider', 'month'] as const).map((tab) => (
                  <Button
                    key={tab}
                    size="sm"
                    variant={coverageTab === tab ? 'primary' : 'ghost'}
                    onClick={() => setCoverageTab(tab)}
                  >
                    {tab === 'insurance_company' ? 'By insurer' : tab === 'provider' ? 'By provider' : 'By month'}
                  </Button>
                ))}
              </div>
            }
          >
            <StatGrid>
              <StatCard
                title="Total billed"
                metric={formatCurrency(coverage.summary.total_billed)}
                desc={`Across ${coverage.summary.record_count} EOB records`}
              />
              <StatCard
                title="Insurance covered"
                metric={formatCurrency(coverage.summary.total_plan_pays)}
                desc={`${coverage.summary.coverage_pct}% of total billed`}
                status={{
                  label: coverage.summary.coverage_pct >= 70 ? 'Good' : coverage.summary.coverage_pct >= 40 ? 'Moderate' : 'Low',
                  tone: coverage.summary.coverage_pct >= 70 ? 'success' : coverage.summary.coverage_pct >= 40 ? 'warning' : 'danger',
                }}
              />
              <StatCard
                title="Patient responsibility"
                metric={formatCurrency(coverage.summary.total_patient_responsibility)}
                desc="Total out-of-pocket across all EOBs"
                status={{
                  label: coverage.summary.total_patient_responsibility > 0 ? 'Outstanding' : 'Clear',
                  tone: coverage.summary.total_patient_responsibility > 0 ? 'warning' : 'success',
                }}
              />
            </StatGrid>
            <div style={{ marginTop: 16 }}>
              <DataTable<CoverageBreakdownRow>
                rowKey={(row) => row.name}
                rows={
                  coverageTab === 'insurance_company'
                    ? coverage.by_insurance_company ?? []
                    : coverageTab === 'provider'
                      ? coverage.by_provider ?? []
                      : coverage.by_month ?? []
                }
                emptyLabel="No breakdown data available."
                columns={[
                  {
                    key: 'name',
                    header: coverageTab === 'insurance_company' ? 'Insurance company' : coverageTab === 'provider' ? 'Provider' : 'Month',
                    render: (row) => (
                      <div className="eob-table-primary">
                        <strong>{row.name}</strong>
                        <span className="eob-table-secondary">{row.count} record{row.count === 1 ? '' : 's'}</span>
                      </div>
                    ),
                  },
                  {
                    key: 'billed',
                    header: 'Total billed',
                    width: '140px',
                    render: (row) => formatCurrency(row.total_billed),
                  },
                  {
                    key: 'plan_pays',
                    header: 'Plan pays',
                    width: '140px',
                    render: (row) => formatCurrency(row.total_plan_pays),
                  },
                  {
                    key: 'patient',
                    header: 'Patient resp.',
                    width: '140px',
                    render: (row) => formatCurrency(row.total_patient_responsibility),
                  },
                  {
                    key: 'coverage',
                    header: 'Coverage %',
                    width: '120px',
                    render: (row) => (
                      <Badge tone={row.coverage_pct >= 70 ? 'success' : row.coverage_pct >= 40 ? 'warning' : 'danger'}>
                        {row.coverage_pct}%
                      </Badge>
                    ),
                  },
                ]}
              />
            </div>
          </Card>
        )}

        {/* Inline Alerts */}
        <section>
          <div className="eob-alert-section-title" style={{ fontSize: '0.92rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            ⚠️ Alerts &amp; Action Items
            {alerts.length > 0 && <Badge tone="warning">{alerts.length}</Badge>}
          </div>
          {alerts.length > 0 ? (
            <div className="eob-alerts-section">
              {alerts.map((alert) => {
                const tone = alertTone(alert.severity);
                const icon = alertIcon(alert.severity);
                const matchId = alert.metadata?.match_id as string | number | undefined;
                return (
                  <div key={String(alert.id)} className={`eob-alert-card ${tone === 'danger' ? 'danger' : tone === 'info' ? 'info' : ''}`}>
                    <div className="eob-alert-header">
                      {icon} {alert.title ?? 'Untitled alert'}
                    </div>
                    {alert.description && <div className="eob-alert-body">{alert.description}</div>}
                    <div className="eob-alert-actions">
                      {matchId && (
                        <Button size="sm" variant="primary" onClick={() => navigate(`/eob/matches/${matchId}`)}>
                          Review Match →
                        </Button>
                      )}
                      {(alert.alert_type ?? '').toLowerCase().includes('overdue') && (
                        <Button
                          size="sm"
                          variant="success"
                          onClick={() => void handleAlertResolve(alert.id)}
                          disabled={alertBusyId !== null}
                        >
                          {alertBusyId === `resolve:${alert.id}` ? 'Saving…' : '✓ Mark as Paid'}
                        </Button>
                      )}
                      {!alert.acknowledged_at && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void handleAlertAcknowledge(alert.id)}
                          disabled={alertBusyId !== null}
                        >
                          {alertBusyId === `ack:${alert.id}` ? 'Saving…' : 'Dismiss'}
                        </Button>
                      )}
                      {alert.action_url && !matchId && (
                        <Button size="sm" variant="ghost" onClick={() => navigate(alert.action_url!)}>
                          View Details →
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="eob-alerts-empty">✅ No active EOB alerts — everything looks good.</div>
          )}
        </section>

        <Card
          title="Recent runs"
          actions={<span className="text-muted">{runs.length} total recorded</span>}
        >
          {runs.length ? (
            <DataTable<EobRun>
              rowKey={(run) => `run-${run.id}`}
              rows={runs.slice(0, 5)}
              columns={[
                {
                  key: 'started',
                  header: 'Started',
                  render: (run) => (
                    <div className="eob-table-primary">
                      <strong>Run #{run.id}</strong>
                      <span className="eob-table-secondary">{formatDateTime(run.started_at)}</span>
                    </div>
                  ),
                },
                {
                  key: 'documents',
                  header: 'Documents',
                  render: (run) => `${run.documents_scanned ?? 0}`,
                  width: '100px',
                },
                {
                  key: 'classified',
                  header: 'Classified',
                  render: (run) => (
                    <div className="eob-table-primary">
                      <span>EOBs: {run.eobs_found ?? 0}</span>
                      <span className="eob-table-secondary">Bills: {run.bills_found ?? 0}</span>
                    </div>
                  ),
                },
                {
                  key: 'matches',
                  header: 'Matches',
                  render: (run) => (
                    <div className="eob-table-primary">
                      <span>{run.matches_found ?? 0} pairs</span>
                      <span className="eob-table-secondary">
                        H/M/L: {run.high_confidence ?? 0}/{run.medium_confidence ?? 0}/{run.low_confidence ?? 0}
                      </span>
                    </div>
                  ),
                },
                {
                  key: 'finished',
                  header: 'Completed',
                  render: (run) => formatDateTime(run.finished_at),
                },
              ]}
            />
          ) : (
            <EmptyState
              title="No matching runs yet"
              desc="Run the pipeline to populate run history, summary metrics, and candidate matches."
            />
          )}
        </Card>

        <Card
          title="Recent matches"
          actions={
            <div className="eob-actions-end">
              <span className="text-muted">Top scores first</span>
              <Button size="sm" onClick={() => navigate('/eob/unmatched')}>
                Review unmatched
              </Button>
            </div>
          }
        >
          {matches.length ? (
            <DataTable<EobMatch>
              rowKey={(match) => `match-${match.id}`}
              rows={matches}
              columns={[
                {
                  key: 'pair',
                  header: 'Document pair',
                  render: (match) => (
                    <div className="eob-table-primary">
                      <strong>
                        EOB #{match.eob_document_id ?? '—'} ↔ Bill #{match.bill_document_id ?? '—'}
                      </strong>
                      <span className="eob-table-secondary">Run #{match.run_id ?? '—'}</span>
                    </div>
                  ),
                },
                {
                  key: 'confidence',
                  header: 'Confidence',
                  width: '220px',
                  render: (match) => (
                    <div className="eob-score-cell">
                      <Badge tone={scoreBadgeTone(match.score)}>{confidenceLabel(match.score)}</Badge>
                      <ConfidenceBar label="Overall" pct={Math.round(match.score ?? 0)} />
                    </div>
                  ),
                },
                {
                  key: 'signals',
                  header: 'Signals',
                  render: (match) => (
                    <div className="eob-table-primary">
                      <span>Date {formatPercent(match.breakdown?.date)}</span>
                      <span className="eob-table-secondary">
                        Provider {formatPercent(match.breakdown?.provider)} · Amount {formatPercent(match.breakdown?.amount)}
                      </span>
                    </div>
                  ),
                },
                {
                  key: 'status',
                  header: 'Status',
                  render: (match) => <Badge tone={statusTone(match.status)}>{statusLabel(match.status)}</Badge>,
                  width: '110px',
                },
                {
                  key: 'created',
                  header: 'Created',
                  render: (match) => formatDateTime(match.created_at),
                  width: '170px',
                },
                {
                  key: 'action',
                  header: '',
                  render: (match) => (
                    <Button size="sm" variant="ghost" onClick={() => navigate(`/eob/matches/${match.id}`)}>
                      Review →
                    </Button>
                  ),
                  width: '110px',
                },
              ]}
            />
          ) : (
            <EmptyState
              title="No match candidates available"
              desc="Run the pipeline after new EOBs or bills arrive to generate a review queue."
            />
          )}
        </Card>

        <Card title="Unmatched follow-up">
          {unmatched.length ? (
            <DataTable<EobUnmatchedItem>
              rowKey={(item) => item.id}
              rows={unmatched.slice(0, 6)}
              columns={[
                {
                  key: 'provider',
                  header: 'Provider',
                  render: (item) => (
                    <div className="eob-table-primary">
                      <strong>{item.provider || 'Unknown provider'}</strong>
                      <span className="eob-table-secondary">Document #{item.id}</span>
                    </div>
                  ),
                },
                {
                  key: 'service-date',
                  header: 'Service date',
                  render: (item) => formatDate(item.date_of_service),
                },
                {
                  key: 'amount',
                  header: 'Billed',
                  render: (item) => formatCurrency(item.amount),
                },
                {
                  key: 'responsibility',
                  header: 'Patient resp.',
                  render: (item) => formatCurrency(item.patient_responsibility),
                },
                {
                  key: 'created',
                  header: 'Queued',
                  render: (item) => formatDateTime(item.created_at),
                },
              ]}
            />
          ) : (
            <EmptyState title="No unmatched EOBs" desc="Every extracted EOB currently has at least one confirmed match." />
          )}
        </Card>

        {/* Quick Stats Footer */}
        <div className="eob-quick-stats">
          <div className="eob-qs-card">
            <div>
              <div className="eob-qs-label">Unmatched EOBs</div>
              <div className="eob-qs-count">{unmatched.length}</div>
            </div>
            <span className="eob-qs-link" onClick={() => navigate('/eob/unmatched')}>View →</span>
          </div>
          <div className="eob-qs-card">
            <div>
              <div className="eob-qs-label">Pending Bills</div>
              <div className="eob-qs-count">{unmatchedBillsCount}</div>
            </div>
            <span className="eob-qs-link" onClick={() => navigate('/eob/unmatched')}>View →</span>
          </div>
        </div>
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}
