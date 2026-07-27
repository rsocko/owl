import { Fragment, useCallback, useEffect, useState } from 'react';
import { Badge, Button, EmptyState, ErrorState, PageHeader, SkeletonLoader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/benchmark.css';

/* ── Types ── */

interface BenchmarkModelSummary {
  model: string;
  success_rate: number | null;
  avg_confidence: number | null;
  avg_time_seconds: number | null;
  estimated_cost_usd: number | null;
}

interface BenchmarkRunSummary {
  id: number;
  started_at: string | null;
  finished_at: string | null;
  documents_tested: number | null;
  models_tested: number | null;
  trigger: string | null;
  status: string | null;
  models: BenchmarkModelSummary[];
}

interface TrendRunEntry {
  run_id: number;
  date: string | null;
  trigger: string | null;
  models: Record<string, {
    success_rate: number | null;
    avg_confidence: number | null;
    avg_time_seconds: number | null;
    estimated_cost_usd: number | null;
  }>;
}

type ToastState = { message: string; tone: 'success' | 'error' };

/* ── Helpers ── */

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function pct(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function seconds(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v.toFixed(2)}s`;
}

function cost(v: number | null | undefined): string {
  if (v == null) return '—';
  return `$${v.toFixed(4)}`;
}

function hasRegression(models: BenchmarkModelSummary[]): boolean {
  return models.some((m) => m.success_rate != null && m.success_rate < 0.7);
}

/* ── Mini bar chart (CSS-only, same pattern as Insights) ── */

function TrendBarChart({ values, isLow }: { values: number[]; isLow?: (v: number) => boolean }) {
  if (values.length === 0) return null;
  const max = Math.max(...values, 0.01);
  return (
    <div className="benchmark-bar-chart">
      {values.map((v, i) => {
        const heightPct = Math.max(Math.round((v / max) * 100), 4);
        const cls = isLow && isLow(v) ? 'bar low' : i === values.length - 1 ? 'bar latest' : 'bar';
        return <div key={i} className="bar-wrap"><div className={cls} style={{ height: `${heightPct}%` }} /></div>;
      })}
    </div>
  );
}

/* ── Page ── */

export default function BenchmarkHistory() {
  const [runs, setRuns] = useState<BenchmarkRunSummary[]>([]);
  const [trendModels, setTrendModels] = useState<string[]>([]);
  const [trendRuns, setTrendRuns] = useState<TrendRunEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [historyRes, trendsRes] = await Promise.all([
        endpoints.eob.benchmarkHistory() as Promise<{ runs: BenchmarkRunSummary[] }>,
        endpoints.eob.benchmarkTrends() as Promise<{ models: string[]; runs: TrendRunEntry[] }>,
      ]);
      setRuns(historyRes.runs ?? []);
      setTrendModels(trendsRes.models ?? []);
      setTrendRuns(trendsRes.runs ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load benchmark data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRunBenchmark = useCallback(async () => {
    setTriggering(true);
    try {
      await endpoints.eob.benchmark();
      setToast({ message: 'Benchmark started — results will appear shortly.', tone: 'success' });
      // Reload after a short delay to pick up the new run
      setTimeout(() => { load(); }, 2000);
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to trigger benchmark', tone: 'error' });
    } finally {
      setTriggering(false);
    }
  }, [load]);

  if (loading) return <div className="benchmark-page"><SkeletonLoader variant="table" rows={6} /></div>;
  if (error) return <div className="benchmark-page"><ErrorState message={error} onRetry={load} /></div>;

  return (
    <div className="benchmark-page">
      <PageHeader
        title="Benchmark History"
        desc="EOB matching model benchmarks — track accuracy and performance over time."
        actions={
          <div className="benchmark-toolbar">
            <Button variant="primary" onClick={handleRunBenchmark} disabled={triggering}>
              {triggering ? 'Starting…' : '▶ Run Benchmark'}
            </Button>
          </div>
        }
      />

      {/* ── Run history table ── */}
      {runs.length === 0 ? (
        <EmptyState
          icon="📊"
          title="No benchmark runs yet"
          desc="Run your first benchmark to start tracking model performance."
          action="Run Benchmark"
          onAction={handleRunBenchmark}
        />
      ) : (
        <table className="benchmark-runs-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Models</th>
              <th>Docs</th>
              <th>Trigger</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const isExpanded = expandedId === run.id;
              return (
                <Fragment key={run.id}>
                  <tr
                    className={isExpanded ? 'expanded' : ''}
                    onClick={() => setExpandedId(isExpanded ? null : run.id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpandedId(isExpanded ? null : run.id); }}
                    aria-expanded={isExpanded}
                  >
                    <td>
                      {fmtDate(run.started_at)}
                      {run.started_at && <span style={{ opacity: 0.6, marginLeft: 6, fontSize: '0.82rem' }}>{fmtTime(run.started_at)}</span>}
                    </td>
                    <td>{run.models.map((m) => m.model).join(', ') || `${run.models_tested ?? 0} model(s)`}</td>
                    <td>{run.documents_tested ?? '—'}</td>
                    <td><span className={`trigger-badge ${run.trigger ?? ''}`}>{run.trigger ?? '—'}</span></td>
                    <td><span className={`status-badge ${run.status ?? ''}`}>{run.status ?? '—'}</span></td>
                    <td>
                      {hasRegression(run.models) && <span className="regression-indicator" title="Low success rate detected">⚠ Regression</span>}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="benchmark-detail-row">
                      <td colSpan={6}>
                        <div className="benchmark-detail-content">
                          {run.models.length === 0 ? (
                            <p style={{ color: 'var(--text-muted)' }}>No per-model results available.</p>
                          ) : (
                            <div className="benchmark-model-cards">
                              {run.models.map((m) => (
                                <div key={m.model} className="benchmark-model-card">
                                  <h4>{m.model}</h4>
                                  <dl>
                                    <dt>Success Rate</dt>
                                    <dd>
                                      {pct(m.success_rate)}
                                      {m.success_rate != null && m.success_rate < 0.7 && (
                                        <Badge tone="danger"> Low</Badge>
                                      )}
                                    </dd>
                                    <dt>Avg Confidence</dt>
                                    <dd>{pct(m.avg_confidence)}</dd>
                                    <dt>Avg Time</dt>
                                    <dd>{seconds(m.avg_time_seconds)}</dd>
                                    <dt>Est. Cost</dt>
                                    <dd>{cost(m.estimated_cost_usd)}</dd>
                                  </dl>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}

      {/* ── Trend charts ── */}
      {trendRuns.length > 1 && trendModels.length > 0 && (
        <div className="benchmark-trends">
          <h3>Model Trends</h3>
          {trendModels.map((model) => {
            const successRates = trendRuns.map((r) => r.models[model]?.success_rate ?? 0);
            const confidences = trendRuns.map((r) => r.models[model]?.avg_confidence ?? 0);
            return (
              <div key={model} className="trend-model-section">
                <h4>{model}</h4>
                <div className="trend-metric-row">
                  <span className="trend-metric-label">Success Rate</span>
                  <TrendBarChart values={successRates} isLow={(v) => v < 0.7} />
                </div>
                <div className="trend-metric-row">
                  <span className="trend-metric-label">Avg Confidence</span>
                  <TrendBarChart values={confidences} isLow={(v) => v < 0.5} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {toast && (
        <Toast
          message={toast.message}
          tone={toast.tone}
          duration={getToastDuration(toast.tone)}
          onDismiss={() => setToast(null)}
        />
      )}
    </div>
  );
}
