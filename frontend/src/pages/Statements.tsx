import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { ColumnFiltersState, SortingState } from '@tanstack/react-table';
import { SortableTable, type SortableColumnDef } from '../components/SortableTable';
import { Badge, Button, Card, ConfidenceBadge, ConfidenceLegend, EmptyState, ErrorState, PageHeader, ProgressBanner, SkeletonLoader, StatCard, StatGrid, Toast } from '../components/ui';
import { useStreamingAction } from '../hooks/useStreamingAction';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/sortable-table.css';
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

function formatDate(iso?: string | null): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  const diffMs = Date.now() - parsed.getTime();
  const diffDays = Math.floor(diffMs / 86_400_000);
  if (diffDays < 0) return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
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

function buildFilterOptions(values: string[]): { value: string; label: string }[] {
  return Array.from(new Set(values))
    .sort((left, right) => left.localeCompare(right))
    .map((value) => ({ value, label: value }));
}

export default function Statements() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<MissingStatement[]>([]);
  const [providers, setProviders] = useState<DiscoveredProvider[]>([]);
  const [providersRunAt, setProvidersRunAt] = useState<string | null>(null);
  const [analyzedDocs, setAnalyzedDocs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [discoveryState, runDiscoveryStream, cancelDiscovery] = useStreamingAction();
  const [recsState, runRecsStream, cancelRecs] = useStreamingAction();
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);
  const [activeTab, setActiveTab] = useState<'discovered' | 'missing'>('discovered');
  const [providerSorting, setProviderSorting] = useState<SortingState>([]);
  const [providerFilters, setProviderFilters] = useState<ColumnFiltersState>([]);
  const [missingSorting, setMissingSorting] = useState<SortingState>([]);
  const [missingFilters, setMissingFilters] = useState<ColumnFiltersState>([]);

  const loadStatements = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setCoverageError(null);

      let missingResponse: MissingStatement[] = [];
      let providersResponse: ProvidersResponse | null = null;

      try {
        missingResponse = (await endpoints.statements.missing()) as MissingStatement[];
      } catch (err) {
        setError(getErrorMessage(err));
      }

      try {
        providersResponse = (await endpoints.statements.providers()) as ProvidersResponse;
      } catch (err) {
        setCoverageError(getErrorMessage(err));
      }

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
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
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

  const providerColumns: SortableColumnDef<DiscoveredProvider>[] = useMemo(() => [
    {
      id: 'provider',
      header: 'Provider',
      accessorFn: (provider) => provider.provider_name.toLowerCase(),
      cell: (provider) => (
        <>
          <div className="statements-provider">{provider.provider_name}</div>
          <div className="statements-subtle">{provider.normalized_title}</div>
        </>
      ),
      filterOptions: buildFilterOptions(providers.map((provider) => provider.provider_name)),
      filterFn: (rowValue, filterValue) => rowValue === filterValue.toLowerCase(),
    },
    {
      id: 'frequency',
      header: 'Frequency',
      accessorFn: (provider) => provider.frequency,
      cell: (provider) => <Badge tone="ok">{provider.frequency}</Badge>,
      filterOptions: buildFilterOptions(providers.map((provider) => provider.frequency)),
    },
    {
      id: 'pattern',
      header: 'Pattern',
      accessorFn: (provider) => formatPatternType(provider.pattern_type, provider.anchor_day),
      cell: (provider) => (
        <>
          <div className="statements-provider">{formatPatternType(provider.pattern_type, provider.anchor_day)}</div>
          <div className="statements-subtle">±{provider.variance_days}d variance</div>
        </>
      ),
      filterOptions: buildFilterOptions(providers.map((provider) => formatPatternType(provider.pattern_type, provider.anchor_day))),
    },
    {
      id: 'confidence',
      header: 'Confidence',
      accessorFn: (provider) => provider.confidence,
      cell: (provider) => <ConfidenceBadge pct={Math.round(provider.confidence * 100)} />,
      filterOptions: [
        { value: 'high', label: '≥85% High' },
        { value: 'medium', label: '60–84% Medium' },
        { value: 'low', label: '<60% Low' },
      ],
      filterFn: (rowValue, filterValue) => {
        const confidence = Number(rowValue);
        if (filterValue === 'high') return confidence >= 0.85;
        if (filterValue === 'medium') return confidence >= 0.6 && confidence < 0.85;
        return confidence < 0.6;
      },
    },
    {
      id: 'documents',
      header: 'Documents',
      accessorFn: (provider) => provider.document_count,
      cell: (provider) => provider.document_count,
    },
    {
      id: 'first_seen',
      header: 'First seen',
      accessorFn: (provider) => provider.first_seen,
      cell: (provider) => formatDate(provider.first_seen),
    },
    {
      id: 'last_seen',
      header: 'Last seen',
      accessorFn: (provider) => provider.last_seen,
      cell: (provider) => formatDate(provider.last_seen),
    },
  ], [providers]);

  const missingColumns: SortableColumnDef<MissingStatement>[] = useMemo(() => [
    {
      id: 'provider',
      header: 'Provider',
      accessorFn: (row) => (row.correspondent ?? 'Unknown provider').toLowerCase(),
      cell: (row) => (
        <>
          <div className="statements-provider">{row.correspondent ?? 'Unknown provider'}</div>
          <div className="statements-subtle">{row.frequency ?? 'Unknown cadence'}</div>
        </>
      ),
      filterOptions: buildFilterOptions(rows.map((row) => row.correspondent ?? 'Unknown provider')),
      filterFn: (rowValue, filterValue) => rowValue === filterValue.toLowerCase(),
    },
    {
      id: 'expected_period',
      header: 'Expected period',
      accessorFn: (row) => row.expected_period ?? '',
      cell: (row) => <div className="statements-provider">{formatExpectedPeriod(row.expected_period)}</div>,
    },
    {
      id: 'days_overdue',
      header: 'Days overdue',
      accessorFn: (row) => row.days_overdue ?? 0,
      cell: (row) => <div className="statements-provider">{row.days_overdue ?? 0} days</div>,
    },
    {
      id: 'priority',
      header: 'Priority',
      accessorFn: (row) => getPriority(row.days_overdue).label,
      cell: (row) => {
        const priority = getPriority(row.days_overdue);
        return <Badge tone={priority.tone}>{priority.label}</Badge>;
      },
      filterOptions: [
        { value: 'Critical', label: 'Critical' },
        { value: 'High', label: 'High' },
        { value: 'Watch', label: 'Watch' },
      ],
    },
    {
      id: 'last_received',
      header: 'Last received',
      accessorFn: (row) => row.last_received_date ?? '',
      cell: (row) => formatDate(row.last_received_date),
    },
    {
      id: 'series_key',
      header: 'Series key',
      accessorFn: (row) => buildSeriesId(row).toLowerCase(),
      cell: (row) => <code className="statements-series-key">{buildSeriesId(row)}</code>,
    },
  ], [rows]);

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
          <div className="statements-action-steps">
            <div className="statements-step">
              <span className="statements-step-number">1</span>
              <Button variant="primary" onClick={runDiscovery} disabled={anyRunning} title="Scans all documents to detect recurring statement providers and their delivery patterns.">
                {discoveryState.running ? 'Running discovery…' : 'Run discovery'}
              </Button>
              <span className="statements-step-desc">Detect providers</span>
            </div>
            <span className="statements-step-arrow">→</span>
            <div className="statements-step">
              <span className={`statements-step-number ${!providersRunAt ? 'disabled' : ''}`}>2</span>
              <Button variant="success" onClick={runRecommendations} disabled={anyRunning || !providersRunAt} title="Analyzes discovered providers to identify missing statement periods. Requires discovery to run first.">
                {recsState.running ? 'Running recommendations…' : 'Run recommendations'}
              </Button>
              <span className="statements-step-desc">Find missing periods</span>
            </div>
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
        <StatCard title="Discovered providers" metric={coverageError ? '—' : providers.length} desc={coverageError ? 'Data unavailable' : `From ${analyzedDocs} analyzed documents`} />
        <StatCard title="Missing series" metric={rows.length} desc="Providers with an outstanding expected statement" />
        <StatCard title="Critical overdue" metric={summary.severe} desc="45+ days overdue" status={summary.severe > 0 ? { label: 'Needs review', tone: 'warn' } : { label: 'Stable', tone: 'ok' }} />
        <StatCard title="Longest overdue" metric={`${summary.maxOverdue}d`} desc="Worst-case delay across all missing statements" />
      </StatGrid>

      {coverageError && (
        <div className="statements-coverage-unavailable">
          <span className="statements-coverage-unavailable-icon">⚠️</span>
          <span className="statements-coverage-unavailable-text">Coverage data unavailable — {coverageError}</span>
          <Button size="sm" onClick={() => void loadStatements()}>Retry</Button>
        </div>
      )}

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
              <EmptyState icon="🔍" title="No providers discovered" desc="Run discovery to scan your documents and detect recurring statement providers." action="Run discovery" onAction={runDiscovery} />
            ) : null}
            {!loading && !error && providers.length > 0 ? (
              <div className="statements-table-wrapper">
                <ConfidenceLegend />
                <SortableTable
                  data={providers}
                  columns={providerColumns}
                  rowKey={(provider) => provider.provider_key}
                  sorting={providerSorting}
                  onSortingChange={setProviderSorting}
                  columnFilters={providerFilters}
                  onColumnFiltersChange={setProviderFilters}
                  rowClassName="statements-row"
                  onRowActivate={(provider) => navigate(`/statements/${encodeURIComponent(provider.provider_key)}`)}
                />
              </div>
            ) : null}
          </Card>
        )}

        {activeTab === 'missing' && (
          <Card title="Missing statements" actions={<span className="text-muted">Click a row to open the series detail view.</span>}>
            {loading ? <SkeletonLoader variant="table" /> : null}
            {!loading && error ? <ErrorState message={error} onRetry={() => void loadStatements()} /> : null}
            {!loading && !error && rows.length === 0 ? (
              <EmptyState icon="✅" title="No missing statements" desc="Run recommendations to refresh expected periods if you recently ingested new statements." action="Run recommendations" onAction={runRecommendations} />
            ) : null}
            {!loading && !error && rows.length > 0 ? (
              <div className="statements-table-wrapper">
                <SortableTable
                  data={rows}
                  columns={missingColumns}
                  rowKey={(row) => `${buildSeriesId(row)}-${row.expected_period ?? 'period'}`}
                  sorting={missingSorting}
                  onSortingChange={setMissingSorting}
                  columnFilters={missingFilters}
                  onColumnFiltersChange={setMissingFilters}
                  rowClassName="statements-row"
                  onRowActivate={(row) => navigate(`/statements/${encodeURIComponent(buildSeriesId(row))}`)}
                />
              </div>
            ) : null}
          </Card>
        )}
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </>
  );
}
