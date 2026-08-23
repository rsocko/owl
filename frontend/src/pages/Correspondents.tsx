import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, SkeletonLoader, StatCard, StatGrid, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import type { CorrespondentInventoryItem } from '../lib/correspondentPolicy';
import { getToastDuration } from '../lib/toast';
import '../styles/correspondents.css';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unable to load correspondent review inventory.';
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    orphaned_profile: 'Orphaned',
    unreviewed_profile: 'Unreviewed',
    suggested_expectations: 'Suggestions',
    stale_analysis: 'Stale analysis',
    statement_history: 'Statement history',
  };
  return labels[reason] ?? reason.replaceAll('_', ' ');
}

export default function Correspondents() {
  const [items, setItems] = useState<CorrespondentInventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'unreviewed' | 'orphaned' | 'stale'>('all');
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems((await endpoints.statements.correspondentProfiles.inventory()) as CorrespondentInventoryItem[]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const summary = useMemo(
    () => ({
      unreviewed: items.filter((item) => item.profile.review_status === 'unreviewed').length,
      orphaned: items.filter((item) => item.profile.lifecycle_status === 'orphaned').length,
      stale: items.filter((item) => item.analysis_stale).length,
      suggestions: items.reduce((total, item) => total + item.suggested_expectation_count, 0),
    }),
    [items],
  );

  const filtered = useMemo(
    () =>
      items.filter((item) => {
        if (filter === 'unreviewed') return item.profile.review_status === 'unreviewed';
        if (filter === 'orphaned') return item.profile.lifecycle_status === 'orphaned';
        if (filter === 'stale') return item.analysis_stale;
        return true;
      }),
    [filter, items],
  );

  const sync = async () => {
    setSyncing(true);
    try {
      const result = (await endpoints.statements.correspondentProfiles.sync()) as {
        created: number;
        updated: number;
        orphaned: number;
      };
      await load();
      setToast({
        message: `Paperless sync complete: ${result.created} added, ${result.updated} updated, ${result.orphaned} orphaned.`,
        tone: 'success',
      });
    } catch (err) {
      setToast({ message: errorMessage(err), tone: 'error' });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Correspondent review"
        desc="Prioritized Phase 1 review of Paperless correspondents and their independent document expectations."
        actions={
          <Button variant="primary" onClick={() => void sync()} disabled={syncing}>
            {syncing ? 'Syncing…' : 'Sync from Paperless'}
          </Button>
        }
      />

      <StatGrid>
        <StatCard title="Unreviewed" metric={summary.unreviewed} desc="Profiles awaiting a decision" />
        <StatCard title="Orphaned" metric={summary.orphaned} desc="Paperless identity no longer present" />
        <StatCard title="Stale analysis" metric={summary.stale} desc="Never analyzed or older than 30 days" />
        <StatCard title="Suggested policy" metric={summary.suggestions} desc="Persisted suggestions awaiting review" />
      </StatGrid>

      <div className="correspondent-filters section" role="group" aria-label="Filter correspondents">
        {(['all', 'unreviewed', 'orphaned', 'stale'] as const).map((value) => (
          <button
            key={value}
            type="button"
            className={`filter-pill ${filter === value ? 'active' : ''}`}
            onClick={() => setFilter(value)}
            aria-pressed={filter === value}
          >
            {value === 'all' ? 'All prioritized' : value[0].toUpperCase() + value.slice(1)}
          </button>
        ))}
      </div>

      <Card title={`Review inventory (${filtered.length})`}>
        {loading ? <SkeletonLoader variant="table" /> : null}
        {!loading && error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
        {!loading && !error && items.length === 0 ? (
          <EmptyState
            icon="📨"
            title="No correspondent profiles yet"
            desc="Synchronize Paperless correspondents to create the deployment-scoped review inventory."
            action="Sync from Paperless"
            onAction={() => void sync()}
          />
        ) : null}
        {!loading && !error && items.length > 0 && filtered.length === 0 ? (
          <EmptyState title="No correspondents match this filter" desc="Choose another review state." />
        ) : null}
        {!loading && !error && filtered.length > 0 ? (
          <div className="correspondent-table-wrap">
            <table className="data-table correspondent-table">
              <thead>
                <tr>
                  <th>Correspondent</th>
                  <th>Review signals</th>
                  <th>Documents</th>
                  <th>Series</th>
                  <th>Expectations</th>
                  <th>Last reviewed</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((item) => (
                  <tr key={item.profile.correspondent_id}>
                    <td>
                      <Link className="correspondent-name-link" to={`/correspondents/${item.profile.correspondent_id}`}>
                        {item.profile.current_name}
                      </Link>
                      <div className="text-muted">Paperless ID {item.profile.correspondent_id}</div>
                    </td>
                    <td>
                      <div className="correspondent-reasons">
                        {item.priority_reasons.map((reason) => (
                          <Badge
                            key={reason}
                            tone={reason === 'orphaned_profile' ? 'danger' : reason === 'unreviewed_profile' || reason === 'stale_analysis' ? 'warning' : 'info'}
                          >
                            {reasonLabel(reason)}
                          </Badge>
                        ))}
                        {item.priority_reasons.length === 0 ? <Badge tone="ok">Reviewed</Badge> : null}
                      </div>
                    </td>
                    <td>{item.profile.observed_summary.document_count || '—'}</td>
                    <td>{item.statement_series_count}</td>
                    <td>
                      {item.expectation_count}
                      {item.suggested_expectation_count > 0 ? (
                        <span className="text-muted"> ({item.suggested_expectation_count} suggested)</span>
                      ) : null}
                    </td>
                    <td>{item.profile.last_reviewed_at ? new Date(item.profile.last_reviewed_at).toLocaleDateString() : 'Never'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </>
  );
}
