import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, ProgressBanner, SkeletonLoader, StatCard, StatGrid, Toast } from '../components/ui';
import { useStreamingAction } from '../hooks/useStreamingAction';
import { endpoints } from '../lib/api';
import '../styles/statements.css';

interface MissingStatement {
  id?: string;
  correspondent?: string;
  correspondent_id?: number | null;
  expected_period?: string;
  frequency?: string;
  last_received_date?: string | null;
  days_overdue?: number;
}

interface DiscoveredProvider {
  provider_key: string;
  provider_name: string;
  correspondent_id?: number | null;
  document_count: number;
  normalized_title: string;
  title_consistency: number;
  frequency: string;
  pattern_type: string;
  confidence: number;
  anchor_day?: number | null;
  variance_days: number;
  grace_period_days: number;
  sample_document_ids: number[];
  first_seen: string;
  last_seen: string;
}

interface ProvidersResponse {
  providers: DiscoveredProvider[];
  analyzed_documents: number;
  run_at: string | null;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong while loading statements.';
}

function buildSeriesId(row: MissingStatement): string {
  return row.id ?? `${row.correspondent ?? 'statement'}-${row.expected_period ?? 'unknown'}`;
}

function formatExpectedPeriod(period?: string): string {
  if (!period) return 'Unknown period';
  const parsed = new Date(`${period}-01T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return period;
  return parsed.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

function getPriority(daysOverdue?: number): { label: string; tone: 'ok' | 'warning' | 'danger' } {
  const days = daysOverdue ?? 0;
  if (days >= 45) return { label: 'Critical', tone: 'danger' };
  if (days >= 14) return { label: 'High', tone: 'warning' };
  return { label: 'Watch', tone: 'ok' };
}

function getConfidenceTone(confidence: number): 'ok' | 'warning' | 'danger' {
  if (confidence >= 0.85) return 'ok';
  if (confidence >= 0.7) return 'warning';
  return 'danger';
}

function formatDate(iso?: string | null): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatPatternType(patternType: string, anchorDay?: number | null): string {
  switch (patternType) {
    case 'fixed_day':
      return anchorDay ? `Day ${anchorDay}` : 'Fixed day';
    case 'last_day':
      return 'Last day of month';
    case 'last_business_day':
      return 'Last business day';
    case 'variable':
      return anchorDay ? `~Day ${anchorDay}` : 'Variable';
    default:
      return patternType;
  }
}

export default function Statements() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<MissingStatement[]>([]);
  const [providers, setProviders] = useState<DiscoveredProvider[]>([]);
  const [providersRunAt, setProvidersRunAt] = useState<string | null>(null);
  const [analyzedDocs, setAnalyzedDocs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [discoveryState, runDiscoveryStream, cancelDiscovery] = useStreamingAction();
  const [recsState, runRecsStream, cancelRecs] = useStreamingAction();
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);
  const [activeTab, setActiveTab] = useState<'discovered' | 'missing'>('discovered');

  const loadStatements = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [missingResponse, providersResponse] = await Promise.all([
        endpoints.statements.missing() as Promise<MissingStatement[]>,
        endpoints.statements.providers() as Promise<ProvidersResponse>,
      ]);
      setRows(Array.isArray(missingResponse) ? missingResponse : []);
      setProviders(providersResponse?.providers ?? []);
      setProvidersRunAt(providersResponse?.run_at ?? null);
      setAnalyzedDocs(providersResponse?.analyzed_documents ?? 0);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatements();
  }, [loadStatements]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeoutId = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeoutId);
  }, [toast]);

  // Show streaming errors as toasts
  useEffect(() => {
    if (discoveryState.error) setToast({ message: discoveryState.error, tone: 'error' });
  }, [discoveryState.error]);
  useEffect(() => {
    if (recsState.error) setToast({ message: recsState.error, tone: 'error' });
  }, [recsState.error]);

  const summary = useMemo(() => {
    const severe = rows.filter((row) => (row.days_overdue ?? 0) >= 45).length;
    const overdueDays = rows.map((row) => row.days_overdue ?? 0);
    const maxOverdue = overdueDays.length > 0 ? Math.max(...overdueDays) : 0;
    return { severe, maxOverdue };
  }, [rows]);

  const anyRunning = discoveryState.running || recsState.running;

  const runDiscovery = useCallback(() => {
    runDiscoveryStream(endpoints.statements.discoveryStreamUrl, () => {
      setToast({ message: 'Statement discovery run completed.', tone: 'success' });
      void loadStatements();
    });
  }, [runDiscoveryStream, loadStatements]);

  const runRecommendations = useCallback(() => {
    const todayIso = new Date().toISOString().slice(0, 10);
    runRecsStream(endpoints.statements.recommendationsStreamUrl(todayIso), () => {
      setToast({ message: 'Statement recommendations run completed.', tone: 'success' });
      void loadStatements();
    });
  }, [runRecsStream, loadStatements]);

  return (
    <>
      <PageHeader
        title="Statements"
        desc="Track expected statement periods, trigger discovery, and review providers that still need documents."
        actions={
          <div className="btn-group">
            <Button variant="primary" onClick={runDiscovery} disabled={anyRunning}>
              {discoveryState.running ? 'Running discovery…' : 'Run discovery'}
            </Button>
            <Button variant="success" onClick={runRecommendations} disabled={anyRunning}>
              {recsState.running ? 'Running recommendations…' : 'Run recommendations'}
            </Button>
          </div>
        }
      />

      {discoveryState.running && discoveryState.progress && (
        <ProgressBanner
          stage={discoveryState.progress.stage}
          message={discoveryState.progress.message}
          current={discoveryState.progress.current}
          total={discoveryState.progress.total}
          onCancel={cancelDiscovery}
        />
      )}
      {recsState.running && recsState.progress && (
        <ProgressBanner
          stage={recsState.progress.stage}
          message={recsState.progress.message}
          current={recsState.progress.current}
          total={recsState.progress.total}
          onCancel={cancelRecs}
        />
      )}

      <StatGrid>
        <StatCard title="Discovered providers" metric={providers.length} desc={`From ${analyzedDocs} analyzed documents`} />
        <StatCard title="Missing series" metric={rows.length} desc="Providers with an outstanding expected statement" />
        <StatCard title="Critical overdue" metric={summary.severe} desc="45+ days overdue" status={summary.severe > 0 ? { label: 'Needs review', tone: 'warn' } : { label: 'Stable', tone: 'ok' }} />
        <StatCard title="Longest overdue" metric={`${summary.maxOverdue}d`} desc="Worst-case delay across all missing statements" />
      </StatGrid>

      <div className="section" style={{ marginTop: 18 }}>
        <div className="statements-tabs">
          <button
            type="button"
            className={`statements-tab ${activeTab === 'discovered' ? 'statements-tab--active' : ''}`}
            onClick={() => setActiveTab('discovered')}
          >
            Discovered providers ({providers.length})
          </button>
          <button
            type="button"
            className={`statements-tab ${activeTab === 'missing' ? 'statements-tab--active' : ''}`}
            onClick={() => setActiveTab('missing')}
          >
            Missing statements ({rows.length})
          </button>
        </div>

        {activeTab === 'discovered' && (
          <Card
            title="Discovered providers"
            actions={
              providersRunAt ? (
                <span className="text-muted">Last discovery: {formatDate(providersRunAt)}</span>
              ) : (
                <span className="text-muted">No discovery run yet</span>
              )
            }
          >
            {loading ? <SkeletonLoader variant="table" /> : null}
            {!loading && error ? <ErrorState message={error} onRetry={() => void loadStatements()} /> : null}
            {!loading && !error && providers.length === 0 ? (
              <EmptyState title="No providers discovered" desc="Run discovery to scan your documents and detect recurring statement providers." />
            ) : null}
            {!loading && !error && providers.length > 0 ? (
              <div className="statements-table-wrapper">
                <table className="data-table statements-table">
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Frequency</th>
                      <th>Pattern</th>
                      <th>Confidence</th>
                      <th>Documents</th>
                      <th>First seen</th>
                      <th>Last seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {providers.map((provider) => (
                      <tr
                        key={provider.provider_key}
                        className="statements-row"
                        onClick={() => navigate(`/statements/${encodeURIComponent(provider.provider_key)}`)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            navigate(`/statements/${encodeURIComponent(provider.provider_key)}`);
                          }
                        }}
                        tabIndex={0}
                      >
                        <td>
                          <div className="statements-provider">{provider.provider_name}</div>
                          <div className="statements-subtle">{provider.normalized_title}</div>
                        </td>
                        <td>
                          <Badge tone="ok">{provider.frequency}</Badge>
                        </td>
                        <td>
                          <div className="statements-provider">{formatPatternType(provider.pattern_type, provider.anchor_day)}</div>
                          <div className="statements-subtle">±{provider.variance_days}d variance</div>
                        </td>
                        <td>
                          <Badge tone={getConfidenceTone(provider.confidence)}>
                            {Math.round(provider.confidence * 100)}%
                          </Badge>
                        </td>
                        <td>{provider.document_count}</td>
                        <td>{formatDate(provider.first_seen)}</td>
                        <td>{formatDate(provider.last_seen)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Card>
        )}

        {activeTab === 'missing' && (
          <Card title="Missing statements" actions={<span className="text-muted">Click a row to open the series detail view.</span>}>
            {loading ? <SkeletonLoader variant="table" /> : null}
            {!loading && error ? <ErrorState message={error} onRetry={() => void loadStatements()} /> : null}
            {!loading && !error && rows.length === 0 ? (
              <EmptyState title="No missing statements" desc="Run recommendations to refresh expected periods if you recently ingested new statements." />
            ) : null}
            {!loading && !error && rows.length > 0 ? (
              <div className="statements-table-wrapper">
                <table className="data-table statements-table">
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Expected period</th>
                      <th>Days overdue</th>
                      <th>Priority</th>
                      <th>Last received</th>
                      <th>Series key</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const rowId = buildSeriesId(row);
                      const priority = getPriority(row.days_overdue);
                      return (
                        <tr
                          key={`${rowId}-${row.expected_period ?? 'period'}`}
                          className="statements-row"
                          onClick={() => navigate(`/statements/${encodeURIComponent(rowId)}`)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              navigate(`/statements/${encodeURIComponent(rowId)}`);
                            }
                          }}
                          tabIndex={0}
                        >
                          <td>
                            <div className="statements-provider">{row.correspondent ?? 'Unknown provider'}</div>
                            <div className="statements-subtle">{row.frequency ?? 'Unknown cadence'}</div>
                          </td>
                          <td>
                            <div className="statements-provider">{formatExpectedPeriod(row.expected_period)}</div>
                            <div className="statements-subtle">{row.expected_period ?? 'Unknown period'}</div>
                          </td>
                          <td>
                            <div className="statements-provider">{row.days_overdue ?? 0} days</div>
                            <div className="statements-subtle">Missing from recommendation feed</div>
                          </td>
                          <td>
                            <Badge tone={priority.tone}>{priority.label}</Badge>
                          </td>
                          <td>{row.last_received_date ?? 'Not provided'}</td>
                          <td>
                            <code className="statements-series-key">{rowId}</code>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Card>
        )}
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}
