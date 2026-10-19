import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import {
  Badge,
  Button,
  Card,
  ConfidenceBadge,
  EmptyState,
  ErrorState,
  SkeletonLoader,
  Toast,
} from '../../ui';
import EobMatchDetail from '../../EobMatchDetail';
import OrphanDetail from '../../triage/OrphanDetail';
import DocumentPreview from '../../DocumentPreview';
import { endpoints } from '../../../lib/api';
import { getToastDuration } from '../../../lib/toast';
import { StatementGroupingDetail } from '../../triage/StatementGroupingDetail';
import DuplicateDetail from '../../../pages/DuplicateDetail';
import OcrFocusInspector from '../../OcrFocusInspector';
import { DocumentWorkspaceFrame } from '../../DocumentWorkspace';
import DocumentWorkspaceDocumentView from '../DocumentWorkspaceDocumentView';
import DocumentMetadataTab from '../tabs/DocumentMetadataTab';
import DocumentOcrTab from '../tabs/DocumentOcrTab';
import CurrencyInput from '../../CurrencyInput';
import { formatUsdCurrency } from '../../../lib/currency';
import '../../../styles/document-workspace-review.css';
import '../../../styles/document-workspace.css';

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
  total: number;
  offset: number;
  limit: number;
}

interface StatsResponse {
  total: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_type_status: Record<string, Record<string, number>>;
  pending: number;
}

type ItemTypeFilter = 'all' | 'action_classification' | 'eob_match_review' | 'grouping_anomaly' | 'orphan_document' | 'duplicate_document' | 'ocr_quality_review';
type StatusFilter = 'pending' | 'deferred' | 'resolved';
type OcrReviewStatusFilter = '' | 'UNCERTAIN' | 'REVIEW_RECOMMENDED' | 'FAILED';
type ReviewDocumentTab = 'actions' | 'metadata' | 'ocr';
type ToastState = { message: string; tone?: 'success' | 'error'; undoId?: string } | null;
type ActionCorrectionDraft = {
  action_type: string;
  title: string;
  summary: string;
  due_date: string;
  amount: string;
};

const ACTION_TYPES = [
  'PAY',
  'RESPOND',
  'SIGN',
  'SCHEDULE',
  'TASK',
  'REVIEW',
  'SHARE',
  'CANCEL',
  'RENEW',
  'DISPUTE',
];

const ITEM_TYPE_FILTERS: ReadonlySet<ItemTypeFilter> = new Set([
  'all',
  'action_classification',
  'eob_match_review',
  'grouping_anomaly',
  'orphan_document',
  'duplicate_document',
  'ocr_quality_review',
]);

function itemTypeFilterFromQuery(value: string | null): ItemTypeFilter {
  return value !== null && ITEM_TYPE_FILTERS.has(value as ItemTypeFilter)
    ? value as ItemTypeFilter
    : 'all';
}

function actionCorrectionDraft(item: TriageItem | null): ActionCorrectionDraft | null {
  if (item?.item_type !== 'action_classification') return null;
  const metadata = item.metadata ?? {};
  return {
    action_type: String(metadata.action_type ?? 'REVIEW').toUpperCase(),
    title: String(metadata.title ?? metadata.action_title ?? metadata.document_title ?? ''),
    summary: String(metadata.summary ?? ''),
    due_date: metadata.due_date ? String(metadata.due_date).slice(0, 10) : '',
    amount: metadata.amount == null ? '' : String(metadata.amount),
  };
}

function actionDraftsMatch(
  current: ActionCorrectionDraft | null,
  detected: ActionCorrectionDraft | null,
): boolean {
  if (!current || !detected) return current === detected;
  return Object.keys(current).every(
    (key) => current[key as keyof ActionCorrectionDraft] === detected[key as keyof ActionCorrectionDraft],
  );
}

function actionDraftRequirement(draft: ActionCorrectionDraft, position = 0): string | null {
  const prefix = position > 0 ? `Action ${position + 1}: ` : '';
  if (!(draft.title || '').trim()) return `${prefix}Enter a task name to continue.`;
  if (draft.action_type === 'PAY' && draft.amount === '') {
    return `${prefix}Enter an amount to continue. A due date is optional.`;
  }
  return null;
}

function emptyActionCorrection(): ActionCorrectionDraft {
  return {
    action_type: 'TASK',
    title: '',
    summary: '',
    due_date: '',
    amount: '',
  };
}

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
    case 'action_classification': return 'ACTION';
    case 'eob_match_review': return 'EOB';
    case 'grouping_anomaly': return 'GROUPING';
    case 'orphan_document': return 'ORPHAN';
    case 'duplicate_document': return 'DUPLICATE';
    case 'ocr_quality_review': return 'OCR';
    default: return itemType.toUpperCase();
  }
}

function typeBadgeTone(itemType: string) {
  switch (itemType) {
    case 'action_classification': return 'warning' as const;
    case 'eob_match_review': return 'info' as const;
    case 'grouping_anomaly': return 'warning' as const;
    case 'orphan_document': return 'danger' as const;
    case 'duplicate_document': return 'warning' as const;
    case 'ocr_quality_review': return 'info' as const;
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
  if (item.item_type === 'action_classification') {
    return String(meta.title || meta.document_title || `Action #${meta.action_id ?? item.target_id}`);
  }
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
  if (item.item_type === 'duplicate_document') {
    const docA = meta.doc_a_id ?? '?';
    const docB = meta.doc_b_id ?? '?';
    const scorePct = typeof meta.score_pct === 'number' ? `${meta.score_pct}%` : '';
    return `Duplicate: #${docA} ↔ #${docB}${scorePct ? ` (${scorePct})` : ''}`;
  }
  if (item.item_type === 'ocr_quality_review') {
    return String(meta.document_title || `Document #${meta.document_id ?? item.target_id}`);
  }
  return `${typeLabel(item.item_type)}: ${item.target_id}`;
}

function itemDocumentId(item: TriageItem): number | null {
  const metadata = item.metadata ?? {};
  const candidates = [
    metadata.document_id,
    metadata.eob_document_id,
    metadata.doc_a_id,
    item.target_type === 'document' ? item.target_id : null,
  ];
  const value = candidates.find((candidate) => candidate != null);
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function TriageDetailBreadcrumb({ item }: { item: TriageItem }) {
  const typeIcons: Record<string, string> = {
    eob_match_review: '🔗',
    grouping_anomaly: '📊',
    orphan_document: '📎',
    duplicate_document: '📋',
    action_classification: '⚡',
    ocr_quality_review: '🔎',
  };
  const icon = typeIcons[item.item_type] ?? '📄';

  return (
    <div className="triage-detail-breadcrumb">
      <span className="triage-breadcrumb-trail">
        <span className="triage-breadcrumb-segment">Queue</span>
        <span className="triage-breadcrumb-sep">›</span>
        <span className="triage-breadcrumb-segment">
          <Badge tone={typeBadgeTone(item.item_type)}>{icon} {typeLabel(item.item_type)}</Badge>
        </span>
        <span className="triage-breadcrumb-sep">›</span>
        <span className="triage-breadcrumb-segment triage-breadcrumb-current">Item #{item.id}</span>
      </span>
    </div>
  );
}

function MoreActionsMenu({
  onDismiss,
  disabled,
  size,
}: {
  onDismiss: () => void;
  disabled?: boolean;
  size?: 'sm';
}) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button size={size} variant="ghost" disabled={disabled}>More…</Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="triage-more-popover" sideOffset={5} align="end">
          <div className="triage-more-title">Other actions</div>
          <Popover.Close asChild>
            <Button size="sm" variant="ghost" onClick={onDismiss}>
              Dismiss
            </Button>
          </Popover.Close>
          <div className="triage-more-help">Close without deciding whether the suggestion is correct.</div>
          <Popover.Arrow className="triage-more-arrow" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function reviewActionLabel(action: string): string {
  const labels: Record<string, string> = {
    confirm: 'accept',
    reject: 'reject',
    defer: 'review later',
    dismiss: 'dismiss',
  };
  return labels[action] ?? action;
}

function ActionCorrectionEditor({
  draft,
  index,
  onChange,
}: {
  draft: ActionCorrectionDraft;
  index: number;
  onChange: (draft: ActionCorrectionDraft) => void;
}) {
  const suffix = index === 0 ? '' : ` for action ${index + 1}`;
  return (
    <div className="triage-action-correction">
      <label>
        <span>Action type</span>
        <select
          aria-label={`Corrected action type${suffix}`}
          value={draft.action_type}
          onChange={(event) => onChange({ ...draft, action_type: event.target.value })}
        >
          {ACTION_TYPES.map((type) => <option value={type} key={type}>{type}</option>)}
        </select>
      </label>
      <label>
        <span>Task name</span>
        <input
          aria-label={`Corrected task name${suffix}`}
          value={draft.title}
          onChange={(event) => onChange({ ...draft, title: event.target.value })}
        />
      </label>
      <label className="triage-action-correction-wide">
        <span>Summary</span>
        <textarea
          aria-label={`Corrected summary${suffix}`}
          rows={3}
          value={draft.summary}
          onChange={(event) => onChange({ ...draft, summary: event.target.value })}
        />
      </label>
      <label>
        <span>Due date</span>
        <input
          aria-label={`Corrected due date${suffix}`}
          type="date"
          value={draft.due_date}
          onChange={(event) => onChange({ ...draft, due_date: event.target.value })}
        />
      </label>
      <label>
        <span>Amount</span>
        <CurrencyInput
          label={`Corrected amount${suffix}`}
          value={draft.amount}
          onChange={(amount) => onChange({ ...draft, amount })}
        />
      </label>
    </div>
  );
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export default function ReviewWorkflow() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryTypeFilter = itemTypeFilterFromQuery(searchParams.get('type'));
  const queryItemId = searchParams.get('item');
  const query = searchParams.get('q') ?? '';
  const queryStatus = searchParams.get('status');
  const queryOcrReviewStatus = searchParams.get('ocr_review_status');

  // Data state
  const [items, setItems] = useState<TriageItem[]>([]);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const typeFilter = queryTypeFilter;
  const [statusFilter, setStatusFilter] = useState<StatusFilter>(
    queryStatus === 'deferred' || queryStatus === 'resolved' ? queryStatus : 'pending',
  );
  const ocrReviewStatus: OcrReviewStatusFilter = (
    queryOcrReviewStatus === 'UNCERTAIN'
    || queryOcrReviewStatus === 'REVIEW_RECOMMENDED'
    || queryOcrReviewStatus === 'FAILED'
  ) ? queryOcrReviewStatus : '';

  // Selection
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const lastCheckedRef = useRef<string | null>(null);
  const [actionCorrection, setActionCorrection] = useState<ActionCorrectionDraft | null>(null);
  const [additionalActionCorrections, setAdditionalActionCorrections] = useState<ActionCorrectionDraft[]>([]);
  const [documentTab, setDocumentTab] = useState<ReviewDocumentTab>('actions');

  // UI state
  const [collapsed, setCollapsed] = useState(false);
  const [toast, setToast] = useState<ToastState>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [populating, setPopulating] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [confirmReviseId, setConfirmReviseId] = useState<string | null>(null);

  // Bulk confirm modal & threshold
  const [pendingBulkAction, setPendingBulkAction] = useState<{ action: string; ids: string[] } | null>(null);
  const [thresholdPct, setThresholdPct] = useState(90);
  const [pendingThreshold, setPendingThreshold] = useState(false);

  const handleTypeFilterChange = (value: string) => {
    const nextType = itemTypeFilterFromQuery(value);
    const nextParams = new URLSearchParams(searchParams);
    if (nextType === 'all') {
      nextParams.delete('type');
    } else {
      nextParams.set('type', nextType);
    }
    if (nextType !== 'ocr_quality_review') nextParams.delete('ocr_review_status');
    nextParams.delete('item');
    setSearchParams(nextParams, { replace: true });
  };

  const handleStatusFilterChange = (value: StatusFilter) => {
    setStatusFilter(value);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('status', value);
    nextParams.delete('item');
    setSearchParams(nextParams, { replace: true });
  };

  const handleSearchChange = (value: string) => {
    const nextParams = new URLSearchParams(searchParams);
    if (value.trim()) nextParams.set('q', value);
    else nextParams.delete('q');
    nextParams.delete('item');
    setSearchParams(nextParams, { replace: true });
  };

  const handleOcrReviewStatusChange = (value: OcrReviewStatusFilter) => {
    const nextParams = new URLSearchParams(searchParams);
    if (value) nextParams.set('ocr_review_status', value);
    else nextParams.delete('ocr_review_status');
    nextParams.delete('item');
    setSearchParams(nextParams, { replace: true });
  };

  const selectItem = useCallback((itemId: string | null) => {
    setSelectedId(itemId);
    const nextParams = new URLSearchParams(searchParams);
    if (itemId) nextParams.set('item', itemId);
    else nextParams.delete('item');
    setSearchParams(nextParams, { replace: true });
  }, [searchParams, setSearchParams]);

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
      if (query.trim()) params.set('search', query.trim());
      if (typeFilter === 'ocr_quality_review' && ocrReviewStatus) {
        params.set('ocr_review_status', ocrReviewStatus);
      }
      params.set('limit', '200');

      const dismissedParams = new URLSearchParams(params);
      dismissedParams.set('status', 'dismissed');
      const [queueRes, dismissedRes, statsRes] = await Promise.all([
        endpoints.triage.queue(params.toString()) as Promise<QueueResponse>,
        statusFilter === 'resolved'
          ? endpoints.triage.queue(dismissedParams.toString()) as Promise<QueueResponse>
          : Promise.resolve(null),
        endpoints.triage.stats() as Promise<StatsResponse>,
      ]);

      setItems([
        ...(queueRes?.items ?? []),
        ...(dismissedRes?.items ?? []),
      ]);
      setStats(statsRes ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load items needing review.');
    } finally {
      setLoading(false);
    }
  }, [ocrReviewStatus, query, statusFilter, typeFilter]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // Auto-clear toast
  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone, !!toast.undoId);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  // ------------------------------------------------------------------
  // Selection helpers
  // ------------------------------------------------------------------

  const selectedItem = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );

  useEffect(() => {
    setDocumentTab(selectedItem?.item_type === 'ocr_quality_review' ? 'ocr' : 'actions');
  }, [selectedItem?.id, selectedItem?.item_type]);
  const visibleItems = items;
  const bulkEligibleItems = useMemo(
    () => visibleItems.filter(
      (item) => !['action_classification', 'ocr_quality_review'].includes(item.item_type),
    ),
    [visibleItems],
  );
  const selectedReviewCanResolve = selectedItem?.item_type !== 'action_classification'
    || ['pending', 'deferred'].includes(selectedItem.status);
  const detectedActionCorrection = useMemo(
    () => actionCorrectionDraft(selectedItem),
    [selectedItem],
  );
  const actionCorrectionIsDirty = !actionDraftsMatch(actionCorrection, detectedActionCorrection)
    || additionalActionCorrections.length > 0;
  const actionCorrectionMissingRequirement = actionCorrection
    ? [actionCorrection, ...additionalActionCorrections]
      .map((draft, index) => actionDraftRequirement(draft, index))
      .find((requirement) => requirement !== null) ?? null
    : null;

  useEffect(() => {
    setActionCorrection(detectedActionCorrection);
    setAdditionalActionCorrections([]);
  }, [detectedActionCorrection]);

  // Keep selectedId valid when items change
  useEffect(() => {
    if (items.length === 0) {
      setSelectedId(null);
      return;
    }
    if (queryItemId && items.some((item) => item.id === queryItemId)) {
      setSelectedId(queryItemId);
      return;
    }
    if (!items.some((item) => item.id === selectedId)) {
      setSelectedId(items[0]?.id ?? null);
    }
  }, [items, queryItemId, selectedId]);

  const toggleCheck = (itemId: string, shiftKey = false) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);

      if (shiftKey && lastCheckedRef.current) {
        const startIdx = visibleItems.findIndex((i) => i.id === lastCheckedRef.current);
        const endIdx = visibleItems.findIndex((i) => i.id === itemId);
        if (startIdx >= 0 && endIdx >= 0) {
          const [lo, hi] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
          for (let i = lo; i <= hi; i++) {
            if (!['action_classification', 'ocr_quality_review'].includes(visibleItems[i].item_type)) {
              next.add(visibleItems[i].id);
            }
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
    const allChecked = bulkEligibleItems.length > 0
      && bulkEligibleItems.every((item) => checkedIds.has(item.id));
    if (allChecked) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(bulkEligibleItems.map((item) => item.id)));
    }
  };

  const moveSelection = useCallback((direction: -1 | 1) => {
    if (visibleItems.length === 0) return;
    const idx = visibleItems.findIndex((i) => i.id === selectedId);
    const next = idx < 0 ? 0 : Math.max(0, Math.min(visibleItems.length - 1, idx + direction));
    selectItem(visibleItems[next]?.id ?? null);
  }, [selectItem, selectedId, visibleItems]);

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  const handleResolve = useCallback(async (itemId: string, action: string) => {
    setBusyAction(`${itemId}-${action}`);
    try {
      const result = await endpoints.triage.resolve(itemId, { action }) as {
        action_ready?: boolean;
        review_state?: string;
        reason?: string;
      };
      const isActionClassification = items.find((item) => item.id === itemId)?.item_type === 'action_classification';
      const remainsInReview = isActionClassification && result.action_ready === false;
      setToast({
        message: remainsInReview
          ? result.reason || 'This action still needs review.'
          : action === 'mark_reviewed'
            ? 'OCR review marked complete.'
          : action === 'confirm'
            ? 'Suggestion accepted.'
            : action === 'no_action'
              ? 'False positive recorded.'
              : action === 're_evaluate'
                ? 'Re-evaluation completed.'
                : 'Suggestion rejected.',
        tone: remainsInReview ? undefined : 'success',
        undoId: isActionClassification || remainsInReview ? undefined : itemId,
      });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Action failed.', tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  }, [items, loadData]);

  const correctActionClassification = async (itemId: string) => {
    if (!actionCorrection) return;
    setBusyAction(`${itemId}-correct`);
    try {
      const result = await endpoints.triage.resolve(itemId, {
        action: 'correct',
        payload: additionalActionCorrections.length > 0
          ? {
              actions: [actionCorrection, ...additionalActionCorrections].map((draft) => ({
                action_type: draft.action_type,
                title: draft.title,
                summary: draft.summary,
                due_date: draft.due_date || null,
                amount: draft.amount === '' ? null : Number(draft.amount),
              })),
            }
          : {
              action_type: actionCorrection.action_type,
              title: actionCorrection.title,
              summary: actionCorrection.summary,
              due_date: actionCorrection.due_date || null,
              amount: actionCorrection.amount === '' ? null : Number(actionCorrection.amount),
            },
      }) as { action_ready?: boolean; reason?: string };
      setToast({
        message: result.action_ready
          ? additionalActionCorrections.length > 0
            ? `${additionalActionCorrections.length + 1} actions accepted and added to Actions.`
            : 'Correction saved and action added to Actions.'
          : result.reason || 'Correction saved; this item still needs review.',
        tone: result.action_ready ? 'success' : undefined,
      });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Correction failed.', tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  const handleDefer = useCallback(async (itemId: string) => {
    setBusyAction(`${itemId}-defer`);
    try {
      await endpoints.triage.defer(itemId);
      setToast({ message: 'Review postponed for 7 days.', tone: 'success', undoId: itemId });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Unable to schedule another review.', tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  }, [loadData]);

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
      setStatusFilter('pending');
      setToast({ message: 'Action undone.', tone: 'success' });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Undo failed.', tone: 'error' });
    }
  };

  const handleBulkAction = async (action: 'confirm' | 'reject' | 'defer' | 'dismiss') => {
    const ids = Array.from(checkedIds);
    if (ids.length === 0) return;

    // Destructive actions require confirmation
    if (action === 'reject' || action === 'dismiss') {
      setPendingBulkAction({ action, ids });
      return;
    }

    await executeBulkAction(action, ids);
  };

  const executeBulkAction = async (action: string, ids: string[]) => {
    setBusyAction(`bulk-${action}`);
    try {
      const result = await endpoints.triage.bulk({ action, item_ids: ids });
      setCheckedIds(new Set());
      setToast({
        message: `${result.affected} item${result.affected !== 1 ? 's' : ''} updated: ${reviewActionLabel(action)}.`,
        tone: 'success',
      });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : `Bulk ${action} failed.`, tone: 'error' });
    } finally {
      setBusyAction(null);
    }
  };

  const confirmPendingBulkAction = async () => {
    if (!pendingBulkAction) return;
    const { action, ids } = pendingBulkAction;
    setPendingBulkAction(null);
    await executeBulkAction(action, ids);
  };

  const handleBulkConfirmThreshold = async () => {
    setPendingThreshold(true);
  };

  const executeBulkConfirmThreshold = async () => {
    setPendingThreshold(false);
    setBusyAction('bulk-threshold');
    try {
      const result = await endpoints.triage.bulkConfirmThreshold({ min_confidence: thresholdPct });
      setToast({
        message: `${result.affected} item${result.affected !== 1 ? 's' : ''} auto-accepted (≥${thresholdPct}%).`,
        tone: 'success',
      });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Auto-accept failed.', tone: 'error' });
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

      // When an EOB match review item is selected, EobMatchDetail owns Y/N/S/R
      const itemOwnsDecisionShortcuts = ['eob_match_review', 'ocr_quality_review'].includes(
        selectedItem?.item_type ?? '',
      );

      // Escape closes modals first, then deselects
      if (e.key === 'Escape') {
        e.preventDefault();
        if (showShortcuts) { setShowShortcuts(false); return; }
        if (pendingBulkAction) { setPendingBulkAction(null); return; }
        if (pendingThreshold) { setPendingThreshold(false); return; }
        selectItem(null);
        return;
      }

      // ? opens keyboard shortcuts overlay
      if (e.key === '?' || (e.shiftKey && e.key === '/')) {
        e.preventDefault();
        setShowShortcuts((s) => !s);
        return;
      }

      // Suppress all other shortcuts while a modal is open
      if (pendingBulkAction || pendingThreshold || showShortcuts) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        moveSelection(1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        moveSelection(-1);
      } else if (e.key.toLowerCase() === 'y' && selectedId && !itemOwnsDecisionShortcuts) {
        e.preventDefault();
        void handleResolve(selectedId, 'confirm');
      } else if (e.key.toLowerCase() === 'n' && selectedId && !itemOwnsDecisionShortcuts) {
        e.preventDefault();
        void handleResolve(selectedId, 'reject');
      } else if (e.key.toLowerCase() === 's' && selectedId && !itemOwnsDecisionShortcuts) {
        e.preventDefault();
        // Skip — move to next
        moveSelection(1);
      } else if (e.key.toLowerCase() === 'd' && selectedId) {
        e.preventDefault();
        void handleDefer(selectedId);
      } else if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        setCollapsed((c) => !c);
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [
    handleDefer,
    handleResolve,
    moveSelection,
    pendingBulkAction,
    pendingThreshold,
    selectedId,
    selectedItem,
    selectItem,
    showShortcuts,
  ]);

  // ------------------------------------------------------------------
  // Derived counts
  // ------------------------------------------------------------------

  const pendingCount = stats?.pending ?? 0;
  const statusCounts = stats?.by_status ?? {};
  const currentTypeCounts = Object.fromEntries(
    Object.entries(stats?.by_type_status ?? {}).map(([itemType, counts]) => [
      itemType,
      statusFilter === 'resolved'
        ? (counts.resolved ?? 0) + (counts.dismissed ?? 0)
        : counts[statusFilter] ?? 0,
    ]),
  );
  const currentTotal = Object.values(currentTypeCounts).reduce((sum, count) => sum + count, 0);

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <>
      {loading && !items.length ? (
        <SkeletonLoader variant="table" rows={8} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <DocumentWorkspaceFrame
          collapsed={collapsed}
          className="triage-shell document-review-workspace"
          rail={(
            <>
          {/* ── Queue list panel (left) ── */}
          <section className={collapsed ? 'triage-queue-panel collapsed' : 'triage-queue-panel'}>
            <div className="triage-queue-header">
              <div className="triage-queue-header-content">
                <div className="triage-queue-title">
                  <strong>Needs Review</strong>
                  <span>{pendingCount} pending · {visibleItems.length} shown</span>
                </div>
                <button
                  type="button"
                  className="document-workspace-collapse"
                  onClick={() => setCollapsed((current) => !current)}
                  title="Toggle review queue (F)"
                  aria-label={collapsed ? 'Expand review queue' : 'Collapse review queue'}
                >
                  {collapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}
                </button>
                {!collapsed ? (
                  <>
                    <label className="document-workspace-search">
                      <Search size={16} />
                      <input
                        aria-label="Search review items"
                        placeholder="Find document, reason, or review"
                        value={query}
                        onChange={(event) => handleSearchChange(event.target.value)}
                      />
                    </label>
                    <div className="workspace-filter-group">
                      <span>Status</span>
                      <div className="workspace-segments">
                        {([
                          ['pending', 'Pending', statusCounts.pending ?? 0],
                          ['deferred', 'Review later', statusCounts.deferred ?? 0],
                          ['resolved', 'Resolved', (statusCounts.resolved ?? 0) + (statusCounts.dismissed ?? 0)],
                        ] as const).map(([value, label, count]) => (
                          <button
                            type="button"
                            key={value}
                            className={statusFilter === value ? 'active' : ''}
                            onClick={() => handleStatusFilterChange(value)}
                          >
                            {label} <b>{count}</b>
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="workspace-filter-group">
                      <span>Review type</span>
                      <div className="workspace-chips">
                        {([
                          ['all', 'All', currentTotal],
                          ['action_classification', 'Actions', currentTypeCounts.action_classification ?? 0],
                          ['eob_match_review', 'EOB', currentTypeCounts.eob_match_review ?? 0],
                          ['ocr_quality_review', 'OCR', currentTypeCounts.ocr_quality_review ?? 0],
                          ['grouping_anomaly', 'Groups', currentTypeCounts.grouping_anomaly ?? 0],
                          ['orphan_document', 'Orphans', currentTypeCounts.orphan_document ?? 0],
                          ['duplicate_document', 'Duplicates', currentTypeCounts.duplicate_document ?? 0],
                        ] as const).map(([value, label, count]) => (
                          <button
                            type="button"
                            key={value}
                            className={typeFilter === value ? 'active' : ''}
                            onClick={() => handleTypeFilterChange(value)}
                          >
                            {label} <b>{count}</b>
                          </button>
                        ))}
                      </div>
                    </div>
                    {typeFilter === 'ocr_quality_review' ? (
                      <div className="workspace-filter-group">
                        <span>OCR assessment</span>
                        <div className="workspace-chips">
                          {([
                            ['', 'All OCR'],
                            ['FAILED', 'Failed'],
                            ['REVIEW_RECOMMENDED', 'Review recommended'],
                            ['UNCERTAIN', 'Uncertain'],
                          ] as const).map(([value, label]) => (
                            <button
                              type="button"
                              key={value || 'all'}
                              className={ocrReviewStatus === value ? 'active' : ''}
                              onClick={() => handleOcrReviewStatusChange(value)}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </>
                ) : null}

                {/* Auto-accept threshold */}
                {!collapsed && statusFilter === 'pending' && typeFilter === 'eob_match_review' && (
                  <div className="triage-threshold-bar">
                    <button
                      className="triage-auto-confirm-btn"
                      onClick={() => void handleBulkConfirmThreshold()}
                      disabled={busyAction !== null}
                      title={`Auto-accept all EOB matches with confidence ≥ ${thresholdPct}%`}
                    >
                      ⚡ Auto-accept ≥
                    </button>
                    <input
                      type="number"
                      className="triage-threshold-input"
                      value={thresholdPct}
                      onChange={(e) => setThresholdPct(Math.max(0, Math.min(100, Number(e.target.value))))}
                      min={0}
                      max={100}
                    />
                    <span className="triage-threshold-label">%</span>
                  </div>
                )}
              </div>
            </div>

            {collapsed ? (
              <div className="triage-collapsed-strip">
                <span>{visibleItems.length}</span>
              </div>
            ) : (
              <>
                {/* Bulk action bar — only for pending items (bulk ops target pending status) */}
                {checkedIds.size > 0 && statusFilter === 'pending' && (
                  <div className="triage-bulk-bar">
                    <span>
                      {checkedIds.size} selected
                      {(() => {
                        const visibleIds = new Set(items.map((i) => i.id));
                        const hiddenCount = Array.from(checkedIds).filter((id) => !visibleIds.has(id)).length;
                        return hiddenCount > 0 ? ` (${hiddenCount} hidden by filter)` : '';
                      })()}
                    </span>
                    <div className="btn-group">
                      <Button variant="success" size="sm" onClick={() => void handleBulkAction('confirm')} disabled={busyAction !== null}>
                        Accept
                      </Button>
                      <Button variant="danger" size="sm" onClick={() => void handleBulkAction('reject')} disabled={busyAction !== null}>
                        Reject
                      </Button>
                      <Button size="sm" onClick={() => void handleBulkAction('defer')} disabled={busyAction !== null}>
                        Review later
                      </Button>
                      <MoreActionsMenu
                        size="sm"
                        disabled={busyAction !== null}
                        onDismiss={() => void handleBulkAction('dismiss')}
                      />
                      <Button size="sm" onClick={() => setCheckedIds(new Set())} disabled={busyAction !== null}>
                        Clear
                      </Button>
                    </div>
                  </div>
                )}

                {/* Select-all + populate */}
                <div className="triage-list-toolbar">
                  {statusFilter === 'pending' && bulkEligibleItems.length > 0 ? (
                    <label className="triage-select-all">
                      <input
                        type="checkbox"
                        checked={bulkEligibleItems.every((item) => checkedIds.has(item.id))}
                        onChange={selectAllVisible}
                      />
                      <span>Select visible</span>
                    </label>
                  ) : (
                    <span />
                  )}
                  <Button size="sm" onClick={() => void handlePopulate()} disabled={populating}>
                    {populating ? 'Scanning…' : '🔄 Populate'}
                  </Button>
                </div>

                {/* Queue list */}
                <div className="triage-queue-list">
                  {visibleItems.length === 0 ? (
                    <EmptyState
                      title="Queue is empty"
                      desc={
                        query.trim()
                          ? 'No review items match the current search.'
                          : statusFilter === 'pending'
                          ? 'No items need review. Use "Populate" to scan for new flagged items.'
                          : `No ${statusFilter} items found.`
                      }
                    />
                  ) : (
                    visibleItems.map((item) => {
                      const documentId = itemDocumentId(item);
                      return (
                      <article
                        key={item.id}
                        className={[
                          'triage-item',
                          selectedId === item.id ? 'selected' : '',
                          checkedIds.has(item.id) ? 'checked' : '',
                        ].filter(Boolean).join(' ')}
                        onClick={() => selectItem(item.id)}
                      >
                        {documentId ? (
                          <img
                            className="triage-item-thumbnail"
                            src={endpoints.documents.thumbnailUrl(documentId)}
                            alt=""
                            loading="lazy"
                          />
                        ) : <div className="triage-item-thumbnail-placeholder">{typeLabel(item.item_type).slice(0, 1)}</div>}
                        <div className="triage-item-content">
                          <div className="triage-item-top">
                            {statusFilter === 'pending' && !['action_classification', 'ocr_quality_review'].includes(item.item_type) && (
                              <input
                                type="checkbox"
                                checked={checkedIds.has(item.id)}
                                onChange={(e) => toggleCheck(item.id, e.nativeEvent instanceof MouseEvent && e.nativeEvent.shiftKey)}
                                onClick={(e) => e.stopPropagation()}
                              />
                            )}
                            <Badge tone={typeBadgeTone(item.item_type)}>{typeLabel(item.item_type)}</Badge>
                            {item.metadata && typeof item.metadata.score_pct === 'number' && (
                              <ConfidenceBadge pct={item.metadata.score_pct as number} />
                            )}
                            {item.item_type === 'ocr_quality_review' && item.metadata?.review_status ? (
                              <Badge tone={String(item.metadata.review_status) === 'FAILED' ? 'danger' : 'warning'}>
                                {String(item.metadata.review_status).replaceAll('_', ' ')}
                              </Badge>
                            ) : null}
                            {item.item_type === 'ocr_quality_review' && item.metadata?.weakest_dimension ? (
                              <Badge tone="muted">
                                {String(item.metadata.weakest_dimension)} {String(item.metadata.weakest_score ?? '—')}
                              </Badge>
                            ) : null}
                          </div>
                          <div className="triage-item-title">{itemTitle(item)}</div>
                          <div className="triage-item-reason">{item.reason || 'Flagged for review'}</div>
                          <div className="triage-item-meta">
                            <Badge tone={priorityBadgeTone(item.priority)}>{priorityLabel(item.priority)}</Badge>
                            {item.item_type === 'ocr_quality_review' ? (
                              <span>Candidate: {String(item.metadata?.candidate_state ?? 'none')}</span>
                            ) : null}
                            <span>{timeAgo(item.created_at)}</span>
                          </div>
                        </div>
                      </article>
                    )})
                  )}
                </div>
              </>
            )}
          </section>
            </>
          )}
        >

          {/* ── Detail panel (right) ── */}
          <section className={[
            'triage-detail-panel',
            selectedItem && ['action_classification', 'ocr_quality_review'].includes(selectedItem.item_type)
              ? 'document-workspace-detail-host'
              : '',
          ].filter(Boolean).join(' ')}>
            {selectedItem ? (
              selectedItem.item_type === 'action_classification' ? (
                <DocumentWorkspaceDocumentView
                  className="document-review-document"
                  document={{
                    id: itemDocumentId(selectedItem) ?? Number(selectedItem.target_id),
                    context: 'Needs Review · Action',
                    title: itemTitle(selectedItem),
                    date: String(selectedItem.metadata?.document_date ?? 'Not set'),
                    correspondent: String(selectedItem.metadata?.correspondent ?? 'Not set'),
                    documentType: String(selectedItem.metadata?.document_type ?? 'Not set'),
                  }}
                  activeTab={documentTab}
                  onTabChange={(key) => setDocumentTab(key as ReviewDocumentTab)}
                  previewUrl={itemDocumentId(selectedItem)
                    ? `${endpoints.documents.previewUrl(itemDocumentId(selectedItem) as number)}#page=1&zoom=page-fit`
                    : undefined}
                  tabs={[
                    { key: 'actions', label: 'Actions' },
                    { key: 'metadata', label: 'Metadata' },
                    { key: 'ocr', label: 'OCR' },
                  ]}
                >
                  {documentTab === 'actions' ? (
                  <>
                  <div className="document-review-guidance text-muted">
                    {selectedReviewCanResolve
                      ? 'Decide what OWL should believe before this becomes actionable work.'
                      : 'This review is complete. The accepted values are shown below.'}
                  </div>
                  <Card title="Document action review">
                    <div className="triage-detail-info">
                      <div className="triage-detail-row">
                        <span>Suggested type</span>
                        <strong>{String(selectedItem.metadata?.action_type ?? 'REVIEW')}</strong>
                      </div>
                      <div className="triage-detail-row">
                        <span>Confidence</span>
                        <strong>{String(selectedItem.metadata?.confidence ?? '—')}%</strong>
                      </div>
                      <div className="triage-detail-row">
                        <span>Due date</span>
                        <strong>{String(selectedItem.metadata?.due_date ?? 'No due date')}</strong>
                      </div>
                      <div className="triage-detail-row">
                        <span>Amount</span>
                        <strong>{formatUsdCurrency(selectedItem.metadata?.amount) || '—'}</strong>
                      </div>
                    </div>
                    {selectedReviewCanResolve && actionCorrection && (
                      <div className="triage-action-set-review">
                        <div className="triage-action-set-heading">
                          <div>
                            <strong>Proposed actions on this document</strong>
                            <span>{additionalActionCorrections.length + 1} proposed</span>
                          </div>
                          <Button
                            size="sm"
                            onClick={() => setAdditionalActionCorrections((current) => [
                              ...current,
                              emptyActionCorrection(),
                            ])}
                          >
                            Add action
                          </Button>
                        </div>
                        <ActionCorrectionEditor
                          draft={actionCorrection}
                          index={0}
                          onChange={(draft) => setActionCorrection(draft)}
                        />
                        {additionalActionCorrections.map((draft, index) => (
                          <div className="triage-additional-action" key={index}>
                            <div className="triage-additional-action-header">
                              <strong>Action {index + 2}</strong>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setAdditionalActionCorrections((current) => (
                                  current.filter((_, candidateIndex) => candidateIndex !== index)
                                ))}
                              >
                                Remove
                              </Button>
                            </div>
                            <ActionCorrectionEditor
                              draft={draft}
                              index={index + 1}
                              onChange={(nextDraft) => setAdditionalActionCorrections((current) => (
                                current.map((candidate, candidateIndex) => (
                                  candidateIndex === index ? nextDraft : candidate
                                ))
                              ))}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                    {selectedReviewCanResolve && actionCorrectionMissingRequirement && (
                      <p className="triage-action-requirement" role="status">
                        {actionCorrectionMissingRequirement}
                      </p>
                    )}
                    {selectedReviewCanResolve ? (
                      <div className="btn-group">
                        <Button
                          variant="success"
                          disabled={
                            busyAction !== null
                            || actionCorrectionMissingRequirement !== null
                          }
                          onClick={() => {
                            if (actionCorrectionIsDirty) {
                              void correctActionClassification(selectedItem.id);
                            } else {
                              void handleResolve(selectedItem.id, 'confirm');
                            }
                          }}
                        >
                          {additionalActionCorrections.length > 0
                            ? `Accept ${additionalActionCorrections.length + 1} actions`
                            : actionCorrectionIsDirty
                              ? 'Save corrections'
                              : 'Accept suggestion'}
                        </Button>
                        {actionCorrectionIsDirty && (
                          <Button
                            variant="ghost"
                            disabled={busyAction !== null}
                            onClick={() => {
                              setActionCorrection(detectedActionCorrection);
                              setAdditionalActionCorrections([]);
                            }}
                          >
                            Reset to suggested values
                          </Button>
                        )}
                        <Button
                          variant="danger"
                          disabled={busyAction !== null}
                          onClick={() => void handleResolve(selectedItem.id, 'no_action')}
                        >
                          Not an action
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={busyAction !== null}
                          onClick={() => void handleResolve(selectedItem.id, 're_evaluate')}
                          title="Discard these values and rerun extraction from the original document."
                        >
                          Analyze document again
                        </Button>
                      </div>
                    ) : (
                      <div className="triage-action-resolution" role="status">
                        <div>
                          <strong>
                            {selectedItem.resolved_action === 'correct'
                              ? 'Corrections saved'
                              : selectedItem.resolved_action === 'confirm'
                                ? 'Suggestion accepted'
                                  : 'Not an action'}
                          </strong>
                          <span>
                            {selectedItem.resolved_at
                              ? `Completed ${new Date(selectedItem.resolved_at).toLocaleString()}`
                              : 'Review completed'}
                          </span>
                        </div>
                        {['confirm', 'correct'].includes(selectedItem.resolved_action ?? '') && (
                          <a href={`#/actions/${selectedItem.metadata?.action_id ?? selectedItem.target_id}`}>
                            View action
                          </a>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setConfirmReviseId(selectedItem.id)}
                        >
                          Revise decision
                        </Button>
                      </div>
                    )}
                  </Card>
                  </>
                  ) : null}
                  {documentTab === 'metadata' ? (
                    <DocumentMetadataTab
                      document={{
                        document_id: itemDocumentId(selectedItem) ?? selectedItem.target_id,
                        title: String(selectedItem.metadata?.document_title ?? itemTitle(selectedItem)),
                        correspondent: selectedItem.metadata?.correspondent
                          ? String(selectedItem.metadata.correspondent)
                          : null,
                        document_type: selectedItem.metadata?.document_type
                          ? String(selectedItem.metadata.document_type)
                          : null,
                        document_date: selectedItem.metadata?.document_date
                          ? String(selectedItem.metadata.document_date)
                          : null,
                      }}
                      editHref={itemDocumentId(selectedItem)
                        ? `#/inbox?document=${itemDocumentId(selectedItem)}&tab=metadata`
                        : undefined}
                      note="Review decisions do not file the document or change Paperless metadata."
                    />
                  ) : null}
                  {documentTab === 'ocr' && itemDocumentId(selectedItem) ? (
                    <DocumentOcrTab documentId={itemDocumentId(selectedItem) as number} />
                  ) : null}
                </DocumentWorkspaceDocumentView>
              ) : selectedItem.item_type === 'eob_match_review' ? (
                /* EOB Match Review — rich detail component (#834) */
                <>
                  <TriageDetailBreadcrumb item={selectedItem} />
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
                        selectItem(nextItem.id);
                      }
                    }}
                  />
                </>
              ) : selectedItem.item_type === 'ocr_quality_review' ? (
                <DocumentWorkspaceDocumentView
                  className="document-review-document document-review-ocr"
                  document={{
                    id: itemDocumentId(selectedItem) ?? Number(selectedItem.target_id),
                    context: 'Needs Review · OCR',
                    title: itemTitle(selectedItem),
                    date: String(selectedItem.metadata?.document_date ?? 'Not set'),
                    correspondent: String(selectedItem.metadata?.correspondent ?? 'Not set'),
                    documentType: String(selectedItem.metadata?.document_type ?? 'Not set'),
                  }}
                  activeTab={documentTab}
                  onTabChange={(key) => setDocumentTab(key as ReviewDocumentTab)}
                  tabs={[
                    { key: 'ocr', label: 'OCR' },
                    { key: 'metadata', label: 'Metadata' },
                  ]}
                  headerActions={(
                    <div className="btn-group">
                      {['pending', 'deferred'].includes(selectedItem.status) ? (
                        <>
                          <Button
                            variant="success"
                            disabled={busyAction !== null}
                            onClick={() => void handleResolve(selectedItem.id, 'mark_reviewed')}
                          >
                            Mark OCR review complete
                          </Button>
                          <Button
                            disabled={busyAction !== null}
                            onClick={() => void handleDefer(selectedItem.id)}
                          >
                            Review later
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="ghost"
                          disabled={busyAction !== null}
                          onClick={() => setConfirmReviseId(selectedItem.id)}
                        >
                          Revise decision
                        </Button>
                      )}
                    </div>
                  )}
                >
                  {documentTab === 'ocr' ? (
                    <>
                    <div className="document-review-guidance">
                      <strong>OCR review reason:</strong> {selectedItem.reason || 'OCR quality needs review.'}
                      <span>
                        {selectedItem.metadata?.weakest_dimension
                          ? `Weakest signal: ${String(selectedItem.metadata.weakest_dimension)} ${String(selectedItem.metadata.weakest_score ?? '—')}`
                          : 'Review the OCR evidence and candidate state.'}
                        {' · '}
                        Candidate: {String(selectedItem.metadata?.candidate_state ?? 'none')}
                      </span>
                    </div>
                    <div className="triage-ocr-inspector">
                    <OcrFocusInspector
                      documentId={Number(selectedItem.metadata?.document_id ?? selectedItem.target_id)}
                      contextLabel="Needs Review"
                      onReturn={() => selectItem(null)}
                    />
                    </div>
                    </>
                  ) : null}
                  {documentTab === 'metadata' ? (
                    <DocumentMetadataTab
                      document={{
                        document_id: itemDocumentId(selectedItem) ?? selectedItem.target_id,
                        title: String(selectedItem.metadata?.document_title ?? itemTitle(selectedItem)),
                        correspondent: selectedItem.metadata?.correspondent
                          ? String(selectedItem.metadata.correspondent)
                          : null,
                        document_type: selectedItem.metadata?.document_type
                          ? String(selectedItem.metadata.document_type)
                          : null,
                        document_date: selectedItem.metadata?.document_date
                          ? String(selectedItem.metadata.document_date)
                          : null,
                      }}
                      editHref={itemDocumentId(selectedItem)
                        ? `#/inbox?document=${itemDocumentId(selectedItem)}&tab=metadata`
                        : undefined}
                      note="Resolving OCR review changes OCR trust only; it does not file this document."
                    />
                  ) : null}
                </DocumentWorkspaceDocumentView>
              ) : selectedItem.item_type === 'orphan_document' ? (
                /* Orphan Document — rich detail component (#831) */
                <>
                  <TriageDetailBreadcrumb item={selectedItem} />
                  <OrphanDetail
                    triageItem={selectedItem}
                    onResolved={() => {
                      void loadData();
                    }}
                    onSkip={() => {
                      const currentIndex = items.findIndex((i) => i.id === selectedItem.id);
                      const nextItem = items[currentIndex + 1] ?? items[0];
                      if (nextItem && nextItem.id !== selectedItem.id) {
                        selectItem(nextItem.id);
                      }
                    }}
                  />
                </>
              ) : (
                <>
                <TriageDetailBreadcrumb item={selectedItem} />
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
                      title="Accept (Y)"
                    >
                      Accept
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
                      title="Review later (D)"
                    >
                      Review later
                    </Button>
                    <MoreActionsMenu
                      disabled={busyAction !== null || selectedItem.status !== 'pending'}
                      onDismiss={() => void handleDismiss(selectedItem.id)}
                    />
                    {selectedItem.status !== 'pending' ? (
                      <Button
                        variant="ghost"
                        disabled={busyAction !== null}
                        onClick={() => setConfirmReviseId(selectedItem.id)}
                      >
                        Revise decision
                      </Button>
                    ) : null}
                  </div>
                </div>

                {/* Reason banner — skip for types with dedicated detail components */}
                {selectedItem.reason && !['grouping_anomaly', 'duplicate_document'].includes(selectedItem.item_type) && (
                  <div className="triage-reason-banner">
                    <strong>Flagged for review:</strong> {selectedItem.reason}
                  </div>
                )}

                {/* Item info card — skip for types with dedicated detail components */}
                {!['grouping_anomaly', 'duplicate_document'].includes(selectedItem.item_type) && (
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
                        <span>Review after</span>
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
                )}

                {/* Document preview — show when target is a document */}
                {selectedItem.target_type === 'document' && selectedItem.target_id && !Number.isNaN(Number(selectedItem.target_id)) && (
                  <Card title="Document preview">
                    <DocumentPreview
                      documentId={Number(selectedItem.target_id)}
                    />
                  </Card>
                )}

                {/* Type-specific detail views */}
                {selectedItem.item_type === 'grouping_anomaly' ? (
                  <StatementGroupingDetail
                    seriesId={selectedItem.target_id}
                    triageItemId={selectedItem.id}
                    reason={selectedItem.reason}
                    onResolved={(action) => {
                      setToast({ message: `Series ${action} completed.`, tone: 'success' });
                      void loadData();
                      // Auto-advance to next item
                      const currentIdx = items.findIndex(i => i.id === selectedItem.id);
                      const next = items[currentIdx + 1] || items[currentIdx - 1];
                      selectItem(next?.id ?? null);
                    }}
                  />
                ) : selectedItem.item_type === 'duplicate_document' && selectedItem.metadata?.duplicate_pair_id ? (
                  <DuplicateDetail
                    pairId={selectedItem.metadata.duplicate_pair_id as string}
                    onResolved={() => void loadData()}
                  />
                ) : (
                  /* Metadata dump — placeholder for remaining detail views */
                  <Card title="Item metadata">
                    <div className="triage-metadata-dump">
                      {selectedItem.metadata ? (
                        <pre>{JSON.stringify(selectedItem.metadata, null, 2)}</pre>
                      ) : (
                        <div className="text-muted">No additional metadata available for this item.</div>
                      )}
                    </div>
                  </Card>
                )}

                {/* Metadata correction link — when target is a document */}
                {(selectedItem.target_type === 'document' || selectedItem.metadata?.document_id) && (
                  <div className="triage-metadata-link">
                    <a href={`#/metadata/${selectedItem.metadata?.document_id ?? selectedItem.target_id}`}>
                      ✏️ Correct Metadata →
                    </a>
                  </div>
                )}
              </>
              )
            ) : (
              <EmptyState
                title="No review item selected"
                desc="Choose an item to inspect its details, metadata, and available actions."
              />
            )}
          </section>
        </DocumentWorkspaceFrame>
      )}

      {/* Confirmation modal for destructive bulk actions */}
      {pendingBulkAction && (
        <div className="triage-modal-overlay" onClick={() => setPendingBulkAction(null)}>
          <div className="triage-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="triage-modal-title">
              Confirm bulk action
            </div>
            <p className="triage-modal-desc">
              Are you sure you want to <strong>{reviewActionLabel(pendingBulkAction.action)}</strong>{' '}
              {pendingBulkAction.ids.length} item{pendingBulkAction.ids.length !== 1 ? 's' : ''}?
              This action cannot be easily undone in bulk.
            </p>
            <div className="triage-modal-actions">
              <Button size="sm" onClick={() => setPendingBulkAction(null)}>Cancel</Button>
              <Button
                variant={pendingBulkAction.action === 'reject' ? 'danger' : 'ghost'}
                size="sm"
                onClick={() => void confirmPendingBulkAction()}
              >
                {pendingBulkAction.action === 'reject' ? 'Reject' : 'Dismiss'} {pendingBulkAction.ids.length} items
              </Button>
            </div>
          </div>
        </div>
      )}

      {confirmReviseId && (
        <div className="triage-modal-overlay" onClick={() => setConfirmReviseId(null)}>
          <div className="triage-modal" role="dialog" aria-modal="true" aria-labelledby="revise-review-title" onClick={(event) => event.stopPropagation()}>
            <div className="triage-modal-title" id="revise-review-title">
              Reopen this document review?
            </div>
            <p className="triage-modal-desc">
              The accepted values will remain visible while you revise them. If the resulting action was already completed,
              its completion history is preserved and the revised classification may change downstream metadata.
            </p>
            <div className="triage-modal-actions">
              <Button size="sm" onClick={() => setConfirmReviseId(null)}>Cancel</Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  const itemId = confirmReviseId;
                  setConfirmReviseId(null);
                  void handleUndo(itemId);
                }}
              >
                Reopen review
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Confirmation modal for threshold auto-accept */}
      {pendingThreshold && (
        <div className="triage-modal-overlay" onClick={() => setPendingThreshold(false)}>
          <div className="triage-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="triage-modal-title">
              Auto-accept matches ≥ {thresholdPct}%
            </div>
            <p className="triage-modal-desc">
              This will accept <strong>all pending EOB matches</strong> with a confidence score
              of {thresholdPct}% or higher. This may affect many items.
            </p>
            <div className="triage-modal-actions">
              <Button size="sm" onClick={() => setPendingThreshold(false)}>Cancel</Button>
              <Button
                variant="success"
                size="sm"
                onClick={() => void executeBulkConfirmThreshold()}
              >
                ⚡ Auto-accept
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Keyboard shortcuts overlay (UX-11) */}
      {showShortcuts && (
        <div className="triage-modal-overlay" onClick={() => setShowShortcuts(false)}>
          <div className="triage-modal triage-shortcuts-modal" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
            <div className="triage-modal-title">
              ⌨️ Keyboard shortcuts
            </div>
            <div className="triage-shortcuts-grid">
              <div className="triage-shortcut-section">
                <div className="triage-shortcut-section-title">Actions</div>
                <div className="triage-shortcut-row"><kbd>Y</kbd><span>Accept selected item</span></div>
                <div className="triage-shortcut-row"><kbd>N</kbd><span>Reject selected item</span></div>
                <div className="triage-shortcut-row"><kbd>D</kbd><span>Review again in 7 days</span></div>
              </div>
              <div className="triage-shortcut-section">
                <div className="triage-shortcut-section-title">Navigation</div>
                <div className="triage-shortcut-row"><kbd>↑</kbd><span>Previous item</span></div>
                <div className="triage-shortcut-row"><kbd>↓</kbd><span>Next item</span></div>
                <div className="triage-shortcut-row"><kbd>S</kbd><span>Skip (next item)</span></div>
                <div className="triage-shortcut-row"><kbd>F</kbd><span>Toggle queue panel</span></div>
              </div>
              <div className="triage-shortcut-section">
                <div className="triage-shortcut-section-title">General</div>
                <div className="triage-shortcut-row"><kbd>?</kbd><span>Show this help</span></div>
                <div className="triage-shortcut-row"><kbd>Esc</kbd><span>Close modal / deselect</span></div>
              </div>
            </div>
            <div className="triage-modal-actions">
              <Button size="sm" onClick={() => setShowShortcuts(false)}>Close</Button>
            </div>
          </div>
        </div>
      )}

      {/* Keyboard hint bar */}
      <div className="triage-keyboard-hint">
        <kbd>Y</kbd> Accept · <kbd>N</kbd> Reject · <kbd>S</kbd> Skip · <kbd>D</kbd> Review later · <kbd>↑↓</kbd> Navigate · <kbd>F</kbd> Toggle queue ·{' '}
        <button type="button" className="triage-shortcuts-link" onClick={() => setShowShortcuts(true)}>
          <kbd>?</kbd> All shortcuts
        </button>
      </div>

      {/* Toast with optional undo */}
      {toast && (
        <div className="triage-toast-wrapper">
          <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />
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
