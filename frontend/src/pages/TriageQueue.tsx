import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  FilterPills,
  PageHeader,
  SkeletonLoader,
  Tabs,
  Toast,
} from '../components/ui';
import EobMatchDetail from '../components/EobMatchDetail';
import OrphanDetail from '../components/triage/OrphanDetail';
import { endpoints } from '../lib/api';
import '../styles/triage-queue.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface TriageItem {
  id: string;
  item_type: string;
  priority: number;
  status: string;
  source: string;
  target_type: string;
  target_id: string;
  reason: string | null;
  metadata: Record<string, unknown> | null;
  deferred_until: string | null;
  resolved_at: string | null;
  resolved_action: string | null;
  created_at: string | null;
}

interface QueueResponse {
  items: TriageItem[];
  count: number;
  offset: number;
  limit: number;
}

interface StatsResponse {
  total: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  pending: number;
}

type ItemTypeFilter = 'all' | 'eob_match_review' | 'grouping_anomaly' | 'orphan_document';
type StatusFilter = 'pending' | 'deferred' | 'resolved';
type ToastState = { message: string; tone?: 'success' | 'error'; undoId?: string } | null;

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function timeAgo(value?: string | null) {
  if (!value) return 'Unknown';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Math.max(0, Date.now() - date.getTime());
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return mins <= 1 ? 'Just now' : `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return '1d ago';
  return `${days}d ago`;
}

function typeLabel(itemType: string): string {
  switch (itemType) {
    case 'eob_match_review': return 'EOB';
    case 'grouping_anomaly': return 'GROUPING';
    case 'orphan_document': return 'ORPHAN';
    default: return itemType.toUpperCase();
  }
}

function typeBadgeTone(itemType: string) {
  switch (itemType) {
    case 'eob_match_review': return 'info' as const;
    case 'grouping_anomaly': return 'warning' as const;
    case 'orphan_document': return 'danger' as const;
    default: return 'muted' as const;
  }
}

function priorityLabel(priority: number): string {
  if (priority >= 80) return 'High';
  if (priority >= 50) return 'Medium';
  return 'Low';
}

function priorityBadgeTone(priority: number) {
  if (priority >= 80) return 'danger' as const;
  if (priority >= 50) return 'warning' as const;
  return 'muted' as const;
}

function itemTitle(item: TriageItem): string {
  const meta = item.metadata || {};
  if (item.item_type === 'eob_match_review') {
    const eobId = meta.eob_document_id ?? '?';
    const billId = meta.bill_document_id ?? '?';
    const scorePct = typeof meta.score_pct === 'number' ? `${meta.score_pct}%` : '';
    return `EOB #${eobId} ↔ Bill #${billId}${scorePct ? ` (${scorePct})` : ''}`;
  }
  if (item.item_type === 'orphan_document') {
    const provider = meta.provider_name || 'Unknown provider';
    return `Orphan: ${provider} (doc #${meta.document_id ?? item.target_id})`;
  }
  if (item.item_type === 'grouping_anomaly') {
    return `Grouping: ${meta.series_name || item.target_id}`;
  }
  return `${typeLabel(item.item_type)}: ${item.target_id}`;
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

const UNDO_TIMEOUT_MS = 30_000;

export default function TriageQueue() {
  // Data state
  const [items, setItems] = useState<TriageItem[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [typeFilter, setTypeFilter] = useState<ItemTypeFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('pending');

  // Selection
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const lastCheckedRef = useRef<string | null>(null);

  // UI state
  const [collapsed, setCollapsed] = useState(false);
  const [toast, setToast] = useState<ToastState>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [populating, setPopulating] = useState(false);

  // ------------------------------------------------------------------
  // Data loading
  // ------------------------------------------------------------------

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (typeFilter !== 'all') params.set('type', typeFilter);
      params.set('status', statusFilter);
      params.set('limit', '200');

      const [queueRes, statsRes] = await Promise.all([
        endpoints.triage.queue(params.toString()) as Promise<QueueResponse>,
        endpoints.triage.stats() as Promise<StatsResponse>,
      ]);

      setItems(queueRes?.items ?? []);
      setStats(statsRes ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load triage queue.');
    } finally {
      setLoading(false);
    }
  }, [typeFilter, statusFilter]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // Auto-clear toast
  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), toast.undoId ? UNDO_TIMEOUT_MS : 3200);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  // ------------------------------------------------------------------
  // Selection helpers
  // ------------------------------------------------------------------

  const selectedItem = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );

  // Keep selectedId valid when items change
  useEffect(() => {
    if (items.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!items.some((item) => item.id === selectedId)) {
      setSelectedId(items[0]?.id ?? null);
    }
  }, [items, selectedId]);

  const toggleCheck = (itemId: string, shiftKey = false) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);

      if (shiftKey && lastCheckedRef.current) {
        const startIdx = items.findIndex((i) => i.id === lastCheckedRef.current);
        const endIdx = items.findIndex((i) => i.id === itemId);
        if (startIdx >= 0 && endIdx >= 0) {
          const [lo, hi] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
          for (let i = lo; i <= hi; i++) {
            next.add(items[i].id);
          }
          lastCheckedRef.current = itemId;
          return next;
        }
      }

      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      lastCheckedRef.current = itemId;
      return next;
    });
  };

  const selectAllVisible = () => {
    const allChecked = items.length > 0 && items.every((i) => checkedIds.has(i.id));
    if (allChecked) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(items.map((i) => i.id)));
    }
  };

  const moveSelection = (direction: -1 | 1) => {
    if (items.length === 0) return;
    const idx = items.findIndex((i) => i.id === selectedId);
    const next = idx < 0 ? 0 : Math.max(0, Math.min(items.length - 1, idx + direction));
    setSelectedId(items[next]?.id ?? null);
  };

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  const handleResolve = async (itemId: string, action: string) => {
    setBusyAction(`${itemId}-${action}`);
    try {
      await endpoints.triage.resolve(itemId, { action });
      setToast({ message: `Item ${action}ed.`, tone: 'success', undoId: itemId });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Action failed.', tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  const handleDefer = async (itemId: string) => {
    setBusyAction(`${itemId}-defer`);
    try {
      await endpoints.triage.defer(itemId);
      setToast({ message: 'Item deferred for 7 days.', tone: 'success', undoId: itemId });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Defer failed.', tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  const handleDismiss = async (itemId: string) => {
    setBusyAction(`${itemId}-dismiss`);
    try {
      await endpoints.triage.dismiss(itemId);
      setToast({ message: 'Item dismissed.', tone: 'success', undoId: itemId });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Dismiss failed.', tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  const handleUndo = async (itemId: string) => {
    try {
      await endpoints.triage.undo(itemId);
      setToast({ message: 'Action undone.', tone: 'success' });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Undo failed.', tone: 'error' });
    }
  };

  const handleBulkAction = async (action: 'confirm' | 'reject' | 'defer' | 'dismiss') => {
    const ids = Array.from(checkedIds);
    if (ids.length === 0) return;
    setBusyAction(`bulk-${action}`);
    try {
      if (action === 'defer') {
        await Promise.all(ids.map((id) => endpoints.triage.defer(id)));
      } else if (action === 'dismiss') {
        await Promise.all(ids.map((id) => endpoints.triage.dismiss(id)));
      } else {
        await Promise.all(ids.map((id) => endpoints.triage.resolve(id, { action })));
      }
      setCheckedIds(new Set());
      setToast({ message: `${ids.length} item${ids.length > 1 ? 's' : ''} ${action}ed.`, tone: 'success' });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : `Bulk ${action} failed.`, tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  const handlePopulate = async () => {
    setPopulating(true);
    try {
      const result = await endpoints.triage.populate() as { items_created: number };
      setToast({ message: `Queue scan complete: ${result.items_created} items added.`, tone: 'success' });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Queue population failed.', tone: 'error' });
    } finally {
      setPopulating(false);
    }
  };

  // ------------------------------------------------------------------
  // Keyboard shortcuts
  // ------------------------------------------------------------------

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        moveSelection(1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        moveSelection(-1);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setSelectedId(null);
      } else if (e.key.toLowerCase() === 'y' && selectedId) {
        e.preventDefault();
        void handleResolve(selectedId, 'confirm');
      } else if (e.key.toLowerCase() === 'n' && selectedId) {
        e.preventDefault();
        void handleResolve(selectedId, 'reject');
      } else if (e.key.toLowerCase() === 's' && selectedId) {
        e.preventDefault();
        // Skip — move to next
        moveSelection(1);
      } else if (e.key.toLowerCase() === 'd' && selectedId) {
        e.preventDefault();
        void handleDefer(selectedId);
      } else if (e.key.toLowerCase() === 'x' && selectedId) {
        e.preventDefault();
        void handleDismiss(selectedId);
      } else if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        setCollapsed((c) => !c);
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [items, selectedId]);

  // ------------------------------------------------------------------
  // Derived counts
  // ------------------------------------------------------------------

  const pendingCount = stats?.pending ?? 0;
  const typeCounts = stats?.by_type ?? {};
  const statusCounts = stats?.by_status ?? {};

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <>
      <PageHeader
        title="Triage Queue"
        desc="Review flagged items: low-confidence EOB matches, grouping anomalies, and orphan documents."
      />

      {loading && !items.length ? (
        <SkeletonLoader variant="table" rows={8} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <div className="triage-shell">
          {/* ── Queue list panel (left) ── */}
          <section className={collapsed ? 'triage-queue-panel collapsed' : 'triage-queue-panel'}>
            <div className="triage-queue-header">
              <button
                className="triage-collapse-button"
                onClick={() => setCollapsed((c) => !c)}
                title="Toggle queue panel (F)"
              >
                {collapsed ? '▶' : '◀'}
              </button>
              <div className="triage-queue-header-content">
                <div className="triage-queue-title">
                  Triage Queue <span>{pendingCount} pending</span>
                </div>

                {/* Type filter tabs */}
                <Tabs
                  active={typeFilter}
                  onChange={(v) => setTypeFilter(v as ItemTypeFilter)}
                  tabs={[
                    { key: 'all', label: `All (${stats?.total ?? 0})` },
                    { key: 'eob_match_review', label: `EOB (${typeCounts.eob_match_review ?? 0})` },
                    { key: 'grouping_anomaly', label: `Groups (${typeCounts.grouping_anomaly ?? 0})` },
                    { key: 'orphan_document', label: `Orphans (${typeCounts.orphan_document ?? 0})` },
                  ]}
                />

                {/* Status filter */}
                <div className="triage-status-filter">
                  <FilterPills
                    active={statusFilter}
                    onChange={(v) => setStatusFilter(v as StatusFilter)}
                    options={[
                      { key: 'pending', label: `Pending (${statusCounts.pending ?? 0})` },
                      { key: 'deferred', label: `Deferred (${statusCounts.deferred ?? 0})` },
                      { key: 'resolved', label: `Resolved (${statusCounts.resolved ?? 0})` },
                    ]}
                  />
                </div>
              </div>
            </div>

            {collapsed ? (
              <div className="triage-collapsed-strip">
                <span>{items.length} items</span>
              </div>
            ) : (
              <>
                {/* Bulk action bar */}
                {checkedIds.size > 0 && (
                  <div className="triage-bulk-bar">
                    <span>{checkedIds.size} selected</span>
                    <div className="btn-group">
                      <Button variant="success" size="sm" onClick={() => void handleBulkAction('confirm')} disabled={busyAction !== null}>
                        Confirm
                      </Button>
                      <Button variant="danger" size="sm" onClick={() => void handleBulkAction('reject')} disabled={busyAction !== null}>
                        Reject
                      </Button>
                      <Button size="sm" onClick={() => void handleBulkAction('defer')} disabled={busyAction !== null}>
                        Defer
                      </Button>
                      <Button size="sm" onClick={() => void handleBulkAction('dismiss')} disabled={busyAction !== null}>
                        Dismiss
                      </Button>
                      <Button size="sm" onClick={() => setCheckedIds(new Set())} disabled={busyAction !== null}>
                        Clear
                      </Button>
                    </div>
                  </div>
                )}

                {/* Select-all + populate */}
                <div className="triage-list-toolbar">
                  <label className="triage-select-all">
                    <input
                      type="checkbox"
                      checked={items.length > 0 && items.every((i) => checkedIds.has(i.id))}
                      onChange={selectAllVisible}
                    />
                    <span>Select visible</span>
                  </label>
                  <Button size="sm" onClick={() => void handlePopulate()} disabled={populating}>
                    {populating ? 'Scanning…' : '🔄 Populate'}
                  </Button>
                </div>

                {/* Queue list */}
                <div className="triage-queue-list">
                  {items.length === 0 ? (
                    <EmptyState
                      title="Queue is empty"
                      desc={
                        statusFilter === 'pending'
                          ? 'No items need review. Use "Populate" to scan for new flagged items.'
                          : `No ${statusFilter} items found.`
                      }
                    />
                  ) : (
                    items.map((item) => (
                      <article
                        key={item.id}
                        className={selectedId === item.id ? 'triage-item selected' : 'triage-item'}
                        onClick={() => setSelectedId(item.id)}
                      >
                        <div className="triage-item-top">
                          <input
                            type="checkbox"
                            checked={checkedIds.has(item.id)}
                            onChange={(e) => toggleCheck(item.id, e.nativeEvent instanceof MouseEvent && e.nativeEvent.shiftKey)}
                            onClick={(e) => e.stopPropagation()}
                          />
                          <Badge tone={typeBadgeTone(item.item_type)}>{typeLabel(item.item_type)}</Badge>
                          {item.metadata && typeof item.metadata.score_pct === 'number' && (
                            <span className={`triage-score ${item.metadata.score_pct >= 85 ? 'high' : item.metadata.score_pct >= 70 ? 'medium' : 'low'}`}>
                              {item.metadata.score_pct}%
                            </span>
                          )}
                        </div>
                        <div className="triage-item-title">{itemTitle(item)}</div>
                        <div className="triage-item-reason">{item.reason || 'Flagged for review'}</div>
                        <div className="triage-item-meta">
                          <Badge tone={priorityBadgeTone(item.priority)}>{priorityLabel(item.priority)}</Badge>
                          <span>{timeAgo(item.created_at)}</span>
                        </div>
                      </article>
                    ))
                  )}
                </div>
              </>
            )}
          </section>

          {/* ── Detail panel (right) ── */}
          <section className="triage-detail-panel">
            {selectedItem ? (
              selectedItem.item_type === 'eob_match_review' ? (
                /* EOB Match Review — rich detail component (#834) */
                <EobMatchDetail
                  matchId={Number(selectedItem.target_id)}
                  triageItemId={selectedItem.id}
                  onResolved={() => {
                    void loadData();
                  }}
                  onSkip={() => {
                    // Advance to next item in the list
                    const currentIndex = items.findIndex((i) => i.id === selectedItem.id);
                    const nextItem = items[currentIndex + 1] ?? items[0];
                    if (nextItem && nextItem.id !== selectedItem.id) {
                      setSelectedId(nextItem.id);
                    }
                  }}
                />
              ) : selectedItem.item_type === 'orphan_document' ? (
                /* Orphan Document — rich detail component (#831) */
                <OrphanDetail
                  triageItem={selectedItem}
                  onResolved={() => {
                    void loadData();
                  }}
                  onSkip={() => {
                    const currentIndex = items.findIndex((i) => i.id === selectedItem.id);
                    const nextItem = items[currentIndex + 1] ?? items[0];
                    if (nextItem && nextItem.id !== selectedItem.id) {
                      setSelectedId(nextItem.id);
                    }
                  }}
                />
              ) : (
                <>
                <div className="triage-detail-header">
                  <div>
                    <div className="triage-detail-title">{itemTitle(selectedItem)}</div>
                    <div className="text-muted">
                      <Badge tone={typeBadgeTone(selectedItem.item_type)}>{typeLabel(selectedItem.item_type)}</Badge>
                      {' · '}
                      Status: {selectedItem.status}
                      {' · '}
                      Priority: {selectedItem.priority}
                    </div>
                  </div>
                  <div className="btn-group">
                    <Button
                      variant="success"
                      onClick={() => void handleResolve(selectedItem.id, 'confirm')}
                      disabled={busyAction !== null || selectedItem.status !== 'pending'}
                      title="Confirm (Y)"
                    >
                      Confirm
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => void handleResolve(selectedItem.id, 'reject')}
                      disabled={busyAction !== null || selectedItem.status !== 'pending'}
                      title="Reject (N)"
                    >
                      Reject
                    </Button>
                    <Button
                      onClick={() => void handleDefer(selectedItem.id)}
                      disabled={busyAction !== null || selectedItem.status !== 'pending'}
                      title="Defer (D)"
                    >
                      Defer
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => void handleDismiss(selectedItem.id)}
                      disabled={busyAction !== null || selectedItem.status !== 'pending'}
                      title="Dismiss (X)"
                    >
                      Dismiss
                    </Button>
                  </div>
                </div>

                {/* Reason banner */}
                {selectedItem.reason && (
                  <div className="triage-reason-banner">
                    <strong>Flagged for review:</strong> {selectedItem.reason}
                  </div>
                )}

                {/* Item info card */}
                <Card title="Item details">
                  <div className="triage-detail-info">
                    <div className="triage-detail-row">
                      <span>Type</span>
                      <strong>{typeLabel(selectedItem.item_type)}</strong>
                    </div>
                    <div className="triage-detail-row">
                      <span>Priority</span>
                      <strong>{selectedItem.priority} ({priorityLabel(selectedItem.priority)})</strong>
                    </div>
                    <div className="triage-detail-row">
                      <span>Source</span>
                      <strong>{selectedItem.source}</strong>
                    </div>
                    <div className="triage-detail-row">
                      <span>Target</span>
                      <strong>{selectedItem.target_type}: {selectedItem.target_id}</strong>
                    </div>
                    <div className="triage-detail-row">
                      <span>Created</span>
                      <strong>{selectedItem.created_at ? new Date(selectedItem.created_at).toLocaleString() : '—'}</strong>
                    </div>
                    {selectedItem.deferred_until && (
                      <div className="triage-detail-row">
                        <span>Deferred until</span>
                        <strong>{new Date(selectedItem.deferred_until).toLocaleString()}</strong>
                      </div>
                    )}
                    {selectedItem.resolved_at && (
                      <div className="triage-detail-row">
                        <span>Resolved</span>
                        <strong>{selectedItem.resolved_action} at {new Date(selectedItem.resolved_at).toLocaleString()}</strong>
                      </div>
                    )}
                  </div>
                </Card>

                {/* Metadata dump — placeholder for specific detail views (#834, #829, #830, #831) */}
                <Card title="Item metadata">
                  <div className="triage-metadata-dump">
                    {selectedItem.metadata ? (
                      <pre>{JSON.stringify(selectedItem.metadata, null, 2)}</pre>
                    ) : (
                      <div className="text-muted">No additional metadata available for this item.</div>
                    )}
                  </div>
                </Card>
              </>
              )
            ) : (
              <EmptyState
                title="No triage item selected"
                desc="Choose a queue item to inspect its details, metadata, and available actions."
              />
            )}
          </section>
        </div>
      )}

      {/* Keyboard hint */}
      <div className="triage-keyboard-hint">
        <kbd>Y</kbd> Confirm · <kbd>N</kbd> Reject · <kbd>S</kbd> Skip · <kbd>D</kbd> Defer · <kbd>X</kbd> Dismiss · <kbd>↑↓</kbd> Navigate · <kbd>Esc</kbd> Deselect · <kbd>F</kbd> Toggle queue
      </div>

      {/* Toast with optional undo */}
      {toast && (
        <div className="triage-toast-wrapper">
          <Toast message={toast.message} tone={toast.tone} />
          {toast.undoId && (
            <button className="triage-undo-btn" onClick={() => void handleUndo(toast.undoId!)}>
              Undo
            </button>
          )}
        </div>
      )}
    </>
  );
}
