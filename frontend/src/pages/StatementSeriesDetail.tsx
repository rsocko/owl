import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Badge, Breadcrumb, Button, Card, EmptyState, ErrorState, PageHeader, SkeletonLoader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/statement-series-detail.css';

interface MissingStatement {
  id?: string;
  correspondent?: string;
  expected_period?: string;
  frequency?: string;
  last_received_date?: string | null;
  days_overdue?: number;
}

interface ProviderOverride {
  provider_key?: string;
  status?: string;
  display_name?: string | null;
  frequency_override?: string | null;
  anchor_day_override?: number | null;
  notes?: string | null;
  updated_at?: string | null;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong while loading the statement series.';
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

function formatDateTime(value?: string | null): string {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function toMonthDate(period: string): Date | null {
  const parsed = new Date(`${period}-01T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function monthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function buildTimelinePeriods(periods: string[]): string[] {
  const dates = periods.map(toMonthDate).filter((date): date is Date => date !== null).sort((a, b) => a.getTime() - b.getTime());
  if (dates.length === 0) {
    const current = new Date();
    return [monthKey(new Date(current.getFullYear(), current.getMonth(), 1))];
  }

  const start = new Date(dates[0].getFullYear(), dates[0].getMonth() - 2, 1);
  const end = new Date(dates[dates.length - 1].getFullYear(), dates[dates.length - 1].getMonth() + 1, 1);
  const result: string[] = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    result.push(monthKey(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }
  return result;
}

function statusTone(status?: string): 'ok' | 'warning' | 'danger' | 'info' | 'muted' {
  switch (status) {
    case 'confirmed':
      return 'ok';
    case 'needs_review':
    case 'flagged':
      return 'warning';
    case 'rejected':
      return 'danger';
    case 'pending':
      return 'muted';
    default:
      return 'info';
  }
}

export default function StatementSeriesDetail() {
  const { seriesId } = useParams();
  const navigate = useNavigate();
  const decodedSeriesId = decodeURIComponent(seriesId ?? '');

  const [rows, setRows] = useState<MissingStatement[]>([]);
  const [overrides, setOverrides] = useState<Record<string, ProviderOverride>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);

  const [overrideStatus, setOverrideStatus] = useState('pending');
  const [displayName, setDisplayName] = useState('');
  const [frequencyOverride, setFrequencyOverride] = useState('');
  const [anchorDay, setAnchorDay] = useState('');
  const [notes, setNotes] = useState('');

  const loadDetail = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [missingResponse, overridesResponse] = (await Promise.all([
        endpoints.statements.missing(),
        endpoints.statements.providerOverrides(),
      ])) as [MissingStatement[], Record<string, ProviderOverride>];
      setRows(Array.isArray(missingResponse) ? missingResponse : []);
      setOverrides(overridesResponse ?? {});
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const seriesRows = useMemo(() => {
    const target = decodedSeriesId.toLowerCase();
    return rows.filter((row) => {
      const rowId = buildSeriesId(row).toLowerCase();
      const providerId = (row.id ?? '').toLowerCase();
      return rowId === target || providerId === target;
    });
  }, [decodedSeriesId, rows]);

  const seriesOverride = overrides[decodedSeriesId] ?? overrides[seriesRows[0]?.id ?? ''];
  const canonicalSeriesKey = seriesRows[0]?.id ?? decodedSeriesId;
  const seriesName = seriesOverride?.display_name || seriesRows[0]?.correspondent || decodedSeriesId || 'Statement series';

  useEffect(() => {
    setOverrideStatus(seriesOverride?.status ?? 'pending');
    setDisplayName(seriesOverride?.display_name ?? seriesRows[0]?.correspondent ?? '');
    setFrequencyOverride(seriesOverride?.frequency_override ?? seriesRows[0]?.frequency ?? '');
    setAnchorDay(seriesOverride?.anchor_day_override ? String(seriesOverride.anchor_day_override) : '');
    setNotes(seriesOverride?.notes ?? '');
  }, [seriesOverride, seriesRows]);

  const timelinePeriods = useMemo(() => buildTimelinePeriods(seriesRows.map((row) => row.expected_period).filter((value): value is string => Boolean(value))), [seriesRows]);
  const missingSet = useMemo(() => new Set(seriesRows.map((row) => row.expected_period).filter((value): value is string => Boolean(value))), [seriesRows]);

  const similarSeries = useMemo(() => {
    const seen = new Set<string>();
    return rows.filter((row) => {
      if (!row.correspondent || row.correspondent !== seriesRows[0]?.correspondent) return false;
      const rowId = buildSeriesId(row);
      if (rowId === decodedSeriesId || row.id === canonicalSeriesKey || seen.has(rowId)) return false;
      seen.add(rowId);
      return true;
    });
  }, [canonicalSeriesKey, decodedSeriesId, rows, seriesRows]);

  const statusBadge = useMemo(() => {
    if (seriesOverride?.status) {
      return { label: seriesOverride.status, tone: statusTone(seriesOverride.status) };
    }
    const worstOverdue = Math.max(...seriesRows.map((row) => row.days_overdue ?? 0), 0);
    if (worstOverdue >= 45) return { label: 'Needs review', tone: 'warning' as const };
    if (seriesRows.length > 0) return { label: 'Tracking', tone: 'info' as const };
    return { label: 'Unknown', tone: 'muted' as const };
  }, [seriesOverride?.status, seriesRows]);

  const summaryText = useMemo(() => {
    const worstOverdue = Math.max(...seriesRows.map((row) => row.days_overdue ?? 0), 0);
    if (seriesRows.length === 0) {
      return 'This route currently falls back to the missing-statements feed, and no matching series was found.';
    }
    if (seriesOverride?.notes) {
      return seriesOverride.notes;
    }
    return `${seriesName} has ${seriesRows.length} missing period${seriesRows.length === 1 ? '' : 's'} in the current recommendation snapshot, with the oldest item ${worstOverdue} days overdue.`;
  }, [seriesName, seriesOverride?.notes, seriesRows]);

  const handleSaveOverride = useCallback(async () => {
    try {
      setSaving(true);
      await endpoints.statements.setProviderOverride(canonicalSeriesKey, {
        status: overrideStatus,
        display_name: displayName || undefined,
        frequency_override: frequencyOverride || undefined,
        anchor_day_override: anchorDay ? Number(anchorDay) : undefined,
        notes: notes || undefined,
      });
      await loadDetail();
      setToast({ message: 'Provider override saved.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setSaving(false);
    }
  }, [anchorDay, canonicalSeriesKey, displayName, frequencyOverride, loadDetail, notes, overrideStatus]);

  const handleClearOverride = useCallback(async () => {
    try {
      setClearing(true);
      await endpoints.statements.clearProviderOverride(canonicalSeriesKey);
      await loadDetail();
      setToast({ message: 'Provider override cleared.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setClearing(false);
    }
  }, [canonicalSeriesKey, loadDetail]);

  return (
    <>
      <Breadcrumb
        items={[
          { label: 'Statements', to: '/statements' },
          { label: 'Series Detail' },
        ]}
      />
      <PageHeader
        title={`Statement series: ${seriesName}`}
        desc="Review the current missing-period snapshot for this provider and capture any provider-specific override details."
        actions={
          <div className="btn-group">
            <Button onClick={() => navigate('/statements')}>Back to Statements</Button>
          </div>
        }
      />

      {loading ? <SkeletonLoader variant="cards" /> : null}
      {!loading && error ? <ErrorState message={error} onRetry={() => void loadDetail()} /> : null}
      {!loading && !error ? (
        <>
          <div className="statement-series-banner section">
            <strong>Series summary:</strong> {summaryText}
          </div>

          <div className="statement-series-info section">
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Correspondent</div>
              <div className="statement-series-info-value">{seriesRows[0]?.correspondent ?? seriesName}</div>
            </div>
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Frequency</div>
              <div className="statement-series-info-value">{frequencyOverride || seriesRows[0]?.frequency || 'Unknown cadence'}</div>
            </div>
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Missing periods</div>
              <div className="statement-series-info-value">{seriesRows.length}</div>
            </div>
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Status</div>
              <div className="statement-series-info-value">
                <Badge tone={statusBadge.tone}>{statusBadge.label}</Badge>
              </div>
            </div>
          </div>

          <div className="statement-series-action-bar section">
            <span className="statement-series-action-label">Actions</span>
            <Button variant="primary" onClick={() => void handleSaveOverride()} disabled={saving || clearing || !canonicalSeriesKey}>
              {saving ? 'Saving…' : 'Save override'}
            </Button>
            <Button onClick={() => void handleClearOverride()} disabled={saving || clearing || !seriesOverride}>
              {clearing ? 'Clearing…' : 'Clear override'}
            </Button>
            <Button variant="success" onClick={() => navigate('/statements')}>Looks correct</Button>
          </div>

          <div className="statement-series-layout">
            <div>
              <Card title="Timeline" className="section">
                {/* TODO: Add a dedicated statement-series detail endpoint with received document history; this timeline is derived from /api/statements/missing only. */}
                <div className="statement-series-timeline-note">
                  Derived from the current missing-statements feed. A dedicated detail endpoint would allow received statements and document previews to appear here.
                </div>
                <div className="statement-series-timeline">
                  <div className="statement-series-track-row">
                    <div className="statement-series-track-label">Expected cadence</div>
                    <div className="statement-series-track-grid">
                      {timelinePeriods.map((period) => (
                        <div key={`expected-${period}`} className="statement-series-cell">
                          <div className="statement-series-cell-month">{formatExpectedPeriod(period)}</div>
                          <div className="statement-series-cell-detail">Expected</div>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="statement-series-track-row">
                    <div className="statement-series-track-label">Missing now</div>
                    <div className="statement-series-track-grid">
                      {timelinePeriods.map((period) => (
                        <div
                          key={`missing-${period}`}
                          className={`statement-series-cell ${missingSet.has(period) ? 'missing' : 'neutral'}`}
                        >
                          <div className="statement-series-cell-month">{formatExpectedPeriod(period)}</div>
                          <div className="statement-series-cell-detail">
                            {missingSet.has(period) ? 'Not received' : 'No issue in current snapshot'}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </Card>

              <Card title="Missing periods" className="section">
                {seriesRows.length === 0 ? (
                  <EmptyState title="Series not found in current snapshot" desc="Try rerunning statement recommendations, or add a dedicated series endpoint if you need historical detail beyond the missing-statements feed." />
                ) : (
                  <div className="statement-series-list">
                    {seriesRows.map((row) => (
                      <div key={`${buildSeriesId(row)}-${row.expected_period ?? 'period'}`} className="statement-series-list-item">
                        <div>
                          <div className="statement-series-list-title">{formatExpectedPeriod(row.expected_period)}</div>
                          <div className="statement-series-list-meta">{row.frequency ?? 'Unknown cadence'} · {row.days_overdue ?? 0} days overdue</div>
                        </div>
                        <div className="statement-series-list-side">
                          <Badge tone={(row.days_overdue ?? 0) >= 45 ? 'danger' : (row.days_overdue ?? 0) >= 14 ? 'warning' : 'ok'}>
                            {(row.days_overdue ?? 0) >= 45 ? 'Critical' : (row.days_overdue ?? 0) >= 14 ? 'High' : 'Watch'}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              <Card title="Related documents" className="section">
                <EmptyState
                  title="Related document previews are not exposed yet"
                  desc="The current statements API returns missing periods and provider overrides, but it does not expose the received document list for a single series."
                />
              </Card>
            </div>

            <div>
              <Card title="Provider override" className="section">
                <div className="form-group">
                  <label htmlFor="override-status">Status</label>
                  <select id="override-status" value={overrideStatus} onChange={(event) => setOverrideStatus(event.target.value)}>
                    <option value="pending">Pending</option>
                    <option value="confirmed">Confirmed</option>
                    <option value="needs_review">Needs review</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </div>
                <div className="form-group">
                  <label htmlFor="display-name">Display name</label>
                  <input id="display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Friendly series label" />
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="frequency-override">Frequency override</label>
                    <select id="frequency-override" value={frequencyOverride} onChange={(event) => setFrequencyOverride(event.target.value)}>
                      <option value="">None</option>
                      <option value="monthly">Monthly</option>
                      <option value="quarterly">Quarterly</option>
                      <option value="annual">Annual</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label htmlFor="anchor-day">Anchor day</label>
                    <input id="anchor-day" type="number" min="1" max="31" value={anchorDay} onChange={(event) => setAnchorDay(event.target.value)} placeholder="e.g. 15" />
                  </div>
                </div>
                <div className="form-group">
                  <label htmlFor="override-notes">Notes</label>
                  <textarea id="override-notes" rows={5} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Add context for future recommendation runs." />
                </div>
                <div className="statement-series-override-meta">Last updated: {formatDateTime(seriesOverride?.updated_at)}</div>
                <div className="btn-group" style={{ marginTop: 12 }}>
                  <Button variant="primary" onClick={() => void handleSaveOverride()} disabled={saving || clearing || !canonicalSeriesKey}>
                    {saving ? 'Saving…' : 'Save override'}
                  </Button>
                  <Button onClick={() => void handleClearOverride()} disabled={saving || clearing || !seriesOverride}>
                    {clearing ? 'Clearing…' : 'Clear override'}
                  </Button>
                </div>
              </Card>

              <Card title="Other series from this correspondent" className="section">
                {similarSeries.length === 0 ? (
                  <EmptyState title="No additional series in the current snapshot" desc="If multiple account variants exist for this correspondent, they will show up here when they also appear in the missing-statements feed." />
                ) : (
                  <div className="statement-series-list">
                    {similarSeries.map((row) => {
                      const rowId = buildSeriesId(row);
                      return (
                        <div key={rowId} className="statement-series-list-item">
                          <div>
                            <div className="statement-series-list-title">{row.correspondent ?? rowId}</div>
                            <div className="statement-series-list-meta">{formatExpectedPeriod(row.expected_period)} · {row.days_overdue ?? 0} days overdue</div>
                          </div>
                          <Link className="statement-series-link" to={`/statements/${encodeURIComponent(rowId)}`}>
                            Open series →
                          </Link>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            </div>
          </div>
        </>
      ) : null}

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </>
  );
}
