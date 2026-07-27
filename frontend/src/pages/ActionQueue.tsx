import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  ConfidenceBar,
  DataTable,
  EmptyState,
  ErrorState,
  FilterPills,
  PageHeader,
  RiskScoreBar,
  SkeletonLoader,
  StatCard,
  StatGrid,
  Toast,
} from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/action-queue.css';

interface ActionQueueCheck {
  status?: string;
  read_only?: boolean;
  paperless?: { status?: string };
  ollama?: { status?: string; model?: string; base_url?: string };
}

interface QueueDatabaseCounts {
  pending?: number;
  completed?: number;
  dismissed?: number;
  total?: number;
}

interface QueueProgress {
  stage?: string;
  processed?: number;
  total?: number;
  current_document?: string;
}

interface QueueStatus {
  status?: string;
  started_at?: string;
  finished_at?: string;
  dry_run?: boolean;
  read_only?: boolean;
  progress?: QueueProgress;
  database?: QueueDatabaseCounts;
}

interface ActionItem {
  id: number;
  document_id?: number | null;
  document_title?: string | null;
  action_type?: string | null;
  title?: string | null;
  summary?: string | null;
  due_date?: string | null;
  amount?: number | null;
  urgency?: string | null;
  confidence?: number | null;
  risk_score?: number | null;
  status?: string | null;
  correspondent?: string | null;
  ai_reasoning?: string | null;
  preview_url?: string | null;
  version?: number | null;
  created_at?: string | null;
  completed_at?: string | null;
}

interface ActionListResponse {
  actions?: ActionItem[];
  total?: number;
}

type ActionFilter = 'pending' | 'completed' | 'dismissed' | 'all';
type ToastState = { message: string; tone?: 'success' | 'error' } | null;

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

function normalizeStatus(value?: string | null) {
  return (value ?? 'pending').toLowerCase();
}

function normalizeType(value?: string | null) {
  return (value ?? 'review').toUpperCase();
}

function actionTypeTone(type: string) {
  switch (type) {
    case 'PAY':
      return 'success' as const;
    case 'RESPOND':
      return 'warning' as const;
    case 'FILE':
      return 'info' as const;
    case 'REVIEW':
      return 'danger' as const;
    default:
      return 'muted' as const;
  }
}

function statusTone(status: string) {
  switch (status) {
    case 'completed':
      return 'success' as const;
    case 'dismissed':
      return 'muted' as const;
    default:
      return 'warning' as const;
  }
}

function healthTone(status?: string) {
  const normalized = (status ?? '').toLowerCase();
  if (normalized === 'ok' || normalized === 'healthy') return 'success' as const;
  if (normalized === 'degraded' || normalized === 'warning') return 'warning' as const;
  return 'danger' as const;
}

function dueMeta(action: ActionItem) {
  const due = action.due_date ? new Date(action.due_date) : null;
  if (!due || Number.isNaN(due.getTime())) {
    return { label: 'No due date', tone: 'muted' as const };
  }

  const days = Math.ceil((due.getTime() - Date.now()) / 86400000);
  if (days < 0) return { label: `${Math.abs(days)}d overdue`, tone: 'danger' as const };
  if (days <= 3) return { label: `Due in ${days}d`, tone: 'danger' as const };
  if (days <= 7) return { label: `Due in ${days}d`, tone: 'warning' as const };
  return { label: `Due in ${days}d`, tone: 'success' as const };
}

export default function ActionQueue() {
  const [status, setStatus] = useState<QueueStatus | null>(null);
  const [health, setHealth] = useState<ActionQueueCheck | null>(null);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [filter, setFilter] = useState<ActionFilter>('pending');
  const [search, setSearch] = useState('');
  const [selectedActionId, setSelectedActionId] = useState<number | null>(null);
  const [pdfExpanded, setPdfExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);

  // [ARCH-01] Bulk selection state
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
  const lastCheckedRef = useRef<number | null>(null);
  const [pendingBulkAction, setPendingBulkAction] = useState<{ action: string; ids: number[] } | null>(null);

  // [UX-10] Per-item loading state for bulk ops
  const [processingIds, setProcessingIds] = useState<Set<number>>(new Set());
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);

  // [UX-07] Cache selected action so filter changes don't lose it
  const [cachedAction, setCachedAction] = useState<ActionItem | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = filter === 'all' ? 'limit=200' : `status=${filter}&limit=200`;
      const [statusResponse, actionsResponse] = await Promise.all([
        endpoints.actionQueue.status() as Promise<QueueStatus>,
        endpoints.actionQueue.actions(params) as Promise<ActionListResponse>,
      ]);

      setStatus(statusResponse ?? null);
      setActions(actionsResponse?.actions ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load action queue.');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const filteredActions = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return actions;
    return actions.filter((action) => {
      const haystack = [
        action.title,
        action.document_title,
        action.summary,
        action.correspondent,
        action.action_type,
        action.document_id,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [actions, search]);

  // [UX-07] Preserve selection across filter changes — cache the selected action
  // When the selected item leaves the filtered set, show it from cache with a banner
  const selectedInView = useMemo(
    () => (selectedActionId !== null ? filteredActions.some((a) => a.id === selectedActionId) : false),
    [filteredActions, selectedActionId],
  );

  useEffect(() => {
    if (selectedActionId === null) return;
    const found = filteredActions.find((a) => a.id === selectedActionId);
    if (found) {
      setCachedAction(found);
    }
    // Don't clear cachedAction when item leaves view — that's the point of UX-07
  }, [filteredActions, selectedActionId]);

  const selectedAction = useMemo(() => {
    if (selectedActionId === null) return null;
    // Prefer live data from current view, fall back to cache
    return filteredActions.find((a) => a.id === selectedActionId) ?? cachedAction;
  }, [filteredActions, selectedActionId, cachedAction]);

  // [UX-15] Reset PDF viewer state on action selection change
  useEffect(() => {
    setPdfExpanded(false);
  }, [selectedActionId]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        const tag = (e.target as HTMLElement)?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (pendingBulkAction) { setPendingBulkAction(null); return; }
        if (selectedActionId !== null) setSelectedActionId(null);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [selectedActionId, pendingBulkAction]);

  // ------------------------------------------------------------------
  // Selection helpers (ARCH-01)
  // ------------------------------------------------------------------

  const toggleCheck = (actionId: number, shiftKey = false) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);

      if (shiftKey && lastCheckedRef.current !== null) {
        const startIdx = filteredActions.findIndex((a) => a.id === lastCheckedRef.current);
        const endIdx = filteredActions.findIndex((a) => a.id === actionId);
        if (startIdx >= 0 && endIdx >= 0) {
          const [lo, hi] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
          for (let i = lo; i <= hi; i++) {
            next.add(filteredActions[i].id);
          }
          lastCheckedRef.current = actionId;
          return next;
        }
      }

      if (next.has(actionId)) {
        next.delete(actionId);
      } else {
        next.add(actionId);
      }
      lastCheckedRef.current = actionId;
      return next;
    });
  };

  const selectAllVisible = () => {
    const allChecked = filteredActions.length > 0 && filteredActions.every((a) => checkedIds.has(a.id));
    if (allChecked) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(filteredActions.map((a) => a.id)));
    }
  };

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  const runCheck = async (mode: 'health' | 'custom-fields') => {
    setBusyKey(mode);
    try {
      if (mode === 'health') {
        const response = (await endpoints.actionQueue.check()) as ActionQueueCheck;
        setHealth(response);
        setToast({ message: 'Pipeline dependency check completed.' });
      } else {
        await endpoints.actionQueue.checkCustomFields();
        setToast({ message: 'Custom field check completed.' });
      }
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Queue check failed.',
        tone: 'error',
      });
    } finally {
      setBusyKey(null);
    }
  };

  const runPipeline = async (dryRun: boolean) => {
    setBusyKey(dryRun ? 'dry-run' : 'run');
    try {
      await endpoints.actionQueue.run({ dry_run: dryRun, force: !dryRun });
      await loadData();
      setToast({ message: dryRun ? 'Dry run completed.' : 'Pipeline run completed.' });
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Pipeline run failed.',
        tone: 'error',
      });
    } finally {
      setBusyKey(null);
    }
  };

  const runBackfill = async (dryRun: boolean) => {
    setBusyKey(dryRun ? 'backfill-dry' : 'backfill');
    try {
      const result = await endpoints.actionQueue.backfill({ dry_run: dryRun }) as Record<string, unknown>;
      await loadData();
      if (dryRun) {
        const count = (result as { would_sync?: number }).would_sync ?? 0;
        setToast({ message: `Backfill preview: ${count} action(s) would be synced to Paperless.` });
      } else {
        const synced = (result as { synced?: number }).synced ?? 0;
        const failed = (result as { failed?: number }).failed ?? 0;
        const msg = failed > 0
          ? `Backfill complete: ${synced} synced, ${failed} failed.`
          : `Backfill complete: ${synced} action(s) synced to Paperless.`;
        setToast({ message: msg, tone: failed > 0 ? 'error' : 'success' });
      }
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Backfill failed.',
        tone: 'error',
      });
    } finally {
      setBusyKey(null);
    }
  };

  const updateAction = async (actionId: number, nextStatus: 'completed' | 'dismissed' | 'pending') => {
    setBusyKey(`action-${actionId}-${nextStatus}`);
    setProcessingIds(new Set([actionId]));
    try {
      // Pass version for optimistic locking to detect concurrent edits
      const currentAction = filteredActions.find((a) => a.id === actionId);
      const payload: Record<string, unknown> = { status: nextStatus, dry_run: false };
      if (currentAction?.version != null) {
        payload.version = currentAction.version;
      }
      await endpoints.actionQueue.updateAction(String(actionId), payload);
      await loadData();
      setToast({ message: `Action marked ${nextStatus}.` });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update action.';
      const isConflict = message.includes('409') || message.includes('version_conflict');
      if (isConflict) {
        await loadData();
        setToast({ message: 'Action was modified by another user. Refreshed.', tone: 'error' });
      } else {
        setToast({ message, tone: 'error' });
      }
    } finally {
      setBusyKey(null);
      setProcessingIds(new Set());
    }
  };

  // [ARCH-01] Bulk action handler
  const handleBulkAction = async (action: 'complete' | 'dismiss' | 'reopen') => {
    const ids = Array.from(checkedIds);
    if (ids.length === 0) return;

    // Destructive actions require confirmation
    if (action === 'dismiss') {
      setPendingBulkAction({ action, ids });
      return;
    }

    await executeBulkAction(action, ids);
  };

  const executeBulkAction = async (action: string, ids: number[]) => {
    setBusyKey(`bulk-${action}`);
    // [UX-10] Mark all affected items as processing
    setProcessingIds(new Set(ids));
    setBulkProgress({ done: 0, total: ids.length });
    try {
      const result = await endpoints.actionQueue.bulk({ action, action_ids: ids });
      setBulkProgress({ done: result.affected, total: ids.length });
      setCheckedIds(new Set());
      setToast({
        message: `${result.affected} action${result.affected !== 1 ? 's' : ''} ${action}${action.endsWith('e') ? 'd' : 'ed'}.`,
      });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : `Bulk ${action} failed.`, tone: 'error' });
    } finally {
      setBusyKey(null);
      setProcessingIds(new Set());
      setBulkProgress(null);
    }
  };

  const confirmPendingBulkAction = async () => {
    if (!pendingBulkAction) return;
    const { action, ids } = pendingBulkAction;
    setPendingBulkAction(null);
    await executeBulkAction(action, ids);
  };

  const handleClosePanel = useCallback(() => {
    setSelectedActionId(null);
    setCachedAction(null);
  }, []);

  const counts = status?.database ?? {};
  const progress = status?.progress;

  // [UX-06] Progress tracking: resolved today = completed + dismissed
  const resolvedToday = (counts.completed ?? 0) + (counts.dismissed ?? 0);
  const totalActions = counts.total ?? 0;
  const pendingCount = counts.pending ?? 0;
  const progressPct = totalActions > 0 ? Math.round((resolvedToday / totalActions) * 100) : 0;

  return (
    <>
      <PageHeader
        title="Action Queue"
        desc="Review pending action items, run the pipeline, and resolve follow-up work from Document Intelligence processing."
        actions={
          <div className="btn-group">
            <Button onClick={() => void runCheck('health')} disabled={busyKey !== null}>
              Check services
            </Button>
            <Button onClick={() => void runCheck('custom-fields')} disabled={busyKey !== null}>
              Check custom fields
            </Button>
            <Button variant="ghost" onClick={() => void runPipeline(true)} disabled={busyKey !== null}>
              Dry run
            </Button>
            <Button variant="primary" onClick={() => void runPipeline(false)} disabled={busyKey !== null}>
              Run pipeline
            </Button>
            <Button variant="ghost" onClick={() => void runBackfill(true)} disabled={busyKey !== null} title="Preview which actions would be re-synced to Paperless">
              Backfill preview
            </Button>
            <Button onClick={() => void runBackfill(false)} disabled={busyKey !== null} title="Re-write action metadata to Paperless for actions that were never synced">
              Backfill Paperless
            </Button>
          </div>
        }
      />

      <StatGrid>
        <StatCard title="Pending" metric={pendingCount} desc="Awaiting review or downstream completion." />
        <StatCard title="Completed" metric={counts.completed ?? 0} desc="Finished and written back." />
        <StatCard title="Dismissed" metric={counts.dismissed ?? 0} desc="Closed without further action." />
        <StatCard
          title="Pipeline status"
          metric={(status?.status ?? 'idle').toUpperCase()}
          desc={progress?.stage ? `${progress.stage}${progress.current_document ? ` · ${progress.current_document}` : ''}` : 'No active run'}
          status={{
            label: status?.read_only ? 'Read only' : 'Write enabled',
            tone: status?.read_only ? 'warning' : 'success',
          }}
        />
      </StatGrid>

      {/* [UX-06] Progress indicator */}
      {totalActions > 0 && (
        <div className="aq-progress-section">
          <div className="aq-progress-header">
            <span className="aq-progress-label">
              {pendingCount === 0
                ? '🎉 Inbox zero — all actions resolved!'
                : `${resolvedToday} of ${totalActions} resolved`}
            </span>
            <span className="aq-progress-pct">{progressPct}%</span>
          </div>
          <div className="aq-progress-track">
            <div
              className={`aq-progress-fill${pendingCount === 0 ? ' complete' : ''}`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      <div className="aq-toolbar">
        <FilterPills
          active={filter}
          onChange={(value) => setFilter(value as ActionFilter)}
          options={[
            { key: 'pending', label: `Pending (${pendingCount})` },
            { key: 'completed', label: `Completed (${counts.completed ?? 0})` },
            { key: 'dismissed', label: `Dismissed (${counts.dismissed ?? 0})` },
            { key: 'all', label: `All (${totalActions})` },
          ]}
        />
        <div className="aq-search">
          <input
            aria-label="Search actions"
            placeholder="Search actions, documents, or correspondents"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      </div>

      {/* [UX-10] Bulk progress counter */}
      {bulkProgress && (
        <div className="aq-bulk-progress" role="status" aria-live="polite">
          Processing {bulkProgress.done} of {bulkProgress.total}…
        </div>
      )}

      {loading ? (
        <><SkeletonLoader variant="stat-grid" /><div className="section"><SkeletonLoader variant="table" /></div></>
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <div className="action-queue-layout">
          <Card title={`Pending actions (${filteredActions.length})`} className="aq-table-card">
            {/* [ARCH-01] Bulk action bar */}
            {checkedIds.size > 0 && (
              <div className="aq-bulk-bar">
                <span className="aq-bulk-count">
                  {checkedIds.size} selected
                  {(() => {
                    const visibleIds = new Set(filteredActions.map((a) => a.id));
                    const hiddenCount = Array.from(checkedIds).filter((id) => !visibleIds.has(id)).length;
                    return hiddenCount > 0 ? ` (${hiddenCount} hidden by filter)` : '';
                  })()}
                </span>
                <div className="btn-group">
                  <Button variant="success" size="sm" onClick={() => void handleBulkAction('complete')} disabled={busyKey !== null}>
                    Complete
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => void handleBulkAction('dismiss')} disabled={busyKey !== null}>
                    Dismiss
                  </Button>
                  <Button size="sm" onClick={() => void handleBulkAction('reopen')} disabled={busyKey !== null}>
                    Re-open
                  </Button>
                  <Button size="sm" onClick={() => setCheckedIds(new Set())} disabled={busyKey !== null}>
                    Clear
                  </Button>
                </div>
              </div>
            )}

            {/* [ARCH-01] Select all toolbar */}
            {filteredActions.length > 0 && (
              <div className="aq-select-toolbar">
                <label className="aq-select-all">
                  <input
                    type="checkbox"
                    checked={filteredActions.length > 0 && filteredActions.every((a) => checkedIds.has(a.id))}
                    onChange={selectAllVisible}
                  />
                  <span>Select visible ({filteredActions.length})</span>
                </label>
              </div>
            )}

            {filteredActions.length === 0 ? (
              pendingCount === 0 && filter === 'pending' ? (
                /* [UX-06] Inbox zero celebration */
                <EmptyState
                  icon="🎉"
                  title="Inbox zero!"
                  desc="All actions have been resolved. Great job! Run the pipeline to discover new actions."
                />
              ) : (
                <EmptyState
                  title="No actions match this view"
                  desc="Try a different status filter or rerun the pipeline to discover new actions."
                />
              )
            ) : (
              <DataTable<ActionItem>
                rows={filteredActions}
                rowKey={(row) => String(row.id)}
                emptyLabel="No actions found"
                columns={[
                  {
                    key: 'select',
                    header: '',
                    render: (row) => (
                      <input
                        type="checkbox"
                        checked={checkedIds.has(row.id)}
                        onChange={(e) => toggleCheck(row.id, e.nativeEvent instanceof MouseEvent && e.nativeEvent.shiftKey)}
                        onClick={(e) => e.stopPropagation()}
                        aria-label={`Select action ${row.id}`}
                      />
                    ),
                    width: '40px',
                  },
                  {
                    key: 'item',
                    header: 'Action item',
                    render: (row) => {
                      const { tone } = dueMeta(row);
                      const isProcessing = processingIds.has(row.id);
                      return (
                        <button className="aq-link-button" onClick={() => setSelectedActionId(row.id)}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            {/* [UX-10] Inline spinner for processing items */}
                            {isProcessing ? (
                              <span className="aq-spinner" aria-label="Processing" />
                            ) : (
                              <span className={`aq-urgency-dot ${tone}`} />
                            )}
                            <div className={selectedActionId === row.id ? 'aq-row-title selected' : 'aq-row-title'}>
                              {row.title || row.document_title || `Action #${row.id}`}
                            </div>
                          </div>
                          <div className="text-muted">{row.correspondent || row.document_title || 'No document metadata'}</div>
                        </button>
                      );
                    },
                  },
                  {
                    key: 'type',
                    header: 'Type',
                    render: (row) => {
                      const type = normalizeType(row.action_type);
                      return <Badge tone={actionTypeTone(type)}>{type}</Badge>;
                    },
                    width: '110px',
                  },
                  {
                    key: 'due',
                    header: 'Due',
                    render: (row) => {
                      const meta = dueMeta(row);
                      return (
                        <div>
                          <div>{formatDate(row.due_date)}</div>
                          <div className={`aq-inline-note ${meta.tone}`}>{meta.label}</div>
                        </div>
                      );
                    },
                    width: '140px',
                  },
                  {
                    key: 'amount',
                    header: 'Amount',
                    render: (row) => formatCurrency(row.amount),
                    width: '110px',
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    render: (row) => {
                      const normalized = normalizeStatus(row.status);
                      return <Badge tone={statusTone(normalized)}>{normalized}</Badge>;
                    },
                    width: '110px',
                  },
                  {
                    key: 'actions',
                    header: 'Actions',
                    render: (row) => {
                      const normalized = normalizeStatus(row.status);
                      return (
                        <div className="btn-group">
                          {normalized !== 'completed' && (
                            <Button
                              size="sm"
                              variant="success"
                              onClick={() => void updateAction(row.id, 'completed')}
                              disabled={busyKey !== null}
                            >
                              Complete
                            </Button>
                          )}
                          {normalized !== 'dismissed' && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => void updateAction(row.id, 'dismissed')}
                              disabled={busyKey !== null}
                            >
                              Dismiss
                            </Button>
                          )}
                          {normalized !== 'pending' && (
                            <Button
                              size="sm"
                              onClick={() => void updateAction(row.id, 'pending')}
                              disabled={busyKey !== null}
                            >
                              Re-open
                            </Button>
                          )}
                        </div>
                      );
                    },
                    width: '220px',
                  },
                ]}
              />
            )}
          </Card>

          <div className="aq-side-column">
            {selectedAction ? (
              <Card
                title="Action detail"
                actions={
                  <Button variant="ghost" size="sm" onClick={handleClosePanel} title="Close panel">
                    ✕
                  </Button>
                }
              >
                <div className="aq-detail-list" role="region" aria-live="polite" aria-label="Action detail panel">
                  {/* [UX-07] Banner when selected item is not in the current filtered view */}
                  {!selectedInView && (
                    <div className="aq-out-of-view-banner" role="status">
                      ⚠ This item is not in the current view. Change filters to see it in the list.
                    </div>
                  )}

                  <div>
                    <div className="aq-detail-title">{selectedAction.title || selectedAction.document_title || `Action #${selectedAction.id}`}</div>
                    <div className="text-muted">{selectedAction.summary || 'No summary provided.'}</div>
                  </div>

                  <div className="aq-badge-row">
                    <Badge tone={actionTypeTone(normalizeType(selectedAction.action_type))}>
                      {normalizeType(selectedAction.action_type)}
                    </Badge>
                    <Badge tone={statusTone(normalizeStatus(selectedAction.status))}>
                      {normalizeStatus(selectedAction.status)}
                    </Badge>
                    <Badge tone={dueMeta(selectedAction).tone}>{dueMeta(selectedAction).label}</Badge>
                  </div>

                  {selectedAction.document_id ? (
                    <div className="aq-doc-preview-section">
                      {pdfExpanded ? (
                        <div className="aq-pdf-embed-wrapper">
                          <div className="aq-pdf-embed-toolbar">
                            <button className="aq-pdf-collapse-btn" onClick={() => setPdfExpanded(false)} title="Collapse PDF viewer">
                              ▾ Collapse
                            </button>
                            {selectedAction.preview_url && /^https?:\/\//i.test(selectedAction.preview_url) && (
                              <a href={selectedAction.preview_url} target="_blank" rel="noreferrer" className="aq-pdf-open-external">
                                Open in Paperless ↗
                              </a>
                            )}
                          </div>
                          <iframe
                            className="aq-pdf-embed"
                            src={endpoints.documents.download(String(selectedAction.document_id))}
                            title="Document PDF preview"
                          />
                        </div>
                      ) : (
                        <button
                          className="aq-thumb-preview"
                          onClick={() => setPdfExpanded(true)}
                          title="Click to expand PDF viewer"
                          aria-label="Expand document preview"
                        >
                          <img
                            className="aq-thumb-img"
                            src={endpoints.statements.documentThumb(String(selectedAction.document_id))}
                            alt={`Thumbnail of ${selectedAction.document_title || 'document'}`}
                            onError={(e) => {
                              (e.currentTarget as HTMLImageElement).style.display = 'none';
                              (e.currentTarget.nextElementSibling as HTMLElement)?.style.setProperty('display', 'flex');
                            }}
                          />
                          <div className="aq-thumb-fallback" style={{ display: 'none' }}>
                            <span className="aq-pdf-icon">📄</span>
                          </div>
                          <div className="aq-thumb-overlay">
                            <span className="aq-thumb-label">{selectedAction.correspondent || selectedAction.document_title || 'Document'}</span>
                            <span className="aq-thumb-hint">Click to preview PDF</span>
                          </div>
                        </button>
                      )}
                    </div>
                  ) : (
                    <div className="aq-pdf-preview">
                      <span className="aq-pdf-icon">📄</span>
                      <span className="aq-pdf-title">{selectedAction.correspondent || selectedAction.document_title || 'No document'}</span>
                      <span className="aq-pdf-hint">No preview available</span>
                    </div>
                  )}

                  <div className="aq-meta-list">
                    <div className="aq-meta-row"><span>Document</span><span>{selectedAction.document_title || `#${selectedAction.document_id ?? '—'}`}</span></div>
                    <div className="aq-meta-row"><span>Correspondent</span><span>{selectedAction.correspondent || '—'}</span></div>
                    <div className="aq-meta-row"><span>Amount</span><span>{formatCurrency(selectedAction.amount)}</span></div>
                    <div className="aq-meta-row"><span>Due date</span><span>{formatDate(selectedAction.due_date)}</span></div>
                    <div className="aq-meta-row"><span>Created</span><span>{formatDateTime(selectedAction.created_at)}</span></div>
                    <div className="aq-meta-row"><span>Completed</span><span>{formatDateTime(selectedAction.completed_at)}</span></div>
                  </div>

                  {selectedAction.confidence != null && (
                    <ConfidenceBar label="AI confidence" pct={selectedAction.confidence} />
                  )}

                  {selectedAction.risk_score != null && selectedAction.risk_score > 0 && (
                    <RiskScoreBar label="Risk score" score={selectedAction.risk_score} />
                  )}

                  {selectedAction.ai_reasoning && (
                    <div>
                      <div className="section-title">AI reasoning</div>
                      <div className="aq-reasoning">{selectedAction.ai_reasoning}</div>
                    </div>
                  )}

                  {(() => {
                    const currentStatus = normalizeStatus(selectedAction.status);
                    return (
                      <div className="btn-group">
                        {currentStatus !== 'completed' && (
                          <Button
                            variant="success"
                            onClick={() => void updateAction(selectedAction.id, 'completed')}
                            disabled={busyKey !== null}
                          >
                            Confirm
                          </Button>
                        )}
                        {currentStatus !== 'dismissed' && (
                          <Button
                            variant="danger"
                            onClick={() => void updateAction(selectedAction.id, 'dismissed')}
                            disabled={busyKey !== null}
                          >
                            Reject
                          </Button>
                        )}
                        {currentStatus !== 'pending' && (
                          <Button
                            variant="ghost"
                            onClick={() => void updateAction(selectedAction.id, 'pending')}
                            disabled={busyKey !== null}
                          >
                            Requeue
                          </Button>
                        )}
                      </div>
                    );
                  })()}
                </div>
              </Card>
            ) : (
              <EmptyState title="Select an action" desc="Pick an item from the queue to inspect its due date, status, and AI reasoning." />
            )}

            <Card title="Run health">
                {health ? (
                  <div className="aq-meta-list">
                    <div className="aq-meta-row">
                      <span>Queue</span>
                      <Badge tone={healthTone(health.status)}>{health.status ?? 'unknown'}</Badge>
                    </div>
                    <div className="aq-meta-row">
                      <span>Paperless</span>
                      <Badge tone={healthTone(health.paperless?.status)}>{health.paperless?.status ?? 'unknown'}</Badge>
                    </div>
                    <div className="aq-meta-row">
                      <span>Ollama</span>
                      <Badge tone={healthTone(health.ollama?.status)}>{health.ollama?.status ?? 'unknown'}</Badge>
                    </div>
                    <div className="aq-meta-row"><span>Model</span><span>{health.ollama?.model ?? '—'}</span></div>
                    <div className="aq-meta-row"><span>Mode</span><span>{health.read_only ? 'Read only' : 'Write enabled'}</span></div>
                  </div>
                ) : (
                  <EmptyState
                    icon="🩺"
                    title="No health check yet"
                    desc="Run Check services to verify Paperless and Ollama connectivity before starting the pipeline."
                  />
                )}
            </Card>
          </div>
        </div>
      )}

      {/* [ARCH-01] Confirmation modal for destructive bulk actions */}
      {pendingBulkAction && (
        <div className="aq-modal-overlay" onClick={() => setPendingBulkAction(null)}>
          <div className="aq-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="aq-modal-title">
              Confirm bulk {pendingBulkAction.action}
            </div>
            <p className="aq-modal-desc">
              Are you sure you want to <strong>{pendingBulkAction.action}</strong>{' '}
              {pendingBulkAction.ids.length} action{pendingBulkAction.ids.length !== 1 ? 's' : ''}?
              This action cannot be easily undone in bulk.
            </p>
            <div className="aq-modal-actions">
              <Button size="sm" onClick={() => setPendingBulkAction(null)}>Cancel</Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => void confirmPendingBulkAction()}
              >
                {pendingBulkAction.action === 'dismiss' ? 'Dismiss' : pendingBulkAction.action} {pendingBulkAction.ids.length} actions
              </Button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </>
  );
}
