import { useCallback, useEffect, useMemo, useState } from 'react';
import { Badge, ErrorState, PageHeader, SkeletonLoader, StatCard, StatGrid, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/insights.css';

/* ── Types (exported for testing) ── */

export type ToastState = { message: string; tone: 'success' | 'error' };

export type InsightItem = {
  id: string | number;
  insight_type?: string | null;
  severity?: string | null;
  status?: string | null;
  rule_id?: string | null;
  series_id?: string | null;
  title?: string | null;
  description?: string | null;
  generated_at?: string | null;
  acknowledged_at?: string | null;
  archived_at?: string | null;
  evidence?: InsightEvidence | null;
  metadata?: Record<string, unknown> | null;
};

export type InsightEvidence = {
  current_value?: number | null;
  previous_value?: number | null;
  average_value?: number | null;
  change_pct?: number | null;
  change_amount?: number | null;
  history?: HistoryPoint[];
  categories?: MoMCategory[];
  compliance_items?: ComplianceItem[];
  highlights?: HighlightItem[];
  trend_description?: string | null;
};

export type HistoryPoint = { label: string; value: number; is_current?: boolean };
export type MoMCategory = { name: string; previous: number; current: number; change: number };
export type ComplianceItem = { name: string; status: string; detail?: string };
export type HighlightItem = { text: string; tone?: string; value?: string };

export type InsightSummary = {
  total?: number;
  by_type?: Record<string, number>;
  by_severity?: Record<string, number>;
  new_count?: number;
};

export type InsightsListResponse = {
  insights?: InsightItem[];
  total?: number;
};

/* Fallback types for alerts API */
export type AlertItem = {
  id: number | string;
  alert_type?: string | null;
  severity?: string | null;
  module?: string | null;
  title?: string | null;
  description?: string | null;
  created_at?: string | null;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
  metadata?: Record<string, unknown> | null;
};

export type AlertSummary = {
  total?: number;
  unacknowledged?: number;
  by_severity?: Record<string, number>;
  by_module?: Record<string, number>;
};

/* ── Helpers (exported for testing) ── */

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

export function formatDate(value?: string | null) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function formatCurrency(value?: number | null) {
  if (value == null) return '—';
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

export function changePctLabel(pct?: number | null): { text: string; tone: 'up' | 'down' | 'flat' } {
  if (pct == null || Math.abs(pct) < 1) return { text: '— 0%', tone: 'flat' };
  const arrow = pct > 0 ? '▲' : '▼';
  const sign = pct > 0 ? '+' : '';
  return { text: `${arrow} ${sign}${Math.round(pct)}%`, tone: pct > 0 ? 'up' : 'down' };
}

export function insightIcon(type?: string | null): string {
  switch (type) {
    case 'spend_summary': case 'anomaly': return '📊';
    case 'trend': return '📈';
    case 'new_category': return '🆕';
    case 'compliance': return '✅';
    default: return '💡';
  }
}

export function insightTypeTab(type?: string | null): string {
  switch (type) {
    case 'spend_summary': case 'anomaly': case 'new_category': return 'anomalies';
    case 'trend': return 'trends';
    case 'compliance': return 'compliance';
    default: return 'anomalies';
  }
}

/** Convert an alert to an insight shape for the fallback path */
export function alertToInsight(alert: AlertItem): InsightItem {
  return {
    id: alert.id,
    insight_type: alert.alert_type ?? alert.module ?? 'alert',
    severity: alert.severity,
    status: alert.resolved_at ? 'archived' : alert.acknowledged_at ? 'viewed' : 'new',
    title: alert.title,
    description: alert.description,
    generated_at: alert.created_at,
    acknowledged_at: alert.acknowledged_at,
    archived_at: alert.resolved_at,
    metadata: alert.metadata,
    evidence: null,
  };
}

/* ── Tab definitions ── */
const TAB_KEYS = ['all', 'anomalies', 'trends', 'compliance'] as const;
type TabKey = (typeof TAB_KEYS)[number];

const TIME_RANGES = [
  { key: '3m', label: 'Last 3 months' },
  { key: '6m', label: 'Last 6 months' },
  { key: '12m', label: 'Last 12 months' },
  { key: 'all', label: 'All time' },
];

/* ── Sub-components ── */

function MiniBarChart({ history }: { history: HistoryPoint[] }) {
  if (!history || history.length === 0) return null;
  const max = Math.max(...history.map((h) => h.value), 1);
  return (
    <div className="chart-container">
      <div className="chart-bars">
        {history.map((point, i) => {
          const pct = Math.round((point.value / max) * 100);
          const isLast = i === history.length - 1;
          const isCurrent = point.is_current || isLast;
          const isHigh = pct > 85;
          const cls = isCurrent ? (isHigh ? 'spike' : 'current') : (isHigh ? 'high' : 'normal');
          return (
            <div key={i} className="chart-bar-wrap">
              <div className={`chart-bar ${cls}`} style={{ height: `${Math.max(pct, 5)}%` }} />
              <span className="chart-label">{point.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Sparkline({ history }: { history: HistoryPoint[] }) {
  if (!history || history.length === 0) return null;
  const max = Math.max(...history.map((h) => h.value), 1);
  return (
    <div className="trend-spark">
      {history.map((point, i) => {
        const h = Math.max(Math.round((point.value / max) * 24), 4);
        const isLast = i === history.length - 1;
        const isCurrent = point.is_current || isLast;
        const cls = isCurrent ? 'current' : (h > 20 ? 'high' : '');
        return <div key={i} className={`trend-spark-bar ${cls}`} style={{ height: `${h}px` }} />;
      })}
    </div>
  );
}

function MoMTable({ categories }: { categories: MoMCategory[] }) {
  if (!categories || categories.length === 0) return null;
  return (
    <table className="mom-table">
      <thead>
        <tr>
          <th>Category</th>
          <th className="amount">Previous</th>
          <th className="amount">Current</th>
          <th className="change">Change</th>
        </tr>
      </thead>
      <tbody>
        {categories.map((cat, i) => {
          const isHighlight = Math.abs(cat.change) > 100;
          const changeTone = cat.change > 10 ? 'up' : cat.change < -10 ? 'down' : 'flat';
          return (
            <tr key={i} className={isHighlight ? 'row-highlight' : ''}>
              <td>{cat.name}</td>
              <td className="amount">{formatCurrency(cat.previous)}</td>
              <td className="amount">{formatCurrency(cat.current)}</td>
              <td className={`change ${changeTone}`}>
                {cat.change > 0 ? '+' : ''}{formatCurrency(cat.change)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ComplianceRows({ items }: { items: ComplianceItem[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      {items.map((item, i) => (
        <div key={i} className="compliance-row">
          <span className="compliance-icon">{item.status === 'ok' ? '✅' : item.status === 'late' ? '⚠️' : '❌'}</span>
          <span className="compliance-text">{item.name}</span>
          {item.detail && <span className="compliance-detail">{item.detail}</span>}
        </div>
      ))}
    </div>
  );
}

function Highlights({ items }: { items: HighlightItem[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="highlights">
      {items.map((item, i) => (
        <div key={i} className="highlight-item">
          <span className={`highlight-dot ${['up', 'down', 'new', 'neutral'].includes(item.tone ?? '') ? item.tone : 'neutral'}`} />
          <span className="highlight-text">{item.text}</span>
          {item.value && <span className="highlight-value">{item.value}</span>}
        </div>
      ))}
    </div>
  );
}

function InsightCardComponent({
  insight,
  onAcknowledge,
  onArchive,
  busyAction,
}: {
  insight: InsightItem;
  onAcknowledge: (id: string | number) => void;
  onArchive: (id: string | number) => void;
  busyAction: string | null;
}) {
  const ev = insight.evidence;
  const isNew = insight.status === 'new' || (!insight.acknowledged_at && !insight.archived_at);
  const isArchived = insight.status === 'archived' || Boolean(insight.archived_at);
  const badgeClass = isNew ? 'new' : isArchived ? 'archived' : 'viewed';
  const badgeLabel = isNew
    ? (ev?.change_pct != null && Math.abs(ev.change_pct) >= 10 ? changePctLabel(ev.change_pct).text : 'New')
    : isArchived ? 'Archived' : 'Viewed';

  return (
    <div className={`insight-card ${isNew ? 'new' : ''}`}>
      {/* Header */}
      <div className="insight-header">
        <span className="insight-icon">{insightIcon(insight.insight_type)}</span>
        <div className="insight-title-area">
          <div className="insight-title">{insight.title ?? 'Untitled insight'}</div>
          <div className="insight-subtitle">
            {insight.rule_id && <>Rule: {insight.rule_id} · </>}
            Generated {formatDate(insight.generated_at)}
          </div>
        </div>
        <span className={`insight-badge ${isNew && ev?.change_pct != null && Math.abs(ev.change_pct) >= 10 ? 'warning' : badgeClass}`}>
          {badgeLabel}
        </span>
      </div>

      {/* Metrics row */}
      {ev && (ev.current_value != null || ev.previous_value != null) && (
        <div className="metrics-row">
          {ev.current_value != null && (
            <div>
              <div className="metric-label">Current</div>
              <div className={`metric-value ${ev.change_pct != null && ev.change_pct > 10 ? 'up' : ev.change_pct != null && ev.change_pct < -10 ? 'down' : 'neutral'}`}>
                {formatCurrency(ev.current_value)}
              </div>
            </div>
          )}
          {ev.previous_value != null && (
            <div>
              <div className="metric-label">Previous</div>
              <div className="metric-value neutral">{formatCurrency(ev.previous_value)}</div>
            </div>
          )}
          {ev.average_value != null && (
            <div>
              <div className="metric-label">Average</div>
              <div className="metric-value neutral">{formatCurrency(ev.average_value)}</div>
            </div>
          )}
          {ev.change_pct != null && (
            <div>
              <div className="metric-label">Change</div>
              <div className={`metric-change ${changePctLabel(ev.change_pct).tone}`}>
                {changePctLabel(ev.change_pct).text}
                {ev.change_amount != null && ` (${ev.change_amount > 0 ? '+' : ''}${formatCurrency(ev.change_amount)})`}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Mini bar chart */}
      {ev?.history && ev.history.length > 2 && insight.insight_type !== 'trend' && (
        <MiniBarChart history={ev.history} />
      )}

      {/* Sparkline for trend type */}
      {ev?.history && ev.history.length > 0 && insight.insight_type === 'trend' && (
        <div className="trend-row">
          <Sparkline history={ev.history} />
          {ev.trend_description && <div className="trend-info">{ev.trend_description}</div>}
        </div>
      )}

      {/* MoM comparison table */}
      {ev?.categories && <MoMTable categories={ev.categories} />}

      {/* Compliance rows */}
      {ev?.compliance_items && <ComplianceRows items={ev.compliance_items} />}

      {/* Highlights */}
      {ev?.highlights && <Highlights items={ev.highlights} />}

      {/* Description fallback when no structured evidence */}
      {!ev && insight.description && (
        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: 14 }}>
          {insight.description}
        </div>
      )}

      {/* Actions */}
      <div className="insight-actions">
        {insight.series_id && (
          <a href={`#/statements/${insight.series_id}`} className="insight-btn primary">
            View Statement →
          </a>
        )}
        {!insight.acknowledged_at && !isArchived && (
          <button
            className="insight-btn"
            onClick={() => onAcknowledge(insight.id)}
            disabled={busyAction !== null}
          >
            {busyAction === `ack:${insight.id}` ? 'Saving…' : 'Acknowledge'}
          </button>
        )}
        {!isArchived && (
          <button
            className="insight-btn"
            onClick={() => onArchive(insight.id)}
            disabled={busyAction !== null}
          >
            {busyAction === `archive:${insight.id}` ? 'Saving…' : 'Archive'}
          </button>
        )}
      </div>
    </div>
  );
}

/* ── Main page ── */

export default function Insights() {
  const [insights, setInsights] = useState<InsightItem[]>([]);
  const [summary, setSummary] = useState<InsightSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [usingFallback, setUsingFallback] = useState(false);

  // Filters
  const [activeTab, setActiveTab] = useState<TabKey>('all');
  const [timeRange, setTimeRange] = useState('6m');
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Try the new insights API first
      const params = new URLSearchParams({ limit: '100' });
      if (timeRange === '3m') {
        const d = new Date(); d.setMonth(d.getMonth() - 3);
        params.set('date_from', d.toISOString().split('T')[0]);
      } else if (timeRange === '6m') {
        const d = new Date(); d.setMonth(d.getMonth() - 6);
        params.set('date_from', d.toISOString().split('T')[0]);
      } else if (timeRange === '12m') {
        const d = new Date(); d.setFullYear(d.getFullYear() - 1);
        params.set('date_from', d.toISOString().split('T')[0]);
      }

      try {
        const [summaryResp, listResp] = await Promise.all([
          endpoints.insights.summary() as Promise<InsightSummary>,
          endpoints.insights.list(params.toString()) as Promise<InsightsListResponse>,
        ]);
        setSummary(summaryResp);
        setInsights(Array.isArray(listResp.insights) ? listResp.insights : []);
        setUsingFallback(false);
      } catch {
        // Fallback to alerts API
        const alertParams = new URLSearchParams({ limit: '100', resolved: 'false' });
        if (params.has('date_from')) alertParams.set('date_from', params.get('date_from')!);
        const [alertSummary, alertList] = await Promise.all([
          endpoints.alerts.summary() as Promise<AlertSummary>,
          endpoints.alerts.list(alertParams.toString()) as Promise<{ alerts?: AlertItem[]; total?: number }>,
        ]);
        const fallbackSummary: InsightSummary = {
          total: alertSummary.total ?? 0,
          new_count: alertSummary.unacknowledged ?? 0,
          by_severity: alertSummary.by_severity,
          by_type: alertSummary.by_module,
        };
        setSummary(fallbackSummary);
        setInsights((alertList.alerts ?? []).map(alertToInsight));
        setUsingFallback(true);
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [timeRange]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // Client-side filtering
  const filtered = useMemo(() => {
    let items = insights;
    if (activeTab !== 'all') {
      items = items.filter((ins) => insightTypeTab(ins.insight_type) === activeTab);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      items = items.filter(
        (ins) =>
          (ins.title ?? '').toLowerCase().includes(q) ||
          (ins.description ?? '').toLowerCase().includes(q) ||
          (ins.rule_id ?? '').toLowerCase().includes(q)
      );
    }
    return items;
  }, [insights, activeTab, search]);

  // Group by status
  const groups = useMemo(() => {
    const newItems: InsightItem[] = [];
    const viewedItems: InsightItem[] = [];
    const archivedItems: InsightItem[] = [];
    for (const ins of filtered) {
      if (ins.status === 'archived' || ins.archived_at) archivedItems.push(ins);
      else if (ins.status === 'new' || (!ins.acknowledged_at && !ins.archived_at)) newItems.push(ins);
      else viewedItems.push(ins);
    }
    return { newItems, viewedItems, archivedItems };
  }, [filtered]);

  // Tab counts
  const tabCounts = useMemo(() => {
    const counts: Record<TabKey, number> = { all: insights.length, anomalies: 0, trends: 0, compliance: 0 };
    for (const ins of insights) {
      const tab = insightTypeTab(ins.insight_type);
      if (tab in counts) counts[tab as TabKey]++;
    }
    return counts;
  }, [insights]);

  // Summary stats
  const newCount = summary?.new_count ?? groups.newItems.length;
  const totalCount = summary?.total ?? insights.length;
  const criticalCount = (summary?.by_severity?.critical ?? 0) + (summary?.by_severity?.high ?? 0);

  const topType = useMemo(() => {
    const entries = Object.entries(summary?.by_type ?? {});
    if (entries.length === 0) return '—';
    const [name, count] = entries.sort((a, b) => b[1] - a[1])[0];
    return `${name.replace('_', ' ')} (${count})`;
  }, [summary]);

  const handleAcknowledge = async (id: string | number) => {
    setBusyAction(`ack:${id}`);
    try {
      if (usingFallback) {
        await endpoints.alerts.acknowledge(String(id));
      } else {
        await endpoints.insights.acknowledge(String(id));
      }
      await loadData();
      setToast({ message: 'Insight acknowledged.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  const handleArchive = async (id: string | number) => {
    setBusyAction(`archive:${id}`);
    try {
      if (usingFallback) {
        await endpoints.alerts.resolve(String(id));
      } else {
        await endpoints.insights.archive(String(id));
      }
      await loadData();
      setToast({ message: 'Insight archived.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Insights"
        desc="Trend charts, anomaly detection, and compliance tracking across all statement series."
        actions={
          <div className="btn-group">
            <button className="btn" onClick={() => void loadData()} disabled={loading}>
              Refresh
            </button>
          </div>
        }
      />

      {toast && <Toast message={toast.message} tone={toast.tone} />}

      {loading ? (
        <>
          <SkeletonLoader variant="stat-grid" />
          <div className="section"><SkeletonLoader variant="cards" /></div>
        </>
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <>
          {/* Stat cards */}
          <StatGrid>
            <StatCard
              title="Total insights"
              metric={totalCount}
              desc="Insights generated across all rules and series."
            />
            <StatCard
              title="New"
              metric={newCount}
              desc="Unreviewed insights awaiting triage."
              status={newCount > 0 ? { label: 'Attention', tone: 'warning' } : { label: 'Clear', tone: 'success' }}
            />
            <StatCard
              title="Critical + High"
              metric={criticalCount}
              desc="Highest-severity findings currently active."
              status={criticalCount > 0 ? { label: 'Escalated', tone: 'danger' } : { label: 'Stable', tone: 'success' }}
            />
            <StatCard
              title="Top category"
              metric={topType}
              desc="Most frequent insight type."
            />
          </StatGrid>

          {/* Filter bar */}
          <div className="insights-filters" style={{ marginTop: 20 }}>
            <div className="insights-filter-tabs">
              {TAB_KEYS.map((key) => (
                <button
                  key={key}
                  className={`insights-filter-tab ${activeTab === key ? 'active' : ''}`}
                  onClick={() => setActiveTab(key)}
                >
                  {key === 'all' ? 'All' : key.charAt(0).toUpperCase() + key.slice(1)}
                  {' '}({tabCounts[key]})
                </button>
              ))}
            </div>
            <div className="insights-filter-sep" />
            <select
              className="insights-filter-select"
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value)}
            >
              {TIME_RANGES.map((r) => (
                <option key={r.key} value={r.key}>{r.label}</option>
              ))}
            </select>
            <input
              type="text"
              className="insights-filter-search"
              placeholder="🔍 Search insights..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {/* Fallback notice */}
          {usingFallback && (
            <div style={{ marginBottom: 12 }}>
              <Badge tone="info">Using alert feed — full insights API not yet available</Badge>
            </div>
          )}

          {/* Insight cards grouped by status */}
          {filtered.length === 0 ? (
            <div className="insight-card" style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
              No insights match the current filters.
            </div>
          ) : (
            <>
              {groups.newItems.length > 0 && (
                <>
                  <div className="insights-section-divider">New ({groups.newItems.length})</div>
                  {groups.newItems.map((ins) => (
                    <InsightCardComponent
                      key={ins.id}
                      insight={ins}
                      onAcknowledge={handleAcknowledge}
                      onArchive={handleArchive}
                      busyAction={busyAction}
                    />
                  ))}
                </>
              )}
              {groups.viewedItems.length > 0 && (
                <>
                  <div className="insights-section-divider">Viewed ({groups.viewedItems.length})</div>
                  {groups.viewedItems.map((ins) => (
                    <InsightCardComponent
                      key={ins.id}
                      insight={ins}
                      onAcknowledge={handleAcknowledge}
                      onArchive={handleArchive}
                      busyAction={busyAction}
                    />
                  ))}
                </>
              )}
              {groups.archivedItems.length > 0 && (
                <>
                  <div className="insights-section-divider">Archived ({groups.archivedItems.length})</div>
                  {groups.archivedItems.map((ins) => (
                    <InsightCardComponent
                      key={ins.id}
                      insight={ins}
                      onAcknowledge={handleAcknowledge}
                      onArchive={handleArchive}
                      busyAction={busyAction}
                    />
                  ))}
                </>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
