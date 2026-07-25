import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  ConfidenceBar,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonLoader,
  Tabs,
  Toast,
} from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/triage-queue.css';

interface MatchBreakdown {
  date?: number | null;
  provider?: number | null;
  patient?: number | null;
  amount?: number | null;
  procedures?: number | null;
}

interface MatchRecord {
  id: number;
  eob_document_id?: number | null;
  bill_document_id?: number | null;
  score?: number | null;
  confidence?: string | null;
  breakdown?: MatchBreakdown | null;
  status?: string | null;
  linked_in_paperless?: boolean;
  eob_preview_url?: string | null;
  bill_preview_url?: string | null;
  created_at?: string | null;
  confirmed_at?: string | null;
  flag_reason?: string | null;
}

interface MatchesResponse {
  matches?: MatchRecord[];
}

interface UnmatchedItem {
  id: string;
  provider?: string | null;
  amount?: number | null;
  date_of_service?: string | null;
  patient_responsibility?: number | null;
  document_url?: string | null;
  created_at?: string | null;
}

type QueueKind = 'eob' | 'grouping' | 'orphan';
type QueueFilter = 'all' | QueueKind;
type ToastState = { message: string; tone?: 'success' | 'error' } | null;

interface TriageItem {
  key: string;
  kind: QueueKind;
  title: string;
  reason: string;
  score?: number;
  priority: 'high' | 'medium' | 'low';
  createdAt?: string | null;
  match?: MatchRecord;
  orphan?: UnmatchedItem;
  relatedUnmatched?: UnmatchedItem[];
}

const priorityLabels: Record<'high' | 'medium' | 'low', string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
});

function formatCurrency(value?: number | null) {
  return typeof value === 'number' ? currencyFormatter.format(value) : '—';
}

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function timeAgo(value?: string | null) {
  if (!value) return 'Unknown age';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Math.max(0, Date.now() - date.getTime());
  const days = Math.floor(diff / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return '1d ago';
  return `${days}d ago`;
}

function valueToPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function factorLabel(label: string, value?: number | null) {
  const pct = valueToPercent(value);
  if (pct >= 90) return `${label} aligns strongly`;
  if (pct >= 70) return `${label} is close but worth a quick review`;
  return `${label} has a meaningful mismatch`;
}

function mismatchLabel(value?: number | null) {
  const pct = valueToPercent(value);
  if (pct >= 90) return 'Aligned';
  if (pct >= 70) return 'Close';
  return 'Mismatch';
}

function mismatchClass(value?: number | null) {
  const pct = valueToPercent(value);
  if (pct >= 90) return 'ok';
  if (pct >= 70) return 'warn';
  return 'danger';
}

function weakestBreakdown(breakdown?: MatchBreakdown | null) {
  const entries: Array<{ label: string; value: number }> = [
    { label: 'Date', value: valueToPercent(breakdown?.date) },
    { label: 'Provider', value: valueToPercent(breakdown?.provider) },
    { label: 'Patient', value: valueToPercent(breakdown?.patient) },
    { label: 'Amount', value: valueToPercent(breakdown?.amount) },
    { label: 'Procedures', value: valueToPercent(breakdown?.procedures) },
  ];

  return entries.sort((left, right) => left.value - right.value)[0];
}

function priorityForScore(score?: number | null): 'high' | 'medium' | 'low' {
  const safeScore = valueToPercent(score);
  if (safeScore < 70) return 'high';
  if (safeScore < 85) return 'medium';
  return 'low';
}

function itemBadgeTone(kind: QueueKind) {
  if (kind === 'eob') return 'info' as const;
  if (kind === 'grouping') return 'warning' as const;
  return 'danger' as const;
}

function itemBadgeLabel(kind: QueueKind) {
  if (kind === 'eob') return 'EOB';
  if (kind === 'grouping') return 'GROUPING';
  return 'ORPHAN';
}

export default function TriageQueue() {
  const navigate = useNavigate();
  const [matches, setMatches] = useState<MatchRecord[]>([]);
  const [unmatched, setUnmatched] = useState<UnmatchedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<QueueFilter>('all');
  const [collapsed, setCollapsed] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [toast, setToast] = useState<ToastState>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [matchesResponse, unmatchedResponse] = await Promise.all([
        endpoints.eob.matches('status=candidate&limit=200') as Promise<MatchesResponse>,
        endpoints.eob.unmatched() as Promise<UnmatchedItem[]>,
      ]);

      setMatches(matchesResponse?.matches ?? []);
      setUnmatched(unmatchedResponse ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load triage items.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const items = useMemo<TriageItem[]>(() => {
    const matchItems = [...matches]
      .sort((left, right) => valueToPercent(left.score) - valueToPercent(right.score))
      .map((match) => {
        const weakest = weakestBreakdown(match.breakdown);
        const score = valueToPercent(match.score);
        return {
          key: `match-${match.id}`,
          kind: 'eob' as const,
          title: `EOB #${match.eob_document_id ?? '—'} ↔ Bill #${match.bill_document_id ?? '—'}`,
          reason: match.flag_reason ?? `${weakest.label} factor needs review (${weakest.value}%).`,
          score,
          priority: priorityForScore(score),
          createdAt: match.created_at,
          match,
        };
      });

    const providerGroups = unmatched.reduce<Record<string, UnmatchedItem[]>>((acc, item) => {
      const key = (item.provider || 'Unknown provider').trim();
      acc[key] = [...(acc[key] ?? []), item];
      return acc;
    }, {});

    const groupingItems = Object.entries(providerGroups)
      .filter(([, docs]) => docs.length > 1)
      .map(([provider, docs]) => ({
        key: `group-${provider}`,
        kind: 'grouping' as const,
        title: `${provider} — ${docs.length} unmatched documents`,
        reason: 'Repeated unmatched documents suggest a grouping or series review issue.',
        priority: docs.length >= 3 ? ('high' as const) : ('medium' as const),
        createdAt: docs[0]?.created_at,
        relatedUnmatched: docs,
      }));

    const groupedIds = new Set(groupingItems.flatMap((item) => item.relatedUnmatched?.map((doc) => doc.id) ?? []));
    const orphanItems = unmatched
      .filter((item) => !groupedIds.has(item.id))
      .map((item) => ({
        key: `orphan-${item.id}`,
        kind: 'orphan' as const,
        title: `${item.provider || 'Unknown provider'} invoice — no confirmed link`,
        reason: 'No confirmed EOB match is currently available for this document.',
        priority: (item.amount ?? item.patient_responsibility ?? 0) > 100 ? ('high' as const) : ('medium' as const),
        createdAt: item.created_at,
        orphan: item,
      }));

    return [...matchItems, ...groupingItems, ...orphanItems];
  }, [matches, unmatched]);

  const counts = useMemo(
    () => ({
      all: items.length,
      eob: items.filter((item) => item.kind === 'eob').length,
      grouping: items.filter((item) => item.kind === 'grouping').length,
      orphan: items.filter((item) => item.kind === 'orphan').length,
    }),
    [items],
  );

  const filteredItems = useMemo(() => {
    return filter === 'all' ? items : items.filter((item) => item.kind === filter);
  }, [filter, items]);

  useEffect(() => {
    if (filteredItems.length === 0) {
      setSelectedKey(null);
      return;
    }

    if (!filteredItems.some((item) => item.key === selectedKey)) {
      setSelectedKey(filteredItems[0]?.key ?? null);
    }
  }, [filteredItems, selectedKey]);

  const selectedItem = useMemo(
    () => filteredItems.find((item) => item.key === selectedKey) ?? null,
    [filteredItems, selectedKey],
  );

  const selectedMatchItems = useMemo(
    () => items.filter((item) => selectedIds.includes(item.key) && item.match),
    [items, selectedIds],
  );

  const toggleSelection = (itemKey: string) => {
    setSelectedIds((current) => (current.includes(itemKey) ? current.filter((value) => value !== itemKey) : [...current, itemKey]));
  };

  const selectAllVisible = () => {
    const visibleKeys = filteredItems.map((item) => item.key);
    const allSelected = visibleKeys.length > 0 && visibleKeys.every((key) => selectedIds.includes(key));
    setSelectedIds(allSelected ? selectedIds.filter((id) => !visibleKeys.includes(id)) : Array.from(new Set([...selectedIds, ...visibleKeys])));
  };

  const moveSelection = (direction: -1 | 1) => {
    if (filteredItems.length === 0) return;
    const currentIndex = filteredItems.findIndex((item) => item.key === selectedKey);
    const nextIndex = currentIndex < 0 ? 0 : Math.max(0, Math.min(filteredItems.length - 1, currentIndex + direction));
    setSelectedKey(filteredItems[nextIndex]?.key ?? null);
  };

  const skipSelected = () => {
    if (!selectedItem) return;
    const currentIndex = filteredItems.findIndex((item) => item.key === selectedItem.key);
    const nextItem = filteredItems[currentIndex + 1] ?? filteredItems[currentIndex - 1] ?? null;
    setSelectedKey(nextItem?.key ?? null);
  };

  const updateMatchStatus = async (matchId: number, nextStatus: 'confirmed' | 'rejected') => {
    setBusyKey(`match-${matchId}-${nextStatus}`);
    try {
      await endpoints.eob.updateMatch(String(matchId), { status: nextStatus });
      await loadData();
      setToast({ message: `Match ${nextStatus}.` });
      setSelectedIds((current) => current.filter((value) => value !== `match-${matchId}`));
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to update match.', tone: 'error' });
    } finally {
      setBusyKey(null);
    }
  };

  const bulkUpdate = async (nextStatus: 'confirmed' | 'rejected') => {
    if (selectedMatchItems.length === 0) {
      setToast({ message: 'Select at least one EOB match to use bulk actions.', tone: 'error' });
      return;
    }

    setBusyKey(`bulk-${nextStatus}`);
    try {
      await Promise.all(
        selectedMatchItems.map((item) => endpoints.eob.updateMatch(String(item.match?.id ?? ''), { status: nextStatus })),
      );
      await loadData();
      setSelectedIds([]);
      setToast({ message: `${selectedMatchItems.length} match${selectedMatchItems.length === 1 ? '' : 'es'} ${nextStatus}.` });
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Bulk update failed.', tone: 'error' });
    } finally {
      setBusyKey(null);
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName?.toLowerCase();
      if (tagName === 'input' || tagName === 'textarea' || target?.isContentEditable) return;

      if (event.key === 'ArrowDown') {
        event.preventDefault();
        moveSelection(1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        moveSelection(-1);
      } else if (event.key.toLowerCase() === 'f') {
        event.preventDefault();
        setCollapsed((current) => !current);
      } else if (event.key.toLowerCase() === 's') {
        event.preventDefault();
        skipSelected();
      } else if (event.key.toLowerCase() === 'y' && selectedItem?.match) {
        event.preventDefault();
        void updateMatchStatus(selectedItem.match.id, 'confirmed');
      } else if (event.key.toLowerCase() === 'n' && selectedItem?.match) {
        event.preventDefault();
        void updateMatchStatus(selectedItem.match.id, 'rejected');
      } else if (event.key.toLowerCase() === 'r' && selectedItem) {
        event.preventDefault();
        if (selectedItem.match) {
          navigate(`/triage/manual-search?matchId=${selectedItem.match.id}&docId=${selectedItem.match.eob_document_id ?? ''}`);
        } else if (selectedItem.orphan) {
          navigate(`/triage/manual-search?docId=${selectedItem.orphan.id}`);
        }
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [navigate, selectedItem, filteredItems, selectedKey]);

  const alternatives = useMemo(() => {
    if (!selectedItem?.match) return [];
    return matches
      .filter(
        (match) =>
          match.id !== selectedItem.match?.id &&
          (match.eob_document_id === selectedItem.match?.eob_document_id ||
            match.bill_document_id === selectedItem.match?.bill_document_id),
      )
      .slice(0, 4);
  }, [matches, selectedItem]);

  const selectedNote = selectedItem ? notes[selectedItem.key] ?? '' : '';

  return (
    <>
      <PageHeader
        title="Triage Queue"
        desc="Review low-confidence EOB matches, triage unmatched documents, and work through queue actions with keyboard-friendly controls."
      />

      {loading ? (
        <SkeletonLoader variant="table" rows={8} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <div className="triage-shell">
          <section className={collapsed ? 'triage-queue-panel collapsed' : 'triage-queue-panel'}>
            <div className="triage-queue-header">
              <button className="triage-collapse-button" onClick={() => setCollapsed((current) => !current)} title="Toggle queue panel (F)">
                {collapsed ? '▶' : '◀'}
              </button>
              <div className="triage-queue-header-content">
                <div className="triage-queue-title">Triage Queue <span>{counts.all} pending</span></div>
                <Tabs
                  active={filter}
                  onChange={(value) => setFilter(value as QueueFilter)}
                  tabs={[
                    { key: 'all', label: `All (${counts.all})` },
                    { key: 'eob', label: `EOB (${counts.eob})` },
                    { key: 'grouping', label: `Groups (${counts.grouping})` },
                    { key: 'orphan', label: `Orphans (${counts.orphan})` },
                  ]}
                />
              </div>
            </div>

            {collapsed ? (
              <div className="triage-collapsed-strip">
                <span>{counts.all} items</span>
              </div>
            ) : (
              <>
                {selectedIds.length > 0 && (
                  <div className="triage-bulk-bar">
                    <span>{selectedIds.length} selected</span>
                    <div className="btn-group">
                      <Button variant="success" size="sm" onClick={() => void bulkUpdate('confirmed')} disabled={busyKey !== null}>
                        Confirm
                      </Button>
                      <Button variant="danger" size="sm" onClick={() => void bulkUpdate('rejected')} disabled={busyKey !== null}>
                        Reject
                      </Button>
                      <Button size="sm" onClick={() => setSelectedIds([])} disabled={busyKey !== null}>
                        Clear
                      </Button>
                    </div>
                  </div>
                )}

                <div className="triage-list-toolbar">
                  <label className="triage-select-all">
                    <input
                      type="checkbox"
                      checked={filteredItems.length > 0 && filteredItems.every((item) => selectedIds.includes(item.key))}
                      onChange={selectAllVisible}
                    />
                    <span>Select visible</span>
                  </label>
                </div>

                <div className="triage-queue-list">
                  {filteredItems.length === 0 ? (
                    <EmptyState title="No triage items in this filter" desc="Try another tab or rerun EOB matching to produce new review candidates." />
                  ) : (
                    filteredItems.map((item) => (
                      <article
                        key={item.key}
                        className={selectedKey === item.key ? 'triage-item selected' : 'triage-item'}
                        onClick={() => setSelectedKey(item.key)}
                      >
                        <div className="triage-item-top">
                          <input
                            type="checkbox"
                            checked={selectedIds.includes(item.key)}
                            onChange={() => toggleSelection(item.key)}
                            onClick={(event) => event.stopPropagation()}
                          />
                          <Badge tone={itemBadgeTone(item.kind)}>{itemBadgeLabel(item.kind)}</Badge>
                          {item.score != null ? (
                            <span className={`triage-score ${priorityForScore(item.score)}`}>{item.score}%</span>
                          ) : null}
                        </div>
                        <div className="triage-item-title">{item.title}</div>
                        <div className="triage-item-reason">{item.reason}</div>
                        <div className="triage-item-meta">
                          <span className={`triage-priority ${item.priority}`}>● {priorityLabels[item.priority]}</span>
                          <span>{timeAgo(item.createdAt)}</span>
                        </div>
                      </article>
                    ))
                  )}
                </div>
              </>
            )}
          </section>

          <section className="triage-detail-panel">
            {selectedItem ? (
              <>
                <div className="triage-detail-header">
                  <div>
                    <div className="triage-detail-title">{selectedItem.kind === 'eob' ? 'EOB Match Review' : selectedItem.kind === 'grouping' ? 'Grouping Review' : 'Orphan Review'}</div>
                    <div className="text-muted">{selectedItem.title}</div>
                  </div>
                  <div className="btn-group">
                    <Button
                      variant="success"
                      onClick={() => selectedItem.match && void updateMatchStatus(selectedItem.match.id, 'confirmed')}
                      disabled={!selectedItem.match || busyKey !== null}
                      title="Confirm (Y)"
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => selectedItem.match && void updateMatchStatus(selectedItem.match.id, 'rejected')}
                      disabled={!selectedItem.match || busyKey !== null}
                      title="Reject (N)"
                    >
                      Reject
                    </Button>
                    <Button
                      onClick={() => {
                        if (selectedItem.match) {
                          navigate(`/triage/manual-search?matchId=${selectedItem.match.id}&docId=${selectedItem.match.eob_document_id ?? ''}`);
                        } else if (selectedItem.orphan) {
                          navigate(`/triage/manual-search?docId=${selectedItem.orphan.id}`);
                        }
                      }}
                      title="Re-link (R)"
                    >
                      Re-link
                    </Button>
                    <Button variant="ghost" onClick={skipSelected} title="Skip (S)">
                      Skip
                    </Button>
                    {selectedItem.match?.bill_preview_url ? (
                      <a className="triage-paperless-link" href={selectedItem.match.bill_preview_url} target="_blank" rel="noreferrer">
                        Paperless
                      </a>
                    ) : selectedItem.orphan?.document_url ? (
                      <a className="triage-paperless-link" href={selectedItem.orphan.document_url} target="_blank" rel="noreferrer">
                        Paperless
                      </a>
                    ) : null}
                  </div>
                </div>

                <div className="triage-reason-banner">
                  <strong>Flagged for review:</strong> {selectedItem.reason}
                </div>

                {selectedItem.match ? (
                  <>
                    <Card title="Confidence breakdown">
                      <div className="triage-confidence-summary">
                        <div>
                          <div className={`triage-overall-score ${priorityForScore(selectedItem.score)}`}>{selectedItem.score}%</div>
                          <div className="text-muted">Weighted 5-factor score · auto-confirm typically starts at 85%.</div>
                        </div>
                        <Badge tone={selectedItem.score != null && selectedItem.score >= 85 ? 'success' : 'warning'}>
                          {selectedItem.score != null && selectedItem.score >= 85 ? 'Ready to confirm' : 'Needs review'}
                        </Badge>
                      </div>
                      <div className="triage-confidence-list">
                        <ConfidenceBar label="Date (30%)" pct={valueToPercent(selectedItem.match.breakdown?.date)} />
                        <ConfidenceBar label="Provider (25%)" pct={valueToPercent(selectedItem.match.breakdown?.provider)} />
                        <ConfidenceBar label="Patient (20%)" pct={valueToPercent(selectedItem.match.breakdown?.patient)} />
                        <ConfidenceBar label="Amount (15%)" pct={valueToPercent(selectedItem.match.breakdown?.amount)} />
                        <ConfidenceBar label="Procedures (10%)" pct={valueToPercent(selectedItem.match.breakdown?.procedures)} />
                      </div>
                    </Card>

                    <Card title="Amount validation">
                      <div className={`triage-amount-card ${mismatchClass(selectedItem.match.breakdown?.amount)}`}>
                        <div className="triage-amount-title">{mismatchLabel(selectedItem.match.breakdown?.amount)}</div>
                        <div className="text-muted">
                          The current API exposes the amount confidence factor but not the raw extracted bill/EOB amounts, so this view summarizes the signal strength instead of exact values.
                        </div>
                        <div className="triage-amount-score">Amount factor: {valueToPercent(selectedItem.match.breakdown?.amount)}%</div>
                      </div>
                    </Card>

                    <Card title="Document comparison">
                      <div className="triage-compare-grid">
                        <div className="triage-compare-col">
                          <div className="triage-compare-header">EOB document</div>
                          <div className="triage-compare-row"><span>Reference</span><strong>#{selectedItem.match.eob_document_id ?? '—'}</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.date)}`}><span>Date</span><strong>{factorLabel('Date', selectedItem.match.breakdown?.date)}</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.provider)}`}><span>Provider</span><strong>{factorLabel('Provider', selectedItem.match.breakdown?.provider)}</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.patient)}`}><span>Patient</span><strong>{factorLabel('Patient', selectedItem.match.breakdown?.patient)}</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.amount)}`}><span>Amount</span><strong>{factorLabel('Amount', selectedItem.match.breakdown?.amount)}</strong></div>
                          <div className="triage-compare-row"><span>Preview</span>{selectedItem.match.eob_preview_url ? <a href={selectedItem.match.eob_preview_url} target="_blank" rel="noreferrer">Open →</a> : <strong>Unavailable</strong>}</div>
                        </div>
                        <div className="triage-compare-col">
                          <div className="triage-compare-header">Bill / claim document</div>
                          <div className="triage-compare-row"><span>Reference</span><strong>#{selectedItem.match.bill_document_id ?? '—'}</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.date)}`}><span>Date signal</span><strong>{valueToPercent(selectedItem.match.breakdown?.date)}%</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.provider)}`}><span>Provider signal</span><strong>{valueToPercent(selectedItem.match.breakdown?.provider)}%</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.patient)}`}><span>Patient signal</span><strong>{valueToPercent(selectedItem.match.breakdown?.patient)}%</strong></div>
                          <div className={`triage-compare-row ${mismatchClass(selectedItem.match.breakdown?.procedures)}`}><span>Procedure overlap</span><strong>{valueToPercent(selectedItem.match.breakdown?.procedures)}%</strong></div>
                          <div className="triage-compare-row"><span>Preview</span>{selectedItem.match.bill_preview_url ? <a href={selectedItem.match.bill_preview_url} target="_blank" rel="noreferrer">Open →</a> : <strong>Unavailable</strong>}</div>
                        </div>
                      </div>
                    </Card>

                    <Card title="Alternative candidates">
                      {alternatives.length > 0 ? (
                        <div className="triage-alt-list">
                          {alternatives.map((match) => (
                            <button
                              key={match.id}
                              className="triage-alt-item"
                              onClick={() => setSelectedKey(`match-${match.id}`)}
                            >
                              <span className="triage-alt-score">{valueToPercent(match.score)}%</span>
                              <span>EOB #{match.eob_document_id ?? '—'} ↔ Bill #{match.bill_document_id ?? '—'}</span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <EmptyState icon="🔎" title="No alternative candidates" desc="No other candidate match shares this EOB or bill document in the current result set." />
                      )}
                    </Card>

                    <div className="triage-detail-grid">
                      <Card title="Match history">
                        <div className="triage-history-list">
                          <div className="triage-history-item">
                            <span className="triage-history-dot auto" />
                            <div>
                              <div><strong>Auto-matched</strong> by the EOB matching engine.</div>
                              <div className="text-muted">{formatDateTime(selectedItem.match.created_at)} · Score {valueToPercent(selectedItem.match.score)}%</div>
                            </div>
                          </div>
                          <div className="triage-history-item">
                            <span className="triage-history-dot" />
                            <div>
                              <div><strong>Queued for manual review</strong> because {weakestBreakdown(selectedItem.match.breakdown).label.toLowerCase()} was the weakest factor.</div>
                              <div className="text-muted">Status: {selectedItem.match.status ?? 'candidate'}</div>
                            </div>
                          </div>
                          {selectedItem.match.confirmed_at ? (
                            <div className="triage-history-item">
                              <span className="triage-history-dot confirm" />
                              <div>
                                <div><strong>Previously confirmed</strong></div>
                                <div className="text-muted">{formatDateTime(selectedItem.match.confirmed_at)}</div>
                              </div>
                            </div>
                          ) : null}
                        </div>
                      </Card>

                      <Card title="Notes">
                        <div className="form-group" style={{ marginBottom: 0 }}>
                          <textarea
                            rows={6}
                            placeholder="Add context for the next reviewer or note why this match is correct / incorrect."
                            value={selectedNote}
                            onChange={(event) =>
                              setNotes((current) => ({
                                ...current,
                                [selectedItem.key]: event.target.value,
                              }))
                            }
                          />
                        </div>
                      </Card>
                    </div>
                  </>
                ) : selectedItem.kind === 'grouping' ? (
                  <>
                    <Card title="Grouping summary">
                      <div className="triage-group-summary">
                        <div>This provider currently has <strong>{selectedItem.relatedUnmatched?.length ?? 0}</strong> unmatched documents that likely belong to the same series or grouping rule.</div>
                        <div className="text-muted">Use manual search to inspect candidates individually or keep this item in the queue until richer grouping actions exist in the backend.</div>
                      </div>
                    </Card>
                    <Card title="Documents in this cluster">
                      <div className="triage-group-docs">
                        {(selectedItem.relatedUnmatched ?? []).map((doc) => (
                          <div key={doc.id} className="triage-group-doc">
                            <div>
                              <strong>{doc.provider || 'Unknown provider'}</strong>
                              <div className="text-muted">Service date {formatDate(doc.date_of_service)} · Amount {formatCurrency(doc.amount ?? doc.patient_responsibility)}</div>
                            </div>
                            {doc.document_url ? <a href={doc.document_url} target="_blank" rel="noreferrer">Open →</a> : null}
                          </div>
                        ))}
                      </div>
                    </Card>
                  </>
                ) : (
                  <>
                    <Card title="Unmatched document">
                      <div className="triage-group-summary">
                        <div><strong>Provider:</strong> {selectedItem.orphan?.provider || 'Unknown provider'}</div>
                        <div><strong>Service date:</strong> {formatDate(selectedItem.orphan?.date_of_service)}</div>
                        <div><strong>Amount:</strong> {formatCurrency(selectedItem.orphan?.amount ?? selectedItem.orphan?.patient_responsibility)}</div>
                        <div className="text-muted">This item does not have a candidate match record yet, so confirm/reject actions stay disabled until a dedicated manual-link endpoint exists.</div>
                      </div>
                    </Card>
                    <Card title="Next step">
                      <div className="triage-group-summary">
                        <div>Use manual search to review nearby candidate claims for document #{selectedItem.orphan?.id}.</div>
                        <div className="btn-group">
                          <Button onClick={() => navigate(`/triage/manual-search?docId=${selectedItem.orphan?.id ?? ''}`)}>Open manual search</Button>
                          {selectedItem.orphan?.document_url ? (
                            <a className="triage-paperless-link" href={selectedItem.orphan.document_url} target="_blank" rel="noreferrer">
                              Open in Paperless
                            </a>
                          ) : null}
                        </div>
                      </div>
                    </Card>
                  </>
                )}
              </>
            ) : (
              <EmptyState
                title="No triage item selected"
                desc="Choose a queue item to inspect the confidence breakdown, alternatives, and review actions."
              />
            )}
          </section>
        </div>
      )}

      <div className="triage-keyboard-hint">
        <kbd>Y</kbd> Confirm · <kbd>N</kbd> Reject · <kbd>R</kbd> Re-link · <kbd>S</kbd> Skip · <kbd>↑↓</kbd> Navigate · <kbd>F</kbd> Toggle queue
      </div>

      {toast && <Toast message={toast.message} tone={toast.tone} />}
    </>
  );
}
