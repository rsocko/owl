import { useCallback, useEffect, useMemo, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import {
  Badge,
  Button,
  ConfidenceBar,
  EmptyState,
  ErrorState,
  FilterPills,
  PageHeader,
  RiskScoreBar,
  SkeletonLoader,
  Toast,
  Tooltip,
} from '../components/ui';
import DocumentViewerModal from '../components/DocumentViewerModal';
import { MetadataTypeahead } from '../components/MetadataTypeahead';
import { useStreamingAction } from '../hooks/useStreamingAction';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/action-queue.css';
import '../styles/sortable-table.css';
import { buildQueueRunBody } from './actionQueueRunBody';
import { customReminderUntil, minimumReminderDate, reminderUntil } from './actionReminder';

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
  document_date?: string | null;
  document_type?: string | null;
  tags?: string[] | null;
  extracted_data?: {
    account_identifier?: string | null;
    payment_url?: string | null;
    phone?: string | null;
    email?: string | null;
    reference_number?: string | null;
    links?: Array<{ url: string; label?: string | null; purpose?: string | null }> | null;
  } | null;
  ai_reasoning?: string | null;
  preview_url?: string | null;
  version?: number | null;
  created_at?: string | null;
  completed_at?: string | null;
  acknowledged_at?: string | null;
  snoozed_until?: string | null;
  severity?: string | null;
  recommended_cta?: {
    id: string;
    label: string;
    url?: string | null;
    phone?: string | null;
    metadata?: Record<string, unknown>;
  } | null;
}

interface ActionListResponse {
  actions?: ActionItem[];
  total?: number;
}

interface PaperlessTag {
  id: number;
  name: string;
  colour?: string | null;
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
type QuickTypeFilter = 'all' | 'pay' | 'respond' | 'sign' | 'schedule' | 'file_archive';
export type DeadlineBucket = 'overdue' | 'today' | 'next7' | 'later' | 'no_due_date';

const ACTION_TYPE_OPTIONS = ['ARCHIVE', 'FILE', 'PAY', 'RESPOND', 'REVIEW', 'SCHEDULE', 'SHARE', 'SIGN', 'TASK'] as const;
const URGENCY_OPTIONS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const;
export const ACTION_GROUP_BATCH_SIZE = 15;
const ACTION_TYPE_PRIORITY: Record<string, number> = {
  PAY: 0,
  RESPOND: 1,
  SIGN: 2,
  SCHEDULE: 3,
  TASK: 4,
  REVIEW: 5,
  SHARE: 6,
  FILE: 7,
  ARCHIVE: 8,
};
const DEADLINE_BUCKETS: Array<{ key: DeadlineBucket; label: string }> = [
  { key: 'overdue', label: 'Overdue' },
  { key: 'today', label: 'Today' },
  { key: 'next7', label: 'Next 7 days' },
  { key: 'later', label: 'Later' },
  { key: 'no_due_date', label: 'No due date' },
];

function localDay(value: string): Date | null {
  const match = value.slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

export function deadlineBucket(action: ActionItem, now = new Date()): DeadlineBucket {
  if (!action.due_date) return 'no_due_date';
  const due = localDay(action.due_date);
  if (!due || Number.isNaN(due.getTime())) return 'no_due_date';
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const delta = Math.round((due.getTime() - today.getTime()) / 86400000);
  if (delta < 0) return 'overdue';
  if (delta === 0) return 'today';
  if (delta <= 7) return 'next7';
  return 'later';
}

export function groupAndSortActions(
  items: ActionItem[],
  now = new Date(),
): Record<DeadlineBucket, ActionItem[]> {
  const groups: Record<DeadlineBucket, ActionItem[]> = {
    overdue: [],
    today: [],
    next7: [],
    later: [],
    no_due_date: [],
  };
  for (const item of items) groups[deadlineBucket(item, now)].push(item);
  const compare = (left: ActionItem, right: ActionItem) => {
    const leftDue = left.due_date || '9999-12-31';
    const rightDue = right.due_date || '9999-12-31';
    if (leftDue !== rightDue) return leftDue.localeCompare(rightDue);
    const typeOrder =
      (ACTION_TYPE_PRIORITY[normalizeType(left.action_type)] ?? 99)
      - (ACTION_TYPE_PRIORITY[normalizeType(right.action_type)] ?? 99);
    if (typeOrder !== 0) return typeOrder;
    return (right.created_at || '').localeCompare(left.created_at || '') || right.id - left.id;
  };
  for (const group of Object.values(groups)) group.sort(compare);
  return groups;
}

function ReminderMenu({
  onSelect,
  disabled,
  size,
}: {
  onSelect: (until: string) => void;
  disabled?: boolean;
  size?: 'sm';
}) {
  const [open, setOpen] = useState(false);
  const [customDate, setCustomDate] = useState('');
  const minimumDate = minimumReminderDate();
  const customUntil = customReminderUntil(customDate);

  const choose = (until: string | null) => {
    if (!until) return;
    onSelect(until);
    setOpen(false);
    setCustomDate('');
  };

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button size={size} disabled={disabled}>Remind me later…</Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="aq-reminder-popover" sideOffset={5} align="start">
          <div className="aq-reminder-title">When should this return?</div>
          <Button size="sm" variant="ghost" onClick={() => choose(reminderUntil('tomorrow'))}>
            Tomorrow at 9:00 AM
          </Button>
          <Button size="sm" variant="ghost" onClick={() => choose(reminderUntil('next_week'))}>
            Next week at 9:00 AM
          </Button>
          <label className="aq-reminder-custom">
            <span>Pick a date</span>
            <input
              type="date"
              min={minimumDate}
              value={customDate}
              onChange={(event) => setCustomDate(event.target.value)}
            />
          </label>
          <Button size="sm" variant="primary" disabled={!customUntil} onClick={() => choose(customUntil)}>
            Set reminder
          </Button>
          <Popover.Arrow className="aq-type-picker-arrow" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function statusDisplayLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: 'Pending',
    acknowledged: 'Acknowledged (legacy)',
    completed: 'Done',
    dismissed: "Won't do",
    snoozed: 'Remind later',
    not_an_action: 'No action needed',
  };
  return labels[status] ?? status;
}

function bulkActionLabel(action: string): string {
  const labels: Record<string, string> = {
    complete: 'mark as done',
    dismiss: "mark as won't do",
    not_an_action: 'mark as no action needed',
    reopen: 'move back to pending',
    snooze: 'remind later',
  };
  return labels[action] ?? action;
}

const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
});

function formatCurrency(value?: number | null) {
  return typeof value === 'number' ? currencyFormatter.format(value) : '—';
}

function normalizeTagColour(value?: string | null): string | null {
  if (!value) return null;
  const match = value.trim().match(/^#?([0-9a-f]{6})$/i);
  return match ? `#${match[1]}` : null;
}

function tagTextColour(background: string): string {
  const red = Number.parseInt(background.slice(1, 3), 16);
  const green = Number.parseInt(background.slice(3, 5), 16);
  const blue = Number.parseInt(background.slice(5, 7), 16);
  return (red * 299 + green * 587 + blue * 114) / 1000 > 150 ? '#111827' : '#ffffff';
}

function PaperlessTagBadge({ label, tag }: { label: string; tag?: PaperlessTag }) {
  const colour = normalizeTagColour(tag?.colour);
  if (!colour) return <Badge tone="muted">{label}</Badge>;
  return (
    <span
      className="badge"
      style={{ backgroundColor: colour, borderColor: colour, color: tagTextColour(colour) }}
    >
      {label}
    </span>
  );
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

function safeWebUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function actionResources(action: ActionItem) {
  const resources: Array<{ url: string; label: string; purpose: string; primary: boolean }> = [];
  const seen = new Set<string>();
  const add = (urlValue: string | null | undefined, label: string | null | undefined, purpose: string, primary = false) => {
    const url = safeWebUrl(urlValue);
    if (!url || seen.has(url)) return;
    seen.add(url);
    resources.push({ url, label: label?.trim() || 'Open link', purpose, primary });
  };

  add(
    action.recommended_cta?.url,
    action.recommended_cta?.label,
    action.recommended_cta?.id || 'recommended',
    true,
  );
  add(action.extracted_data?.payment_url, 'Pay online', 'payment');
  for (const link of action.extracted_data?.links ?? []) {
    add(link.url, link.label, link.purpose || 'other');
  }
  return resources;
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
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [tagMetadata, setTagMetadata] = useState<PaperlessTag[]>([]);
  const [filter, setFilter] = useState<ActionFilter>('pending');
  const [search, setSearch] = useState('');
  const [quickTypeFilter, setQuickTypeFilter] = useState<QuickTypeFilter>(() => {
    const stored = window.localStorage.getItem('owl.actionQueue.typeFilter');
    return ['all', 'pay', 'respond', 'sign', 'schedule', 'file_archive'].includes(stored || '')
      ? stored as QuickTypeFilter
      : 'all';
  });
  const [collapsedGroups, setCollapsedGroups] = useState<Set<DeadlineBucket>>(new Set());
  const [visibleByGroup, setVisibleByGroup] = useState<Record<DeadlineBucket, number>>({
    overdue: ACTION_GROUP_BATCH_SIZE,
    today: ACTION_GROUP_BATCH_SIZE,
    next7: ACTION_GROUP_BATCH_SIZE,
    later: ACTION_GROUP_BATCH_SIZE,
    no_due_date: ACTION_GROUP_BATCH_SIZE,
  });
  const [selectedActionId, setSelectedActionId] = useState<number | null>(null);
  const [drawerExpanded, setDrawerExpanded] = useState(false);
  const [pdfViewerOpen, setPdfViewerOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);

  // [UX-STREAM] Pipeline streaming progress
  const [pipelineState, runPipelineStream] = useStreamingAction();

  // [ARCH-01] Bulk selection state
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
  const [pendingBulkAction, setPendingBulkAction] = useState<{ action: string; ids: number[] } | null>(null);
  const [pendingNoActionId, setPendingNoActionId] = useState<number | null>(null);

  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);

  // [UX-07] Cache selected action so filter changes don't lose it
  const [cachedAction, setCachedAction] = useState<ActionItem | null>(null);
  const [isEditingDetails, setIsEditingDetails] = useState(false);
  const [editDraft, setEditDraft] = useState<ActionEditDraft | null>(null);

  const [correspondents, setCorrespondents] = useState<Array<{
    id: number;
    name: string;
    suggested?: boolean;
  }>>([]);
  const [correspondentsDocumentId, setCorrespondentsDocumentId] = useState<
    number | null | undefined
  >(undefined);
  const correspondentOptions = useMemo(() => {
    const options = correspondents.map((correspondent) => ({
      value: correspondent.name,
      label: correspondent.suggested
        ? `${correspondent.name} (Paperless suggestion)`
        : correspondent.name,
    }));
    if (
      editDraft?.correspondent
      && !correspondents.some((correspondent) => correspondent.name === editDraft.correspondent)
    ) {
      options.push({ value: editDraft.correspondent, label: editDraft.correspondent });
    }
    return options;
  }, [correspondents, editDraft?.correspondent]);
  const tagsByValue = useMemo(() => {
    const lookup = new Map<string, PaperlessTag>();
    for (const tag of tagMetadata) {
      lookup.set(String(tag.id), tag);
      lookup.set(tag.name, tag);
    }
    return lookup;
  }, [tagMetadata]);
  const displayActions = useMemo(() => actions.map((action) => ({
    ...action,
    tags: action.tags?.map((value) => tagsByValue.get(String(value))?.name ?? String(value)),
  })), [actions, tagsByValue]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = filter === 'all'
        ? 'limit=200'
        : `status=${filter}&limit=200${filter === 'not_an_action' ? '&include_not_ready=true' : ''}`;
      const [statusResponse, actionsResponse, tagsResponse] = await Promise.all([
        endpoints.actionQueue.status() as Promise<QueueStatus>,
        endpoints.actionQueue.actions(params) as Promise<ActionListResponse>,
        endpoints.actionQueue.metadataTags() as Promise<{ tags?: PaperlessTag[] }>,
      ]);

      setStatus(statusResponse ?? null);
      setActions(actionsResponse?.actions ?? []);
      setTagMetadata(tagsResponse?.tags ?? []);
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
    return displayActions.filter((action) => {
      const type = normalizeType(action.action_type);
      const matchesType = quickTypeFilter === 'all'
        || type === quickTypeFilter.toUpperCase()
        || (quickTypeFilter === 'file_archive' && ['FILE', 'ARCHIVE'].includes(type));
      if (!matchesType) return false;
      if (!needle) return true;
      const haystack = [
        action.title,
        action.document_title,
        action.summary,
        action.correspondent,
        action.action_type,
        action.document_id,
        action.document_type,
        ...(action.tags || []),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [displayActions, quickTypeFilter, search]);

  useEffect(() => {
    window.localStorage.setItem('owl.actionQueue.typeFilter', quickTypeFilter);
  }, [quickTypeFilter]);

  const groupedActions = useMemo(() => groupAndSortActions(filteredActions), [filteredActions]);
  const checkedIncludesFilingAction = useMemo(
    () => displayActions.some(
      (action) => checkedIds.has(action.id)
        && ['FILE', 'ARCHIVE'].includes(normalizeType(action.action_type)),
    ),
    [checkedIds, displayActions],
  );

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
        if (pendingNoActionId !== null) { setPendingNoActionId(null); return; }
        if (pendingBulkAction) { setPendingBulkAction(null); return; }
        if (selectedActionId !== null) setSelectedActionId(null);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [selectedActionId, pendingBulkAction, pendingNoActionId]);

  // ------------------------------------------------------------------
  // Selection helpers (ARCH-01)
  // ------------------------------------------------------------------

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

  const rerunDocument = (action: ActionItem) => {
    const documentId = action.document_id;
    if (!documentId) {
      setToast({ message: 'This action is not linked to a Paperless document.', tone: 'error' });
      return;
    }

    const key = `rerun-document-${documentId}`;
    setBusyKey(key);
    runPipelineStream(
      endpoints.actionQueue.runStreamUrl,
      () => {
        setToast({ message: `Document #${documentId} analysis completed.`, tone: 'success' });
        setBusyKey(null);
        void loadData();
      },
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...buildQueueRunBody(false, documentId),
          document_id: documentId,
        }),
      },
    );
  };

  const refreshAction = async (actionId: number) => {
    const key = `refresh-action-${actionId}`;
    setBusyKey(key);
    try {
      const updatedAction = await endpoints.actionQueue.refreshAction(String(actionId)) as ActionItem;
      setActions((current) => current.map((action) => (
        action.id === actionId ? updatedAction : action
      )));
      setCachedAction((current) => (
        current?.id === actionId ? updatedAction : current
      ));
      setToast({ message: 'Document metadata refreshed from Paperless.', tone: 'success' });
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Document metadata refresh failed.',
        tone: 'error',
      });
    } finally {
      setBusyKey(null);
    }
  };

  const loadCorrectionMetadata = async (documentId?: number | null) => {
    const requestedDocumentId = documentId ?? null;
    if (correspondentsDocumentId === requestedDocumentId) return;
    try {
      const response = await endpoints.actionQueue.metadataCorrespondents(
        documentId ?? undefined,
      ) as {
        correspondents: Array<{ id: number; name: string; suggested?: boolean }>;
      };
      setCorrespondents(response.correspondents ?? []);
      setCorrespondentsDocumentId(requestedDocumentId);
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Could not load Paperless correspondents.',
        tone: 'error',
      });
    }
  };

  const patchAction = async (
    actionId: number,
    payload: Record<string, unknown>,
    successMessage: string,
    onSuccess?: () => void,
  ) => {
    setBusyKey(`action-${actionId}`);
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
    await patchAction(actionId, payload, `Action updated: ${statusDisplayLabel(nextStatus)}.`);
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
  const handleBulkAction = async (action: 'complete' | 'dismiss' | 'reopen' | 'not_an_action') => {
    const ids = Array.from(checkedIds);
    if (ids.length === 0) return;

    // Destructive actions require confirmation
    if (action === 'dismiss' || action === 'not_an_action') {
      setPendingBulkAction({ action, ids });
      return;
    }

    await executeBulkAction(action, ids);
  };

  const executeBulkAction = async (action: string, ids: number[], snoozedUntil?: string) => {
    setBusyKey(`bulk-${action}`);
    setBulkProgress({ done: 0, total: ids.length });
    try {
      const result = await endpoints.actionQueue.bulk({
        action,
        action_ids: ids,
        ...(snoozedUntil ? { snoozed_until: snoozedUntil } : {}),
      });
      setBulkProgress({ done: result.affected, total: ids.length });
      setCheckedIds(new Set());
      setToast({
        message: `${result.affected} action${result.affected !== 1 ? 's' : ''} updated: ${bulkActionLabel(action)}.`,
      });
      await loadData();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : `Bulk ${action} failed.`, tone: 'error' });
    } finally {
      setBusyKey(null);
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
      const label = feedbackType === 'not_an_action' ? 'Marked as no action needed. Feedback recorded.' : 'Feedback submitted.';
      setToast({ message: label });
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to submit feedback.', tone: 'error' });
    } finally {
      setBusyKey(null);
    }
  };

  const confirmNoAction = async () => {
    if (pendingNoActionId === null) return;
    const actionId = pendingNoActionId;
    setPendingNoActionId(null);
    await submitFeedback(actionId, 'not_an_action');
  };

  const fileDocument = async (actionId: number) => {
    setBusyKey(`file-${actionId}`);
    try {
      await endpoints.actionQueue.file(String(actionId));
      await loadData();
      handleClosePanel();
      setToast({ message: 'Filed in Paperless and marked done.', tone: 'success' });
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Paperless filing failed.',
        tone: 'error',
      });
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
  const totalActions = counts.total ?? 0;
  const pendingCount = (counts.pending ?? 0) + (counts.acknowledged ?? 0) + (counts.snoozed ?? 0);

  return (
    <>
      <PageHeader
        title="Action Queue"
        desc="Trusted, actionable work from your documents."
        actions={
          <div className="btn-group">
            <Button onClick={() => void loadData()} disabled={loading || busyKey !== null}>Refresh</Button>
            <Button variant="primary" onClick={() => runPipeline(false)} disabled={busyKey !== null || pipelineState.running}>
              {busyKey === 'run' ? 'Running…' : 'Run now'}
            </Button>
          </div>
        }
      />

      <div className="aq-compact-status" role="status">
        <span>
          <strong>{pipelineState.running ? 'Pipeline running' : `Pipeline ${status?.status ?? 'idle'}`}</strong>
          {' · '}
          {status?.finished_at
            ? `Last run ${formatDateTime(status.finished_at)}`
            : status?.started_at
              ? `Started ${formatDateTime(status.started_at)}`
              : 'No recorded run'}
        </span>
        <span>{status?.read_only ? 'Read only' : 'Paperless writeback enabled'}</span>
        <a href="#/action-queue/operations">Operations</a>
      </div>

      <div className="aq-toolbar">
        <FilterPills
          active={quickTypeFilter}
          onChange={(value) => setQuickTypeFilter(value as QuickTypeFilter)}
          options={[
            { key: 'all', label: 'All types' },
            { key: 'pay', label: 'Pay' },
            { key: 'respond', label: 'Respond' },
            { key: 'sign', label: 'Sign' },
            { key: 'schedule', label: 'Schedule' },
            { key: 'file_archive', label: 'File / Archive' },
          ]}
        />
        <FilterPills
          active={filter}
          onChange={(value) => setFilter(value as ActionFilter)}
          options={[
            { key: 'pending', label: `Pending (${pendingCount})` },
            { key: 'acknowledged', label: `Acknowledged - legacy (${counts.acknowledged ?? 0})` },
            { key: 'snoozed', label: `Remind later (${counts.snoozed ?? 0})` },
            { key: 'completed', label: `Done (${counts.completed ?? 0})` },
            { key: 'dismissed', label: `Won't do (${counts.dismissed ?? 0})` },
            { key: 'not_an_action', label: `No action needed (${counts.not_an_action ?? 0})` },
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
          <div className="aq-deadline-groups">
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
                  <ReminderMenu
                    size="sm"
                    disabled={busyKey !== null}
                    onSelect={(until) => void executeBulkAction('snooze', Array.from(checkedIds), until)}
                  />
                  <Tooltip label={checkedIncludesFilingAction ? 'File / Archive items must be filed in Paperless' : 'Mark as done — actions have been resolved'}>
                  <Button variant="success" size="sm" onClick={() => void handleBulkAction('complete')} disabled={busyKey !== null || checkedIncludesFilingAction}>
                    Done
                  </Button>
                  </Tooltip>
                  <Tooltip label="These are real tasks you are choosing not to do">
                  <Button variant="ghost" size="sm" onClick={() => void handleBulkAction('dismiss')} disabled={busyKey !== null}>
                    Won't do
                  </Button>
                  </Tooltip>
                  <Tooltip label="OWL incorrectly identified these documents as requiring action">
                  <Button variant="ghost" size="sm" onClick={() => void handleBulkAction('not_an_action')} disabled={busyKey !== null}>
                    No action needed
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
            ) : DEADLINE_BUCKETS.map(({ key, label }) => {
              const group = groupedActions[key];
              if (!group.length) return null;
              const collapsed = collapsedGroups.has(key);
              const visible = group.slice(0, visibleByGroup[key]);
              return (
                <section className={`aq-deadline-group aq-deadline-${key}`} key={key}>
                  <button
                    className="aq-group-heading"
                    type="button"
                    onClick={() => setCollapsedGroups((current) => {
                      const next = new Set(current);
                      if (next.has(key)) next.delete(key); else next.add(key);
                      return next;
                    })}
                    aria-expanded={!collapsed}
                  >
                    <span>{collapsed ? '▸' : '▾'} {label}</span>
                    <Badge tone={key === 'overdue' ? 'danger' : key === 'today' ? 'warning' : 'muted'}>
                      {group.length}
                    </Badge>
                  </button>
                  {!collapsed && (
                    <>
                      <div className="aq-action-list">
                        {visible.map((action) => (
                          <article className="aq-action-card" key={action.id}>
                            <button
                              className="aq-action-card-main"
                              type="button"
                              onClick={() => setSelectedActionId(action.id)}
                              aria-label={`Open ${action.title || action.document_title}`}
                            >
                              <div className="aq-action-card-title">
                                <Badge tone={actionTypeTone(normalizeType(action.action_type))}>
                                  {normalizeType(action.action_type)}
                                </Badge>
                                <strong>{action.title || action.document_title || `Action #${action.id}`}</strong>
                              </div>
                              <div className="aq-action-card-meta">
                                <span>{action.due_date ? `Due ${formatDate(action.due_date)}` : 'No due date'}</span>
                                {action.amount != null && <span>{formatCurrency(action.amount)}</span>}
                                {action.correspondent && <span>{action.correspondent}</span>}
                              </div>
                            </button>
                            <div className="aq-action-card-actions">
                              {['FILE', 'ARCHIVE'].includes(normalizeType(action.action_type)) ? (
                                <Button
                                  size="sm"
                                  variant="primary"
                                  disabled={busyKey !== null || status?.read_only}
                                  onClick={() => void fileDocument(action.id)}
                                >
                                  {status?.read_only ? 'Filing unavailable' : 'File in Paperless'}
                                </Button>
                              ) : (
                                <Button
                                  size="sm"
                                  variant="success"
                                  disabled={busyKey !== null}
                                  onClick={() => void updateAction(action.id, 'completed')}
                                >
                                  Done
                                </Button>
                              )}
                            </div>
                          </article>
                        ))}
                      </div>
                      {visible.length < group.length && (
                        <Button
                          size="sm"
                          onClick={() => setVisibleByGroup((current) => ({
                            ...current,
                            [key]: current[key] + ACTION_GROUP_BATCH_SIZE,
                          }))}
                        >
                          Show more {label.toLowerCase()}
                        </Button>
                      )}
                    </>
                  )}
                </section>
              );
            })}
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
                    <span style={{ display: 'inline-flex' }}>
                      <Popover.Trigger asChild>
                        <button className="aq-type-picker-trigger" disabled={busyKey !== null}>
                          <Badge tone={actionTypeTone(normalizeType(selectedAction.action_type))}>
                            {normalizeType(selectedAction.action_type)} ▾
                          </Badge>
                        </button>
                      </Popover.Trigger>
                    </span>
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

              {(actionResources(selectedAction).length > 0 || selectedAction.recommended_cta?.phone) && (
                <div className="aq-resources" aria-label="Action links">
                  <div className="section-title">Quick links</div>
                  <div className="aq-resource-list">
                    {actionResources(selectedAction).map((resource) => (
                      <a
                        className={`aq-resource-link${resource.primary ? ' primary' : ''}`}
                        href={resource.url}
                        key={resource.url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <span>{resource.label}</span>
                        <span aria-hidden="true">↗</span>
                      </a>
                    ))}
                    {selectedAction.recommended_cta?.phone && (
                      <a
                        className="aq-resource-link primary"
                        href={`tel:${selectedAction.recommended_cta.phone}`}
                      >
                        Call {selectedAction.recommended_cta.phone}
                      </a>
                    )}
                  </div>
                </div>
              )}

              <div className="aq-edit-section">
                <div className="aq-edit-header">
                  <div className="section-title">Something's wrong</div>
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
                        void loadCorrectionMetadata(selectedAction.document_id);
                      }
                    }}
                    disabled={busyKey !== null}
                  >
                    {isEditingDetails ? 'Cancel' : 'Correct details'}
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
                      <MetadataTypeahead
                        ariaLabel="Correspondent"
                        options={correspondentOptions}
                        value={editDraft.correspondent}
                        placeholder="Search correspondents…"
                        onChange={(correspondent) => setEditDraft((prev) => (
                          prev ? { ...prev, correspondent } : prev
                        ))}
                      />
                    </label>
                    <div className="aq-edit-actions">
                      <Button variant="success" size="sm" onClick={() => void saveActionDetails()} disabled={busyKey !== null}>
                        Save details
                      </Button>
                    </div>
                  </div>
                )}
                {!isEditingDetails && (
                  <div className="aq-exception-outcome">
                    <span className="text-muted">False positive, not merely the wrong action type?</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setPendingNoActionId(selectedAction.id)}
                      disabled={busyKey !== null}
                    >
                      No action needed
                    </Button>
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

              <div className="aq-edit-header">
                <div className="section-title">Document metadata</div>
                <div className="btn-group">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void refreshAction(selectedAction.id)}
                    disabled={busyKey !== null}
                    aria-busy={busyKey === `refresh-action-${selectedAction.id}`}
                  >
                    {busyKey === `refresh-action-${selectedAction.id}` && (
                      <span className="aq-spinner" aria-hidden="true" />
                    )}
                    {busyKey === `refresh-action-${selectedAction.id}`
                      ? 'Refreshing…'
                      : 'Refresh from Paperless'}
                  </Button>
                  {selectedAction.document_id && (
                    <Button
                      size="sm"
                      onClick={() => rerunDocument(selectedAction)}
                      disabled={busyKey !== null || pipelineState.running}
                      aria-busy={busyKey === `rerun-document-${selectedAction.document_id}`}
                      title="Force the Action Queue pipeline to analyze this document again"
                    >
                      {busyKey === `rerun-document-${selectedAction.document_id}`
                        ? 'Re-running…'
                        : 'Re-run analysis'}
                    </Button>
                  )}
                </div>
              </div>
              <div className="aq-meta-list">
                <div className="aq-meta-row"><span>Document</span><span>{selectedAction.document_title || `#${selectedAction.document_id ?? '—'}`}</span></div>
                <div className="aq-meta-row"><span>Correspondent</span><span>{selectedAction.correspondent || '—'}</span></div>
                <div className="aq-meta-row"><span>Document date</span><span>{formatDate(selectedAction.document_date)}</span></div>
                <div className="aq-meta-row"><span>Document type</span><span>{selectedAction.document_type || '—'}</span></div>
                <div className="aq-meta-row">
                  <span>Tags</span>
                  <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {selectedAction.tags?.length
                      ? selectedAction.tags.map((tag) => (
                          <PaperlessTagBadge key={tag} label={tag} tag={tagsByValue.get(tag)} />
                        ))
                      : '—'}
                  </span>
                </div>
                <div className="aq-meta-row"><span>Amount</span><span>{formatCurrency(selectedAction.amount)}</span></div>
                <div className="aq-meta-row"><span>Due date</span><span>{formatDate(selectedAction.due_date)}</span></div>
                {selectedAction.extracted_data?.account_identifier && <div className="aq-meta-row"><span>Account</span><span>{selectedAction.extracted_data.account_identifier}</span></div>}
                {selectedAction.extracted_data?.reference_number && <div className="aq-meta-row"><span>Reference</span><span>{selectedAction.extracted_data.reference_number}</span></div>}
                {selectedAction.extracted_data?.phone && <div className="aq-meta-row"><span>Phone</span><span><a href={`tel:${selectedAction.extracted_data.phone}`}>{selectedAction.extracted_data.phone}</a></span></div>}
                {selectedAction.extracted_data?.email && <div className="aq-meta-row"><span>Email</span><span><a href={`mailto:${selectedAction.extracted_data.email}`}>{selectedAction.extracted_data.email}</a></span></div>}
                <div className="aq-meta-row"><span>Severity</span><span><Badge tone={selectedAction.severity === 'critical' ? 'danger' : selectedAction.severity === 'focus' ? 'warning' : 'success'}>{selectedAction.severity ?? 'safe'}</Badge></span></div>
                <div className="aq-meta-row"><span>Created</span><span>{formatDateTime(selectedAction.created_at)}</span></div>
                {selectedAction.completed_at && <div className="aq-meta-row"><span>Done</span><span>{formatDateTime(selectedAction.completed_at)}</span></div>}
                {selectedAction.acknowledged_at && <div className="aq-meta-row"><span>Acknowledged (legacy)</span><span>{formatDateTime(selectedAction.acknowledged_at)}</span></div>}
                {selectedAction.snoozed_until && <div className="aq-meta-row"><span>Remind on</span><span>{formatDateTime(selectedAction.snoozed_until)}</span></div>}
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
                    {/* Lifecycle transition buttons */}
                    <div className="btn-group">
                      {['pending', 'acknowledged', 'snoozed'].includes(currentStatus)
                        && ['FILE', 'ARCHIVE'].includes(normalizeType(selectedAction.action_type)) && (
                        <Button
                          variant="primary"
                          onClick={() => void fileDocument(selectedAction.id)}
                          disabled={busyKey !== null || status?.read_only}
                          title={status?.read_only ? 'Paperless writes are disabled' : undefined}
                        >
                          {status?.read_only ? 'Filing unavailable (read only)' : 'File in Paperless'}
                        </Button>
                      )}
                      {['pending', 'acknowledged', 'snoozed'].includes(currentStatus) && (
                        <ReminderMenu
                          disabled={busyKey !== null}
                          onSelect={(until) => void updateAction(selectedAction.id, 'snoozed', until)}
                        />
                      )}
                      {['pending', 'acknowledged', 'snoozed'].includes(currentStatus)
                        && !['FILE', 'ARCHIVE'].includes(normalizeType(selectedAction.action_type)) && (
                        <Tooltip label="Mark as done — action has been resolved">
                        <Button
                          variant="success"
                          onClick={() => void updateAction(selectedAction.id, 'completed')}
                          disabled={busyKey !== null}
                        >
                          Done
                        </Button>
                        </Tooltip>
                      )}
                      {['pending', 'acknowledged', 'snoozed'].includes(currentStatus) && (
                        <Tooltip label="This was a real task, but you are choosing not to do it">
                        <Button
                          variant="danger"
                          onClick={() => void updateAction(selectedAction.id, 'dismissed')}
                          disabled={busyKey !== null}
                        >
                          Won't do
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

                  </div>
                );
              })()}
            </div>
          </>
        )}
      </div>

      {pendingNoActionId !== null && (
        <div className="aq-modal-overlay" onClick={() => setPendingNoActionId(null)}>
          <div
            className="aq-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="no-action-confirm-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div id="no-action-confirm-title" className="aq-modal-title">Mark as no action needed?</div>
            <p className="aq-modal-desc">
              OWL will preserve the rejected guess in feedback history, not as current metadata. Paperless
              keeps durable facts such as Document Amount and masked Account Identifier, while action-specific
              and legacy inferred fields are cleared.
            </p>
            <div className="aq-modal-actions">
              <Button size="sm" onClick={() => setPendingNoActionId(null)}>Cancel</Button>
              <Button variant="danger" size="sm" onClick={() => void confirmNoAction()}>
                No action needed
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* [ARCH-01] Confirmation modal for destructive bulk actions */}
      {pendingBulkAction && (
        <div className="aq-modal-overlay" onClick={() => setPendingBulkAction(null)}>
          <div className="aq-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="aq-modal-title">
              Confirm bulk action
            </div>
            <p className="aq-modal-desc">
              Are you sure you want to <strong>{bulkActionLabel(pendingBulkAction.action)}</strong>{' '}
              {pendingBulkAction.ids.length} action{pendingBulkAction.ids.length !== 1 ? 's' : ''}?
              {pendingBulkAction.action === 'not_an_action' && (
                <> OWL retains the extracted details. Paperless keeps Document Amount and clears Action Type,
                  Due Date, Urgency, Summary, and Action Count.</>
              )}
              This action cannot be easily undone in bulk.
            </p>
            <div className="aq-modal-actions">
              <Button size="sm" onClick={() => setPendingBulkAction(null)}>Cancel</Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => void confirmPendingBulkAction()}
              >
                {bulkActionLabel(pendingBulkAction.action)} for {pendingBulkAction.ids.length} actions
              </Button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </>
  );
}
