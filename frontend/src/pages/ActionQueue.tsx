import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import {
  Badge,
  Button,
  Card,
  ConfidenceBar,
  EmptyState,
  ErrorState,
  FilterPills,
  PageHeader,
  ProgressBanner,
  RiskScoreBar,
  SkeletonLoader,
  StatCard,
  StatGrid,
  Toast,
  Tooltip,
} from '../components/ui';
import { SortableTable, type SortableColumnDef } from '../components/SortableTable';
import DocumentViewerModal from '../components/DocumentViewerModal';
import { useStreamingAction } from '../hooks/useStreamingAction';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/action-queue.css';
import '../styles/sortable-table.css';
import { buildQueueRunBody } from './actionQueueRunBody';

interface ActionQueueCheck {
  status?: string;
  read_only?: boolean;
  paperless?: { status?: string };
  ollama?: { status?: string; model?: string; base_url?: string };
}

interface QueueDatabaseCounts {
  pending?: number;
  acknowledged?: number;
  completed?: number;
  dismissed?: number;
  snoozed?: number;
  not_an_action?: number;
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
  acknowledged_at?: string | null;
  snoozed_until?: string | null;
  severity?: string | null;
  recommended_cta?: { id: string; label: string; url?: string | null; phone?: string | null } | null;
}

interface ActionListResponse {
  actions?: ActionItem[];
  total?: number;
}

type ActionFilter = 'pending' | 'acknowledged' | 'completed' | 'dismissed' | 'snoozed' | 'not_an_action' | 'all';
type ActionStatus = 'completed' | 'dismissed' | 'pending' | 'acknowledged' | 'snoozed' | 'not_an_action';
type ActionEditDraft = {
  action_type: string;
  title: string;
  summary: string;
  due_date: string;
  amount: string;
  urgency: string;
  correspondent: string;
};
type ToastState = { message: string; tone?: 'success' | 'error' } | null;

const ACTION_TYPE_OPTIONS = ['ARCHIVE', 'FILE', 'PAY', 'RESPOND', 'REVIEW', 'SCHEDULE', 'SHARE', 'SIGN', 'TASK'] as const;
const URGENCY_OPTIONS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const;

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

function dateInputValue(value?: string | null) {
  return value ? value.slice(0, 10) : '';
}

function buildEditDraft(action: ActionItem): ActionEditDraft {
  return {
    action_type: normalizeType(action.action_type),
    title: action.title ?? '',
    summary: action.summary ?? '',
    due_date: dateInputValue(action.due_date),
    amount: action.amount == null ? '' : String(action.amount),
    urgency: action.urgency ?? 'LOW',
    correspondent: action.correspondent ?? '',
  };
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
    case 'TASK':
      return 'info' as const;
    case 'CANCEL':
      return 'warning' as const;
    case 'RENEW':
      return 'success' as const;
    case 'DISPUTE':
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
    case 'acknowledged':
      return 'info' as const;
    case 'snoozed':
      return 'warning' as const;
    case 'not_an_action':
      return 'danger' as const;
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
  const [drawerExpanded, setDrawerExpanded] = useState(false);
  const [pdfViewerOpen, setPdfViewerOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);

  // [UX-STREAM] Pipeline streaming progress
  const [pipelineState, runPipelineStream, cancelPipeline] = useStreamingAction();

  // [ARCH-01] Bulk selection state
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
  const lastCheckedRef = useRef<number | null>(null);
  const [pendingBulkAction, setPendingBulkAction] = useState<{ action: string; ids: number[] } | null>(null);

  // [UX-10] Per-item loading state for bulk ops
  const [processingIds, setProcessingIds] = useState<Set<number>>(new Set());
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);

  // [UX-07] Cache selected action so filter changes don't lose it
  const [cachedAction, setCachedAction] = useState<ActionItem | null>(null);
  const [isEditingDetails, setIsEditingDetails] = useState(false);
  const [editDraft, setEditDraft] = useState<ActionEditDraft | null>(null);

  // Custom run panel state
  const [customRunOpen, setCustomRunOpen] = useState(false);
  const [customRunMode, setCustomRunMode] = useState<'defaults' | 'custom'>('defaults');
  const [customRunFilters, setCustomRunFilters] = useState<{
    tag_override: string;
    saved_view_id: string;
    correspondent: string;
    document_type: string;
    created_after: string;
    created_before: string;
    added_after: string;
    added_before: string;
    document_id: string;
    limit: string;
  }>({
    tag_override: '',
    saved_view_id: '',
    correspondent: '',
    document_type: '',
    created_after: '',
    created_before: '',
    added_after: '',
    added_before: '',
    document_id: '',
    limit: '',
  });
  const [customRunMetadata, setCustomRunMetadata] = useState<{
    saved_views: Array<{ id: number; name: string }>;
    correspondents: Array<{ id: number; name: string }>;
    document_types: Array<{ id: number; name: string }>;
    loaded: boolean;
  }>({ saved_views: [], correspondents: [], document_types: [], loaded: false });

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

  // Show pipeline streaming errors as toasts and clear busy state
  useEffect(() => {
    if (pipelineState.error) {
      setToast({ message: pipelineState.error, tone: 'error' });
      setBusyKey(null);
    }
  }, [pipelineState.error]);

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
    setPdfViewerOpen(false);
  }, [selectedActionId]);

  useEffect(() => {
    setIsEditingDetails(false);
  }, [selectedActionId]);

  // Lock body scroll when drawer is open
  useEffect(() => {
    if (selectedAction) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [selectedAction]);

  useEffect(() => {
    if (!selectedAction) {
      setEditDraft(null);
      return;
    }
    if (!isEditingDetails) {
      setEditDraft(buildEditDraft(selectedAction));
    }
  }, [selectedAction, isEditingDetails]);

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

  const runPipeline = (dryRun: boolean) => {
    setBusyKey(dryRun ? 'dry-run' : 'run');
    runPipelineStream(
      endpoints.actionQueue.runStreamUrl,
      () => {
        setToast({ message: dryRun ? 'Dry run completed.' : 'Pipeline run completed.' });
        setBusyKey(null);
        void loadData();
      },
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildQueueRunBody(dryRun)),
      },
    );
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

  const loadCustomRunMetadata = async () => {
    if (customRunMetadata.loaded) return;
    try {
      const [views, correspondents, docTypes] = await Promise.all([
        endpoints.actionQueue.metadataSavedViews() as Promise<{ saved_views: Array<{ id: number; name: string }> }>,
        endpoints.actionQueue.metadataCorrespondents() as Promise<{ correspondents: Array<{ id: number; name: string }> }>,
        endpoints.actionQueue.metadataDocumentTypes() as Promise<{ document_types: Array<{ id: number; name: string }> }>,
      ]);
      setCustomRunMetadata({
        saved_views: views.saved_views ?? [],
        correspondents: correspondents.correspondents ?? [],
        document_types: docTypes.document_types ?? [],
        loaded: true,
      });
    } catch {
      // Metadata load failure is non-blocking
    }
  };

  const runCustomPipeline = async (dryRun: boolean) => {
    setBusyKey(dryRun ? 'custom-dry-run' : 'custom-run');
    try {
      const body: Record<string, unknown> = buildQueueRunBody(
        dryRun,
        customRunFilters.document_id,
      );
      if (customRunMode === 'custom') {
        if (customRunFilters.tag_override) body.tag_override = customRunFilters.tag_override;
        if (customRunFilters.saved_view_id) body.saved_view_id = Number(customRunFilters.saved_view_id);
        if (customRunFilters.document_id) body.document_id = Number(customRunFilters.document_id);
        if (customRunFilters.created_after) body.created_after = customRunFilters.created_after;
        if (customRunFilters.created_before) body.created_before = customRunFilters.created_before;
        if (customRunFilters.added_after) body.added_after = customRunFilters.added_after;
        if (customRunFilters.added_before) body.added_before = customRunFilters.added_before;
        if (customRunFilters.correspondent) body.correspondent = customRunFilters.correspondent;
        if (customRunFilters.document_type) body.document_type = customRunFilters.document_type;
      }
      if (customRunFilters.limit) body.limit = Number(customRunFilters.limit);
      await endpoints.actionQueue.run(body);
      await loadData();
      setToast({ message: dryRun ? 'Custom dry run completed.' : 'Custom pipeline run completed.' });
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Custom pipeline run failed.',
        tone: 'error',
      });
    } finally {
      setBusyKey(null);
    }
  };

  const patchAction = async (
    actionId: number,
    payload: Record<string, unknown>,
    successMessage: string,
    onSuccess?: () => void,
  ) => {
    setBusyKey(`action-${actionId}`);
    setProcessingIds(new Set([actionId]));
    try {
      const updatedAction = await endpoints.actionQueue.updateAction(String(actionId), payload) as ActionItem;
      if (selectedActionId === actionId) {
        setCachedAction(updatedAction);
      }
      await loadData();
      onSuccess?.();
      setToast({ message: successMessage });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update action.';
      const isConflict = message.includes('409') || message.includes('version_conflict');
      if (isConflict) {
        setIsEditingDetails(false);
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

  const updateAction = async (actionId: number, nextStatus: ActionStatus, snoozedUntil?: string) => {
    setBusyKey(`action-${actionId}-${nextStatus}`);
    const currentAction = filteredActions.find((a) => a.id === actionId) ?? cachedAction;
    const payload: Record<string, unknown> = { status: nextStatus, dry_run: false };
    if (currentAction?.version != null) {
      payload.version = currentAction.version;
    }
    if (nextStatus === 'snoozed' && snoozedUntil) {
      payload.snoozed_until = snoozedUntil;
    }
    const statusLabel = nextStatus === 'not_an_action' ? 'not an action' : nextStatus;
    await patchAction(actionId, payload, `Action marked ${statusLabel}.`);
  };

  const quickChangeType = async (actionId: number, newType: string) => {
    const action = filteredActions.find((a) => a.id === actionId) ?? cachedAction;
    const payload: Record<string, unknown> = { action_type: newType };
    if (action?.version != null) payload.version = action.version;
    await patchAction(actionId, payload, `Action type changed to ${newType}.`);
  };

  const saveActionDetails = async () => {
    if (!selectedAction || !editDraft) return;

    if (!editDraft.title.trim()) {
      setToast({ message: 'Task name is required.', tone: 'error' });
      return;
    }

    const payload: Record<string, unknown> = {};
    const currentVersion = selectedAction.version;
    if (currentVersion != null) {
      payload.version = currentVersion;
    }

    if (normalizeType(selectedAction.action_type) !== editDraft.action_type) {
      payload.action_type = editDraft.action_type;
    }
    if ((selectedAction.title ?? '') !== editDraft.title.trim()) {
      payload.title = editDraft.title.trim();
    }
    if ((selectedAction.summary ?? '') !== editDraft.summary.trim()) {
      payload.summary = editDraft.summary.trim() || null;
    }
    if (dateInputValue(selectedAction.due_date) !== editDraft.due_date) {
      payload.due_date = editDraft.due_date || null;
    }
    if ((selectedAction.amount == null ? '' : String(selectedAction.amount)) !== editDraft.amount.trim()) {
      payload.amount = editDraft.amount.trim() ? Number(editDraft.amount) : null;
    }
    if ((selectedAction.urgency ?? 'LOW') !== editDraft.urgency) {
      payload.urgency = editDraft.urgency;
    }
    if ((selectedAction.correspondent ?? '') !== editDraft.correspondent.trim()) {
      payload.correspondent = editDraft.correspondent.trim() || null;
    }

    if (Object.keys(payload).length === (currentVersion != null ? 1 : 0)) {
      setIsEditingDetails(false);
      setToast({ message: 'No detail changes to save.' });
      return;
    }

    await patchAction(selectedAction.id, payload, 'Action details updated.', () => {
      setIsEditingDetails(false);
    });
  };

  // [ARCH-01] Bulk action handler
  const handleBulkAction = async (action: 'complete' | 'dismiss' | 'reopen' | 'acknowledge' | 'snooze') => {
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

  const submitFeedback = async (actionId: number, feedbackType: string) => {
    setBusyKey(`feedback-${actionId}`);
    try {
      await endpoints.actionQueue.feedback(String(actionId), { feedback_type: feedbackType });
      await loadData();
      const label = feedbackType === 'not_an_action' ? 'Marked as not an action (feedback recorded).' : 'Feedback submitted.';
      setToast({ message: label });
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to submit feedback.', tone: 'error' });
    } finally {
      setBusyKey(null);
    }
  };

  const handleClosePanel = useCallback(() => {
    setSelectedActionId(null);
    setCachedAction(null);
    setDrawerExpanded(false);
  }, []);

  const counts = status?.database ?? {};
  const progress = status?.progress;

  // [UX-06] Progress tracking: resolved today = completed + dismissed + not_an_action
  const resolvedToday = (counts.completed ?? 0) + (counts.dismissed ?? 0) + (counts.not_an_action ?? 0);
  const totalActions = counts.total ?? 0;
  const pendingCount = (counts.pending ?? 0) + (counts.acknowledged ?? 0) + (counts.snoozed ?? 0);
  const progressPct = totalActions > 0 ? Math.round((resolvedToday / totalActions) * 100) : 0;

  // Derive unique action type options for filtering
  const actionTypeOptions = useMemo(() => {
    const types = new Set(actions.map((a) => normalizeType(a.action_type)));
    return Array.from(types).sort().map((t) => ({ value: t, label: t }));
  }, [actions]);

  const statusOptions = useMemo(() => {
    const statuses = new Set(actions.map((a) => normalizeStatus(a.status)));
    return Array.from(statuses).sort().map((s) => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1) }));
  }, [actions]);

  const urgencyOptions = useMemo(() => {
    const tones = new Set(actions.map((a) => dueMeta(a).tone));
    const labelMap: Record<string, string> = { danger: 'Overdue / Due soon', warning: 'Due this week', success: 'Not urgent', muted: 'No due date' };
    return Array.from(tones).map((t) => ({ value: t, label: labelMap[t] ?? t }));
  }, [actions]);

  // Column definitions for sortable table
  const actionTableColumns: SortableColumnDef<ActionItem>[] = useMemo(
    () => [
      {
        id: 'select',
        header: '',
        cell: (row) => (
          <input
            type="checkbox"
            checked={checkedIds.has(row.id)}
            onChange={(e) => toggleCheck(row.id, e.nativeEvent instanceof MouseEvent && e.nativeEvent.shiftKey)}
            onClick={(e) => e.stopPropagation()}
            aria-label={`Select action ${row.id}`}
          />
        ),
        enableSorting: false,
        width: '40px',
      },
      {
        id: 'item',
        header: 'Action item',
        accessorFn: (row) => (row.title || row.document_title || '').toLowerCase(),
        cell: (row) => {
          const { tone } = dueMeta(row);
          const isProcessing = processingIds.has(row.id);
          return (
            <button className="aq-link-button" onClick={() => setSelectedActionId(row.id)}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
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
        id: 'type',
        header: 'Type',
        accessorFn: (row) => normalizeType(row.action_type),
        cell: (row) => {
          const type = normalizeType(row.action_type);
          return <Badge tone={actionTypeTone(type)}>{type}</Badge>;
        },
        filterOptions: actionTypeOptions,
        width: '110px',
      },
      {
        id: 'due',
        header: 'Due',
        accessorFn: (row) => row.due_date ?? '',
        cell: (row) => {
          const meta = dueMeta(row);
          return (
            <div>
              <div>{formatDate(row.due_date)}</div>
              <div className={`aq-inline-note ${meta.tone}`}>{meta.label}</div>
            </div>
          );
        },
        filterOptions: urgencyOptions,
        filterFn: (rowValue, filterValue) => {
          // Filter by urgency tone derived from due_date
          const dueStr = rowValue as string;
          const due = dueStr ? new Date(dueStr) : null;
          if (!due || Number.isNaN(due.getTime())) return filterValue === 'muted';
          const days = Math.ceil((due.getTime() - Date.now()) / 86400000);
          const tone = days < 0 ? 'danger' : days <= 3 ? 'danger' : days <= 7 ? 'warning' : 'success';
          return tone === filterValue;
        },
        width: '140px',
      },
      {
        id: 'amount',
        header: 'Amount',
        accessorFn: (row) => row.amount ?? 0,
        cell: (row) => formatCurrency(row.amount),
        width: '110px',
      },
      {
        id: 'status',
        header: 'Status',
        accessorFn: (row) => normalizeStatus(row.status),
        cell: (row) => {
          const normalized = normalizeStatus(row.status);
          return <Badge tone={statusTone(normalized)}>{normalized}</Badge>;
        },
        filterOptions: statusOptions,
        width: '110px',
      },
      {
        id: 'actions',
        header: 'Actions',
        enableSorting: false,
        cell: (row) => {
          const normalized = normalizeStatus(row.status);
          return (
            <div className="btn-group">
              {normalized === 'pending' && (
                <Tooltip label="Mark as seen — you'll handle it later">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void updateAction(row.id, 'acknowledged')}
                  disabled={busyKey !== null}
                >
                  Acknowledge
                </Button>
                </Tooltip>
              )}
              {normalized !== 'completed' && (
                <Tooltip label="Mark as done — action has been resolved">
                <Button
                  size="sm"
                  variant="success"
                  onClick={() => void updateAction(row.id, 'completed')}
                  disabled={busyKey !== null}
                >
                  Complete
                </Button>
                </Tooltip>
              )}
              {normalized !== 'dismissed' && normalized !== 'not_an_action' && (
                <Tooltip label="Close without acting — removes from active queue">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void updateAction(row.id, 'dismissed')}
                  disabled={busyKey !== null}
                >
                  Dismiss
                </Button>
                </Tooltip>
              )}
              {normalized !== 'pending' && (
                <Tooltip label="Move back to pending for review">
                <Button
                  size="sm"
                  onClick={() => void updateAction(row.id, 'pending')}
                  disabled={busyKey !== null}
                >
                  Re-open
                </Button>
                </Tooltip>
              )}
            </div>
          );
        },
        width: '280px',
      },
    ],
    [checkedIds, selectedActionId, processingIds, busyKey, actionTypeOptions, statusOptions, urgencyOptions],
  );

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
            <Button variant="ghost" onClick={() => runPipeline(true)} disabled={busyKey !== null || pipelineState.running}>
              {busyKey === 'dry-run' ? 'Running…' : 'Dry run'}
            </Button>
            <Button variant="primary" onClick={() => runPipeline(false)} disabled={busyKey !== null || pipelineState.running}>
              {busyKey === 'run' ? 'Running…' : 'Run pipeline'}
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
        <StatCard title="Pending" metric={counts.pending ?? 0} desc="Awaiting review or downstream completion." />
        <StatCard title="Acknowledged" metric={counts.acknowledged ?? 0} desc="Seen, will handle later." />
        <StatCard title="Snoozed" metric={counts.snoozed ?? 0} desc="Deferred, will resurface." />
        <StatCard title="Completed" metric={counts.completed ?? 0} desc="Finished and written back." />
        <StatCard title="Dismissed" metric={counts.dismissed ?? 0} desc="Closed without further action." />
        <StatCard
          title="Pipeline status"
          metric={pipelineState.running ? 'RUNNING' : (status?.status ?? 'idle').toUpperCase()}
          desc={
            pipelineState.running && pipelineState.progress
              ? `${pipelineState.progress.stage}${pipelineState.progress.message ? ` · ${pipelineState.progress.message}` : ''}`
              : progress?.stage ? `${progress.stage}${progress.current_document ? ` · ${progress.current_document}` : ''}` : 'No active run'
          }
          status={{
            label: status?.read_only ? 'Read only' : 'Write enabled',
            tone: status?.read_only ? 'warning' : 'success',
          }}
        />
      </StatGrid>

      {pipelineState.running && pipelineState.progress && (
        <ProgressBanner
          stage={pipelineState.progress.stage}
          message={pipelineState.progress.message}
          current={pipelineState.progress.current}
          total={pipelineState.progress.total}
          onCancel={() => { cancelPipeline(); setBusyKey(null); setToast({ message: 'Disconnected from pipeline stream. The run continues in the background.' }); }}
        />
      )}

      {/* Custom Run Panel */}
      <div style={{ margin: '16px 0' }}>
        <details
          open={customRunOpen}
          onToggle={(e) => {
            const open = (e.target as HTMLDetailsElement).open;
            setCustomRunOpen(open);
            if (open) void loadCustomRunMetadata();
          }}
        >
          <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: '0.9rem', padding: '8px 0' }}>
            ▸ Custom Run (one-off with filters)
          </summary>
          <Card title="Run with custom filters">
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', gap: 16, marginBottom: 12 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <input
                    type="radio"
                    name="custom-run-mode"
                    value="defaults"
                    checked={customRunMode === 'defaults'}
                    onChange={() => setCustomRunMode('defaults')}
                    style={{ width: 'auto' }}
                  />
                  Use configured defaults
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <input
                    type="radio"
                    name="custom-run-mode"
                    value="custom"
                    checked={customRunMode === 'custom'}
                    onChange={() => setCustomRunMode('custom')}
                    style={{ width: 'auto' }}
                  />
                  Custom filters
                </label>
              </div>

              {customRunMode === 'custom' && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
                  <div className="form-group">
                    <label htmlFor="cr-tag-override">Tags override</label>
                    <input
                      id="cr-tag-override"
                      placeholder="e.g. Inbox,Bills"
                      value={customRunFilters.tag_override}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, tag_override: e.target.value }))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-saved-view">Saved view</label>
                    <select
                      id="cr-saved-view"
                      value={customRunFilters.saved_view_id}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, saved_view_id: e.target.value }))}
                    >
                      <option value="">None</option>
                      {customRunMetadata.saved_views.map((v) => (
                        <option key={v.id} value={v.id}>{v.name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-correspondent">Correspondent</label>
                    <select
                      id="cr-correspondent"
                      value={customRunFilters.correspondent}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, correspondent: e.target.value }))}
                    >
                      <option value="">Any</option>
                      {customRunMetadata.correspondents.map((c) => (
                        <option key={c.id} value={c.name}>{c.name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-doc-type">Document type</label>
                    <select
                      id="cr-doc-type"
                      value={customRunFilters.document_type}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, document_type: e.target.value }))}
                    >
                      <option value="">Any</option>
                      {customRunMetadata.document_types.map((t) => (
                        <option key={t.id} value={t.name}>{t.name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-created-after">Created after</label>
                    <input
                      id="cr-created-after"
                      type="date"
                      value={customRunFilters.created_after}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, created_after: e.target.value }))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-created-before">Created before</label>
                    <input
                      id="cr-created-before"
                      type="date"
                      value={customRunFilters.created_before}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, created_before: e.target.value }))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-added-after">Added after</label>
                    <input
                      id="cr-added-after"
                      type="date"
                      value={customRunFilters.added_after}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, added_after: e.target.value }))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-added-before">Added before</label>
                    <input
                      id="cr-added-before"
                      type="date"
                      value={customRunFilters.added_before}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, added_before: e.target.value }))}
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="cr-doc-id">Document ID (specific)</label>
                    <input
                      id="cr-doc-id"
                      type="number"
                      placeholder="e.g. 1234"
                      value={customRunFilters.document_id}
                      onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, document_id: e.target.value }))}
                    />
                  </div>
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <div className="form-group" style={{ margin: 0, width: 120 }}>
                  <label htmlFor="cr-limit" style={{ fontSize: '0.78rem' }}>Limit</label>
                  <input
                    id="cr-limit"
                    type="number"
                    min={1}
                    max={500}
                    placeholder="No limit"
                    value={customRunFilters.limit}
                    onChange={(e) => setCustomRunFilters((prev) => ({ ...prev, limit: e.target.value }))}
                  />
                </div>
                <div className="btn-group" style={{ marginTop: 16 }}>
                  <Button variant="ghost" onClick={() => void runCustomPipeline(true)} disabled={busyKey !== null}>
                    Dry run
                  </Button>
                  <Button variant="primary" onClick={() => void runCustomPipeline(false)} disabled={busyKey !== null}>
                    Run now
                  </Button>
                </div>
              </div>
            </div>
          </Card>
        </details>
      </div>

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
            { key: 'acknowledged', label: `Acknowledged (${counts.acknowledged ?? 0})` },
            { key: 'snoozed', label: `Snoozed (${counts.snoozed ?? 0})` },
            { key: 'completed', label: `Completed (${counts.completed ?? 0})` },
            { key: 'dismissed', label: `Dismissed (${counts.dismissed ?? 0})` },
            { key: 'not_an_action', label: `Not an action (${counts.not_an_action ?? 0})` },
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
          <Card title={`Pending actions (${filteredActions.length})`} className="aq-table-card">            {/* [ARCH-01] Bulk action bar */}
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
                  <Tooltip label="Mark as seen — you'll handle them later">
                  <Button variant="ghost" size="sm" onClick={() => void handleBulkAction('acknowledge')} disabled={busyKey !== null}>
                    Acknowledge
                  </Button>
                  </Tooltip>
                  <Tooltip label="Mark as done — actions have been resolved">
                  <Button variant="success" size="sm" onClick={() => void handleBulkAction('complete')} disabled={busyKey !== null}>
                    Complete
                  </Button>
                  </Tooltip>
                  <Tooltip label="Close without acting — removes from active queue">
                  <Button variant="ghost" size="sm" onClick={() => void handleBulkAction('dismiss')} disabled={busyKey !== null}>
                    Dismiss
                  </Button>
                  </Tooltip>
                  <Tooltip label="Move back to pending for review">
                  <Button size="sm" onClick={() => void handleBulkAction('reopen')} disabled={busyKey !== null}>
                    Re-open
                  </Button>
                  </Tooltip>
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
              <SortableTable<ActionItem>
                data={filteredActions}
                rowKey={(row) => String(row.id)}
                emptyLabel="No actions found"
                columns={actionTableColumns}
              />
            )}
          </Card>

          <div className="aq-side-column">
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

      {/* Slide-over drawer for action detail */}
      <div
        className={`aq-drawer-backdrop${selectedAction ? ' open' : ''}`}
        onClick={handleClosePanel}
      />
      <div
        className={`aq-drawer${selectedAction ? ' open' : ''}${drawerExpanded ? ' expanded' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="Action detail"
      >
        {selectedAction && (
          <>
            <div className="aq-drawer-header">
              <h3>Action detail</h3>
              <div className="aq-drawer-header-actions">
                {selectedAction.document_id && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      const url = endpoints.documents.previewUrl(selectedAction.document_id!);
                      window.open(url, '_blank', 'noopener,noreferrer');
                    }}
                    title="Pop out preview in new window"
                  >
                    ↗
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setDrawerExpanded((v) => !v)}
                  title={drawerExpanded ? 'Collapse panel' : 'Expand panel'}
                >
                  {drawerExpanded ? '⇥' : '⇤'}
                </Button>
                <Button variant="ghost" size="sm" onClick={handleClosePanel} title="Close panel">
                  ✕
                </Button>
              </div>
            </div>
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
                <Popover.Root>
                  <Tooltip label="Click to change action type">
                  <Popover.Trigger asChild>
                    <button className="aq-type-picker-trigger" disabled={busyKey !== null}>
                      <Badge tone={actionTypeTone(normalizeType(selectedAction.action_type))}>
                        {normalizeType(selectedAction.action_type)} ▾
                      </Badge>
                    </button>
                  </Popover.Trigger>
                  </Tooltip>
                  <Popover.Portal>
                    <Popover.Content className="aq-type-picker-popover" sideOffset={5} align="start">
                      <div className="aq-type-picker-label">Change action type</div>
                      <div className="aq-type-picker-grid">
                        {ACTION_TYPE_OPTIONS.map((type) => (
                          <Popover.Close asChild key={type}>
                            <button
                              className={`aq-type-picker-option ${normalizeType(selectedAction.action_type) === type ? 'active' : ''}`}
                              onClick={() => void quickChangeType(selectedAction.id, type)}
                            >
                              <Badge tone={actionTypeTone(type)}>{type}</Badge>
                            </button>
                          </Popover.Close>
                        ))}
                      </div>
                      <Popover.Arrow className="aq-type-picker-arrow" />
                    </Popover.Content>
                  </Popover.Portal>
                </Popover.Root>
                <Badge tone={statusTone(normalizeStatus(selectedAction.status))}>
                  {normalizeStatus(selectedAction.status)}
                </Badge>
                <Badge tone={dueMeta(selectedAction).tone}>{dueMeta(selectedAction).label}</Badge>
              </div>

              <div className="aq-edit-section">
                <div className="aq-edit-header">
                  <div className="section-title">Correct extracted details</div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (isEditingDetails) {
                        setEditDraft(buildEditDraft(selectedAction));
                        setIsEditingDetails(false);
                      } else {
                        setEditDraft(buildEditDraft(selectedAction));
                        setIsEditingDetails(true);
                      }
                    }}
                    disabled={busyKey !== null}
                  >
                    {isEditingDetails ? 'Cancel' : 'Edit details'}
                  </Button>
                </div>

                {isEditingDetails && editDraft && (
                  <div className="aq-edit-grid">
                    <label className="aq-edit-field">
                      <span>Action type</span>
                      <select
                        aria-label="Action type"
                        value={editDraft.action_type}
                        onChange={(e) => setEditDraft((prev) => (prev ? { ...prev, action_type: e.target.value } : prev))}
                      >
                        {ACTION_TYPE_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="aq-edit-field">
                      <span>Task name</span>
                      <input
                        aria-label="Task name"
                        value={editDraft.title}
                        onChange={(e) => setEditDraft((prev) => (prev ? { ...prev, title: e.target.value } : prev))}
                      />
                    </label>
                    <label className="aq-edit-field aq-edit-field-full">
                      <span>Summary</span>
                      <textarea
                        aria-label="Summary"
                        rows={3}
                        value={editDraft.summary}
                        onChange={(e) => setEditDraft((prev) => (prev ? { ...prev, summary: e.target.value } : prev))}
                      />
                    </label>
                    <label className="aq-edit-field">
                      <span>Due date</span>
                      <input
                        aria-label="Due date"
                        type="date"
                        value={editDraft.due_date}
                        onChange={(e) => setEditDraft((prev) => (prev ? { ...prev, due_date: e.target.value } : prev))}
                      />
                    </label>
                    <label className="aq-edit-field">
                      <span>Amount</span>
                      <input
                        aria-label="Amount"
                        type="number"
                        step="0.01"
                        value={editDraft.amount}
                        onChange={(e) => setEditDraft((prev) => (prev ? { ...prev, amount: e.target.value } : prev))}
                      />
                    </label>
                    <label className="aq-edit-field">
                      <span>Urgency</span>
                      <select
                        aria-label="Urgency"
                        value={editDraft.urgency}
                        onChange={(e) => setEditDraft((prev) => (prev ? { ...prev, urgency: e.target.value } : prev))}
                      >
                        {URGENCY_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="aq-edit-field">
                      <span>Correspondent</span>
                      <input
                        aria-label="Correspondent"
                        value={editDraft.correspondent}
                        onChange={(e) => setEditDraft((prev) => (prev ? { ...prev, correspondent: e.target.value } : prev))}
                      />
                    </label>
                    <div className="aq-edit-actions">
                      <Button variant="success" size="sm" onClick={() => void saveActionDetails()} disabled={busyKey !== null}>
                        Save details
                      </Button>
                    </div>
                  </div>
                )}
              </div>

              {selectedAction.document_id ? (
                <div className="aq-doc-preview-section">
                  <button
                    className="aq-thumb-preview"
                    onClick={() => setPdfViewerOpen(true)}
                    title="Click to preview PDF"
                    aria-label="Preview document"
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
                  {pdfViewerOpen && (
                    <DocumentViewerModal
                      documentId={selectedAction.document_id}
                      title={selectedAction.document_title || selectedAction.correspondent || `Document #${selectedAction.document_id}`}
                      paperlessUrl={selectedAction.preview_url}
                      onClose={() => setPdfViewerOpen(false)}
                    />
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
                <div className="aq-meta-row"><span>Severity</span><span><Badge tone={selectedAction.severity === 'critical' ? 'danger' : selectedAction.severity === 'focus' ? 'warning' : 'success'}>{selectedAction.severity ?? 'safe'}</Badge></span></div>
                <div className="aq-meta-row"><span>Created</span><span>{formatDateTime(selectedAction.created_at)}</span></div>
                {selectedAction.completed_at && <div className="aq-meta-row"><span>Completed</span><span>{formatDateTime(selectedAction.completed_at)}</span></div>}
                {selectedAction.acknowledged_at && <div className="aq-meta-row"><span>Acknowledged</span><span>{formatDateTime(selectedAction.acknowledged_at)}</span></div>}
                {selectedAction.snoozed_until && <div className="aq-meta-row"><span>Snoozed until</span><span>{formatDateTime(selectedAction.snoozed_until)}</span></div>}
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
                  <div className="aq-detail-actions">
                    {/* Recommended CTA — primary action derived from document intelligence */}
                    {selectedAction.recommended_cta && (
                      <div className="aq-cta-section">
                        <div className="section-title">Recommended action</div>
                        <Button
                          variant="primary"
                          onClick={() => {
                            if (selectedAction.recommended_cta?.url) {
                              window.open(selectedAction.recommended_cta.url, '_blank', 'noopener');
                            } else if (selectedAction.recommended_cta?.phone) {
                              window.open(`tel:${selectedAction.recommended_cta.phone}`);
                            }
                          }}
                          disabled={!selectedAction.recommended_cta.url && !selectedAction.recommended_cta.phone}
                        >
                          {selectedAction.recommended_cta.label}
                          {selectedAction.recommended_cta.url && ' ↗'}
                          {selectedAction.recommended_cta.phone && ` (${selectedAction.recommended_cta.phone})`}
                        </Button>
                      </div>
                    )}

                    {/* Lifecycle transition buttons */}
                    <div className="btn-group">
                      {currentStatus === 'pending' && (
                        <Tooltip label="Mark as seen — you'll handle it later">
                        <Button
                          variant="ghost"
                          onClick={() => void updateAction(selectedAction.id, 'acknowledged')}
                          disabled={busyKey !== null}
                        >
                          Acknowledge
                        </Button>
                        </Tooltip>
                      )}
                      {currentStatus !== 'completed' && (
                        <Tooltip label="Mark as done — action has been resolved">
                        <Button
                          variant="success"
                          onClick={() => void updateAction(selectedAction.id, 'completed')}
                          disabled={busyKey !== null}
                        >
                          Complete
                        </Button>
                        </Tooltip>
                      )}
                      {currentStatus !== 'snoozed' && currentStatus !== 'completed' && currentStatus !== 'not_an_action' && (
                        <Tooltip label="Hide for 24 hours — it will resurface automatically">
                        <Button
                          variant="default"
                          onClick={() => {
                            // Snooze for 24 hours by default
                            const snoozedUntil = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
                            void updateAction(selectedAction.id, 'snoozed', snoozedUntil);
                          }}
                          disabled={busyKey !== null}
                        >
                          Snooze 24h
                        </Button>
                        </Tooltip>
                      )}
                      {currentStatus !== 'dismissed' && currentStatus !== 'not_an_action' && (
                        <Tooltip label="Close without acting — removes from active queue">
                        <Button
                          variant="danger"
                          onClick={() => void updateAction(selectedAction.id, 'dismissed')}
                          disabled={busyKey !== null}
                        >
                          Dismiss
                        </Button>
                        </Tooltip>
                      )}
                      {currentStatus !== 'pending' && (
                        <Tooltip label="Move back to pending for review">
                        <Button
                          variant="ghost"
                          onClick={() => void updateAction(selectedAction.id, 'pending')}
                          disabled={busyKey !== null}
                        >
                          Re-open
                        </Button>
                        </Tooltip>
                      )}
                    </div>

                    {/* Feedback: not an action (false positive signal) */}
                    {currentStatus !== 'not_an_action' && (
                      <div className="aq-feedback-section">
                        <Tooltip label="Flag as false positive — this document doesn't need action">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void submitFeedback(selectedAction.id, 'not_an_action')}
                          disabled={busyKey !== null}
                        >
                          ⚑ Not an action
                        </Button>
                        </Tooltip>
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          </>
        )}
      </div>

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
