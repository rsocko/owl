import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, SkeletonLoader, StatCard, StatGrid, Toast } from '../components/ui';
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

export default function Statements() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<MissingStatement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<'discovery' | 'recommendations' | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);

  const loadStatements = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = (await endpoints.statements.missing()) as MissingStatement[];
      setRows(Array.isArray(response) ? response : []);
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

  const summary = useMemo(() => {
    const severe = rows.filter((row) => (row.days_overdue ?? 0) >= 45).length;
    const overdueDays = rows.map((row) => row.days_overdue ?? 0);
    const maxOverdue = overdueDays.length > 0 ? Math.max(...overdueDays) : 0;
    return { severe, maxOverdue };
  }, [rows]);

  const runAction = useCallback(
    async (action: 'discovery' | 'recommendations') => {
      try {
        setActionLoading(action);
        if (action === 'discovery') {
          await endpoints.statements.discoveryRun();
          setToast({ message: 'Statement discovery run completed.', tone: 'success' });
        } else {
          const todayIso = new Date().toISOString().slice(0, 10);
          await endpoints.statements.recommendationsRun(todayIso);
          setToast({ message: 'Statement recommendations run completed.', tone: 'success' });
        }
        await loadStatements();
      } catch (err) {
        setToast({ message: getErrorMessage(err), tone: 'error' });
      } finally {
        setActionLoading(null);
      }
    },
    [loadStatements],
  );

  return (
    <>
      <PageHeader
        title="Statements"
        desc="Track expected statement periods, trigger discovery, and review providers that still need documents."
        actions={
          <div className="btn-group">
            <Button variant="primary" onClick={() => void runAction('discovery')} disabled={actionLoading !== null}>
              {actionLoading === 'discovery' ? 'Running discovery…' : 'Run discovery'}
            </Button>
            <Button variant="success" onClick={() => void runAction('recommendations')} disabled={actionLoading !== null}>
              {actionLoading === 'recommendations' ? 'Running recommendations…' : 'Run recommendations'}
            </Button>
          </div>
        }
      />

      <StatGrid>
        <StatCard title="Missing series" metric={rows.length} desc="Providers with an outstanding expected statement" />
        <StatCard title="Critical overdue" metric={summary.severe} desc="45+ days overdue" status={summary.severe > 0 ? { label: 'Needs review', tone: 'warn' } : { label: 'Stable', tone: 'ok' }} />
        <StatCard title="Longest overdue" metric={`${summary.maxOverdue}d`} desc="Worst-case delay across all missing statements" />
      </StatGrid>

      <div className="section" style={{ marginTop: 18 }}>
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
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}
