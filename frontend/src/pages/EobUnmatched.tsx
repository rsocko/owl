import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonLoader,
  StatCard,
  StatGrid,
  Tabs,
  Toast,
  confidenceTone,
} from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import ConfirmModal from '../components/ConfirmModal';
import '../styles/eob-pages.css';

interface UnmatchedEobItem {
  id: string;
  provider?: string | null;
  amount?: number | null;
  date_of_service?: string | null;
  patient_responsibility?: number | null;
  document_url?: string | null;
  created_at?: string | null;
  doc_type?: string | null;
  orphaned?: boolean | null;
}

interface SuggestedMatch {
  id: number;
  eob_document_id?: number | null;
  bill_document_id?: number | null;
  score?: number | null;
  status?: string | null;
  eob_provider?: string | null;
  bill_provider?: string | null;
  eob_amount?: number | null;
  bill_amount?: number | null;
  eob_date?: string | null;
  bill_date?: string | null;
}

interface EobMatchesResponse {
  matches?: SuggestedMatch[];
}

type TabKey = 'all' | 'eobs' | 'bills' | 'orphaned';
type AgeFilter = 'any' | 'under7' | '7to30' | 'over30';
type SortKey = 'newest' | 'oldest' | 'amount-desc' | 'amount-asc';

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

function formatCurrency(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

function ageInDays(item: UnmatchedEobItem) {
  const source = item.created_at || item.date_of_service;
  if (!source) return null;
  const date = new Date(source);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / (1000 * 60 * 60 * 24)));
}

function ageTone(age: number | null): 'success' | 'warning' | 'danger' | 'muted' {
  if (age === null) return 'muted';
  if (age > 30) return 'danger';
  if (age > 14) return 'warning';
  return 'success';
}

function inferDocType(item: UnmatchedEobItem): 'eob' | 'bill' {
  if (item.doc_type) return item.doc_type.toLowerCase() === 'bill' ? 'bill' : 'eob';
  return item.patient_responsibility != null ? 'eob' : 'bill';
}

export interface EobUnmatchedProps {
  /** When true, skip page header (rendered by EobWorkspace). */
  embedded?: boolean;
  /** Navigate to match review in workspace context. */
  onNavigateMatch?: (matchId: number) => void;
}

export default function EobUnmatched({
  embedded = false,
  onNavigateMatch,
}: EobUnmatchedProps = {}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<UnmatchedEobItem[]>([]);
  const [activeTab, setActiveTab] = useState<TabKey>('all');
  const [ageFilter, setAgeFilter] = useState<AgeFilter>('any');
  const [sortKey, setSortKey] = useState<SortKey>('newest');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [suggestedMatches, setSuggestedMatches] = useState<SuggestedMatch[]>([]);
  const [toast, setToast] = useState<{ message: string; tone?: 'success' | 'error' } | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [pendingBulkAction, setPendingBulkAction] = useState<'mark_orphan' | null>(null);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [unmatchedResponse, matchesResponse] = await Promise.all([
        endpoints.eob.unmatched() as Promise<UnmatchedEobItem[]>,
        endpoints.eob.matches('status=candidate&limit=50') as Promise<EobMatchesResponse>,
      ]);
      setItems(Array.isArray(unmatchedResponse) ? unmatchedResponse : []);
      const allMatches = matchesResponse.matches ?? [];
      setSuggestedMatches(allMatches.filter((m) => (m.score ?? 0) > 0 && (m.score ?? 0) < 60));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load unmatched EOBs.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const filteredItems = useMemo(() => {
    let result = items;

    // Tab filter (sole document-type filter per UX-02)
    if (activeTab === 'eobs') result = result.filter((item) => inferDocType(item) === 'eob');
    else if (activeTab === 'bills') result = result.filter((item) => inferDocType(item) === 'bill');
    else if (activeTab === 'orphaned') result = result.filter((item) => item.orphaned === true);

    // Age dropdown filter
    if (ageFilter !== 'any') {
      result = result.filter((item) => {
        const age = ageInDays(item);
        if (age === null) return false;
        switch (ageFilter) {
          case 'under7': return age < 7;
          case '7to30': return age >= 7 && age <= 30;
          case 'over30': return age > 30;
          default: return true;
        }
      });
    }

    // Search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (item) =>
          (item.provider || '').toLowerCase().includes(q) ||
          item.id.toLowerCase().includes(q),
      );
    }

    // Sort
    result = [...result].sort((a, b) => {
      switch (sortKey) {
        case 'newest': {
          const da = new Date(a.created_at || a.date_of_service || 0).getTime();
          const db = new Date(b.created_at || b.date_of_service || 0).getTime();
          return db - da;
        }
        case 'oldest': {
          const da = new Date(a.created_at || a.date_of_service || 0).getTime();
          const db = new Date(b.created_at || b.date_of_service || 0).getTime();
          return da - db;
        }
        case 'amount-desc':
          return (b.amount ?? 0) - (a.amount ?? 0);
        case 'amount-asc':
          return (a.amount ?? 0) - (b.amount ?? 0);
        default:
          return 0;
      }
    });

    return result;
  }, [items, activeTab, ageFilter, sortKey, searchQuery]);

  const tabCounts = useMemo(() => ({
    all: items.length,
    eobs: items.filter((item) => inferDocType(item) === 'eob').length,
    bills: items.filter((item) => inferDocType(item) === 'bill').length,
    orphaned: items.filter((item) => item.orphaned === true).length,
  }), [items]);

  const oldestAge = useMemo(() => {
    const ages = items.map(ageInDays).filter((age): age is number => age !== null);
    return ages.length ? Math.max(...ages) : null;
  }, [items]);

  const totalPatientResponsibility = useMemo(
    () => items.reduce((sum, item) => sum + (item.patient_responsibility ?? 0), 0),
    [items],
  );

  // Clear stale selections when the visible set changes
  useEffect(() => {
    setSelectedIds((prev) => {
      const visibleIds = new Set(filteredItems.map((item) => item.id));
      const pruned = new Set([...prev].filter((id) => visibleIds.has(id)));
      return pruned.size === prev.size ? prev : pruned;
    });
  }, [filteredItems]);

  const toggleSelection = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === filteredItems.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredItems.map((item) => item.id)));
    }
  };

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const bulkUpdate = async (action: 'mark_orphan' | 'mark_paid') => {
    if (selectedIds.size === 0) {
      setToast({ message: 'Select at least one document to use bulk actions.', tone: 'error' });
      return;
    }
    const count = selectedIds.size;
    setBusyKey(`bulk-${action}`);
    try {
      await endpoints.eob.bulkUpdate({ ids: Array.from(selectedIds), action });
      setSelectedIds(new Set());
      await loadItems();
      const label = action === 'mark_orphan' ? 'orphan' : 'paid';
      setToast({ message: `${count} document${count === 1 ? '' : 's'} marked as ${label}.` });
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Bulk update failed.', tone: 'error' });
    } finally {
      setBusyKey(null);
    }
  };

  if (loading) {
    return (
      <>
        {!embedded && <PageHeader title="Unmatched Documents" desc="EOB and bill documents that do not yet have a confirmed match." />}
        <SkeletonLoader variant="table" rows={6} />
      </>
    );
  }

  if (error) {
    return (
      <>
        {!embedded && <PageHeader title="Unmatched Documents" desc="EOB and bill documents that do not yet have a confirmed match." />}
        <ErrorState message={error} onRetry={() => void loadItems()} />
      </>
    );
  }

  return (
    <>
      {!embedded && (
        <PageHeader
          title="Unmatched Documents"
          desc={
            <div className="eob-meta-row">
              <Link to="/eob" className="eob-link">
                ← Back to dashboard
              </Link>
              <span>These documents have no confirmed claim/bill link yet.</span>
            </div>
          }
          actions={
            <div className="btn-group">
              <Button onClick={() => void loadItems()}>Refresh</Button>
            </div>
          }
        />
      )}

      {embedded && (
        <div className="eob-meta-row" style={{ marginBottom: 8 }}>
          <span className="eob-table-secondary">These documents have no confirmed claim/bill link yet.</span>
          <div style={{ marginLeft: 'auto' }}>
            <Button onClick={() => void loadItems()}>Refresh</Button>
          </div>
        </div>
      )}

      <div className="eob-page-stack">
        <StatGrid>
          <StatCard
            title="Open unmatched"
            metric={items.length}
            desc="Documents without a confirmed pair"
            status={{ label: items.length ? 'Needs action' : 'Clear', tone: items.length ? 'warning' : 'success' }}
          />
          <StatCard
            title="Oldest age"
            metric={oldestAge !== null ? `${oldestAge}d` : '—'}
            desc="Days since created/service date"
            status={{ label: oldestAge !== null && oldestAge > 30 ? 'Escalate' : 'Normal', tone: oldestAge !== null && oldestAge > 30 ? 'danger' : 'muted' }}
          />
          <StatCard
            title="Patient responsibility"
            metric={formatCurrency(totalPatientResponsibility)}
            desc="Total responsibility represented by the unmatched queue"
          />
        </StatGrid>

        {/* Tabs — sole document-type filter (UX-02: removed overlapping type dropdown) */}
        <Tabs
          active={activeTab}
          onChange={(key) => setActiveTab(key as TabKey)}
          tabs={[
            { key: 'all', label: `All (${tabCounts.all})` },
            { key: 'eobs', label: `EOBs (${tabCounts.eobs})` },
            { key: 'bills', label: `Bills (${tabCounts.bills})` },
            { key: 'orphaned', label: `Orphaned (${tabCounts.orphaned})` },
          ]}
        />

        {/* Search & Filter Bar */}
        <Card title="Search & filters">
          <div className="eob-card-stack">
            <div className="eob-filter-bar">
              <input
                className="eob-search-input"
                type="text"
                placeholder="🔍  Search by provider, ID..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              <div className="eob-filter-group">
                <span className="eob-filter-label">Age:</span>
                <select className="eob-select" value={ageFilter} onChange={(e) => setAgeFilter(e.target.value as AgeFilter)}>
                  <option value="any">Any Age</option>
                  <option value="under7">Under 7 days</option>
                  <option value="7to30">7–30 days</option>
                  <option value="over30">Over 30 days</option>
                </select>
              </div>
              <div className="eob-filter-group">
                <span className="eob-filter-label">Sort:</span>
                <select className="eob-select" value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
                  <option value="newest">Newest First</option>
                  <option value="oldest">Oldest First</option>
                  <option value="amount-desc">Amount (High → Low)</option>
                  <option value="amount-asc">Amount (Low → High)</option>
                </select>
              </div>
            </div>

            {/* Active filter pills (UX-02) */}
            {(activeTab !== 'all' || ageFilter !== 'any' || searchQuery.trim() || sortKey !== 'newest') && (
              <div className="eob-active-filters">
                {activeTab !== 'all' && (
                  <span className="eob-filter-pill">
                    {activeTab === 'eobs' ? 'EOBs' : activeTab === 'bills' ? 'Bills' : 'Orphaned'}
                    <button className="eob-filter-pill-clear" onClick={() => setActiveTab('all')} aria-label="Clear tab filter">✕</button>
                  </span>
                )}
                {ageFilter !== 'any' && (
                  <span className="eob-filter-pill">
                    {ageFilter === 'under7' ? '< 7 days' : ageFilter === '7to30' ? '7–30 days' : '> 30 days'}
                    <button className="eob-filter-pill-clear" onClick={() => setAgeFilter('any')} aria-label="Clear age filter">✕</button>
                  </span>
                )}
                {searchQuery.trim() && (
                  <span className="eob-filter-pill">
                    Search: "{searchQuery.trim()}"
                    <button className="eob-filter-pill-clear" onClick={() => setSearchQuery('')} aria-label="Clear search">✕</button>
                  </span>
                )}
                {sortKey !== 'newest' && (
                  <span className="eob-filter-pill">
                    {sortKey === 'oldest' ? 'Oldest first' : sortKey === 'amount-desc' ? 'Amount ↓' : 'Amount ↑'}
                    <button className="eob-filter-pill-clear" onClick={() => setSortKey('newest')} aria-label="Clear sort">✕</button>
                  </span>
                )}
                <button
                  className="eob-filter-clear-all"
                  onClick={() => {
                    setActiveTab('all');
                    setAgeFilter('any');
                    setSearchQuery('');
                    setSortKey('newest');
                  }}
                >
                  Clear all filters
                </button>
              </div>
            )}
          </div>
        </Card>

        <Card title="Documents waiting for manual match">
          {filteredItems.length ? (
            <>
              <DataTable<UnmatchedEobItem>
                rowKey={(item) => item.id}
                rows={filteredItems}
                columns={[
                  {
                    key: 'select',
                    header: '',
                    width: '40px',
                    render: (item) => (
                      <input
                        type="checkbox"
                        checked={selectedIds.has(item.id)}
                        onChange={() => toggleSelection(item.id)}
                      />
                    ),
                  },
                  {
                    key: 'document',
                    header: 'Document',
                    render: (item) => (
                      <div className="eob-table-primary">
                        <strong>{item.provider || 'Unknown provider'}</strong>
                        <span className="eob-table-secondary">
                          {inferDocType(item) === 'eob' ? '📋' : '🧾'} {inferDocType(item).toUpperCase()} #{item.id}
                        </span>
                      </div>
                    ),
                  },
                  {
                    key: 'service-date',
                    header: 'Date of service',
                    render: (item) => formatDate(item.date_of_service),
                  },
                  {
                    key: 'amount',
                    header: 'Total billed',
                    render: (item) => formatCurrency(item.amount),
                  },
                  {
                    key: 'patient-resp',
                    header: 'Patient resp.',
                    render: (item) => formatCurrency(item.patient_responsibility),
                  },
                  {
                    key: 'age',
                    header: 'Age',
                    render: (item) => {
                      const age = ageInDays(item);
                      return <Badge tone={ageTone(age)}>{age !== null ? `${age} days` : 'Unknown'}</Badge>;
                    },
                  },
                  {
                    key: 'queued',
                    header: 'Queued',
                    render: (item) => formatDateTime(item.created_at),
                  },
                  {
                    key: 'actions',
                    header: '',
                    width: '220px',
                    render: (item) => {
                      const params = new URLSearchParams();
                      params.set('docId', item.id);
                      if (item.provider) params.set('provider', item.provider);
                      if (typeof item.patient_responsibility === 'number') {
                        params.set('patientResponsibility', String(item.patient_responsibility));
                      }
                      if (item.date_of_service) params.set('dateOfService', item.date_of_service);
                      return (
                        <div className="eob-actions-end">
                          <Link className="eob-link" to={`/triage/manual-search?${params.toString()}`}>
                            Find match →
                          </Link>
                          {item.document_url ? (
                            <a className="eob-link-secondary" href={item.document_url} target="_blank" rel="noreferrer">
                              Open source
                            </a>
                          ) : null}
                        </div>
                      );
                    },
                  },
                ]}
              />
              {/* Bulk action bar */}
              {selectedIds.size > 0 && (
                <div className="eob-bulk-bar eob-bulk-bar--floating">
                  <div className="eob-bulk-bar-left">
                    <input type="checkbox" checked={selectedIds.size === filteredItems.length && filteredItems.length > 0} onChange={toggleSelectAll} />
                    <span>{selectedIds.size} selected</span>
                  </div>
                  <div className="eob-bulk-bar-right">
                    <Button size="sm" variant="ghost" disabled={busyKey !== null} onClick={() => setPendingBulkAction('mark_orphan')}>
                      Mark as Orphan
                    </Button>
                    <Button size="sm" variant="ghost" disabled={busyKey !== null} onClick={() => void bulkUpdate('mark_paid')}>
                      Mark as Paid
                    </Button>
                    <Button size="sm" onClick={() => setSelectedIds(new Set())} disabled={busyKey !== null}>
                      Clear
                    </Button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <EmptyState
              title="No unmatched documents for this filter"
              desc="Try a different filter or refresh after the next pipeline run."
            />
          )}
        </Card>

        {/* Suggested Matches (Low Confidence) */}
        <Card title="🤖 Suggested Matches (Low Confidence)">
          {suggestedMatches.length ? (
            <div className="eob-card-stack">
              <div className="eob-field-note" style={{ marginBottom: 4 }}>
                The system found possible matches below the auto-match threshold. Review and confirm or dismiss.
              </div>
              {suggestedMatches.map((sm) => {
                const pct = Math.round(sm.score ?? 0);
                const tone = confidenceTone(pct);
                const barColor = tone === 'high' ? 'var(--success)' : tone === 'medium' ? 'var(--warning)' : 'var(--danger)';
                const diff = Math.abs((sm.eob_amount ?? sm.bill_amount ?? 0) - (sm.bill_amount ?? sm.eob_amount ?? 0));
                return (
                  <div key={sm.id} className="eob-suggested-row">
                    <div className="eob-suggested-pair">
                      <div>
                        <div className="eob-field-value">{sm.eob_provider || `EOB #${sm.eob_document_id ?? '—'}`}</div>
                        <div className="eob-field-note">
                          {formatDate(sm.eob_date)} · {formatCurrency(sm.eob_amount)}
                        </div>
                      </div>
                      <div>
                        <div className="eob-field-value">{sm.bill_provider || `Bill #${sm.bill_document_id ?? '—'}`}</div>
                        <div className="eob-field-note">
                          {formatDate(sm.bill_date)} · {formatCurrency(sm.bill_amount)}
                        </div>
                      </div>
                    </div>
                    <div className="eob-suggested-confidence">
                      <div className="eob-suggested-bar">
                        <div className="eob-suggested-bar-fill" style={{ width: `${pct}%`, background: barColor }} />
                      </div>
                      <span style={{ fontWeight: 700, fontSize: '0.82rem', color: barColor }}>{pct}%</span>
                    </div>
                    <div className="eob-suggested-reason">
                      {diff > 0 ? `Amount differs by ${formatCurrency(diff)}` : 'Dates / provider close'}
                    </div>
                    <div className="eob-suggested-actions">
                      {onNavigateMatch ? (
                        <Button size="sm" variant="ghost" onClick={() => onNavigateMatch(sm.id)}>Review →</Button>
                      ) : (
                        <Link to={`/eob/matches/${sm.id}`}>
                          <Button size="sm" variant="ghost">Review →</Button>
                        </Link>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState
              title="No suggested matches"
              desc="All low-confidence candidates have been reviewed or no suggestions are available."
            />
          )}
        </Card>

        <Card title="Follow-up guidance">
          <div className="eob-tip-box">
            <span className="eob-tip-icon">💡</span>
            <div>
              <strong>Tip:</strong> Older items usually mean either the bill has not been uploaded yet or the match signal is too weak
              for auto-linking. Start with aging or high-value EOBs first, then use manual search to inspect likely bill candidates.
            </div>
          </div>
        </Card>
      </div>

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}

      {/* Bulk orphan confirmation modal (destructive per UX-03) */}
      <ConfirmModal
        open={pendingBulkAction === 'mark_orphan'}
        title="Mark documents as orphan?"
        description={
          <>
            This will mark <strong>{selectedIds.size} document{selectedIds.size === 1 ? '' : 's'}</strong> as orphaned.
            Orphaned documents are removed from the matching queue and may require manual intervention to restore.
          </>
        }
        confirmLabel={`Mark ${selectedIds.size} as orphan`}
        confirmVariant="danger"
        busy={busyKey === 'bulk-mark_orphan'}
        onConfirm={() => {
          setPendingBulkAction(null);
          void bulkUpdate('mark_orphan');
        }}
        onCancel={() => setPendingBulkAction(null)}
      />
    </>
  );
}
