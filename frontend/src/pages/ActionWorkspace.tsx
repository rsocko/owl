import * as Popover from '@radix-ui/react-popover';
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  History,
  Maximize2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button, EmptyState, ErrorState, SkeletonLoader, Toast } from '../components/ui';
import OcrSummaryPanel from '../components/OcrSummaryPanel';
import { useOcrSummary } from '../hooks/useOcrSummary';
import { endpoints, type DocumentSummaryModel } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/document-workspace.css';

type LifecycleFilter = 'pending' | 'snoozed' | 'resolved';
type DueFilter = 'all' | 'overdue' | 'upcoming';
type WorkspaceTab = 'metadata' | 'actions' | 'related' | 'ocr';
type ActionType = 'PAY' | 'RESPOND' | 'REVIEW' | 'SCHEDULE' | 'SHARE' | 'SIGN' | 'TASK';
type ActionStatus = 'completed' | 'dismissed' | 'pending' | 'acknowledged' | 'snoozed' | 'not_an_action';

interface ActionHistoryEvent {
  id: number;
  event_type?: string | null;
  previous_status?: string | null;
  new_status?: string | null;
  created_at?: string | null;
}

interface LinkedDocument {
  document_id: number;
  role: string;
  title?: string | null;
  correspondent?: string | null;
  document_type?: string | null;
  document_date?: string | null;
  thumbnail_url?: string | null;
  preview_url?: string | null;
  document_summary?: DocumentSummaryModel;
}

interface ActionItem {
  id: number;
  document_id?: number | null;
  document_title?: string | null;
  action_type?: string | null;
  title?: string | null;
  summary?: string | null;
  payee_or_owner?: string | null;
  reference?: string | null;
  due_date?: string | null;
  amount?: number | null;
  urgency?: string | null;
  confidence?: number | null;
  status?: string | null;
  correspondent?: string | null;
  document_date?: string | null;
  document_type?: string | null;
  extracted_data?: {
    payment_url?: string | null;
    phone?: string | null;
    email?: string | null;
    reference_number?: string | null;
    links?: Array<{ url: string; label?: string | null; purpose?: string | null }> | null;
  } | null;
  ai_reasoning?: string | null;
  recommended_cta?: {
    id: string;
    label: string;
    url?: string | null;
    phone?: string | null;
    metadata?: Record<string, unknown>;
  } | null;
  preview_url?: string | null;
  version?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  snoozed_until?: string | null;
  action_index?: number | null;
  is_primary?: boolean;
  linked_documents?: LinkedDocument[];
  linked_document_count?: number;
  history?: ActionHistoryEvent[];
  obligation_id?: string | null;
}

type PaymentCheckState =
  | 'unchecked'
  | 'checking'
  | 'posted_match'
  | 'pending_match'
  | 'multiple_matches'
  | 'no_match'
  | 'partial_payment'
  | 'source_unavailable'
  | 'stale'
  | 'conflict'
  | 'possible_double_payment'
  | 'confirmed';

interface PaymentCandidate {
  id: string;
  payee?: string | null;
  amount?: number | null;
  currency?: string | null;
  transaction_date?: string | null;
  status?: 'posted' | 'pending' | null;
  account_display_name?: string | null;
  account_last_four?: string | null;
  confidence?: string | null;
  score?: number | null;
  match_reasons?: string[];
}

interface PaymentCheck {
  state: PaymentCheckState;
  checked_at?: string | null;
  stale_after?: string | null;
  source: string;
  revision: string;
  message?: string | null;
  candidates: PaymentCandidate[];
}

interface ActionListResponse {
  actions?: ActionItem[];
  total?: number;
}

interface QueueStatus {
  database?: {
    pending?: number;
    acknowledged?: number;
    completed?: number;
    dismissed?: number;
    snoozed?: number;
    not_an_action?: number;
  };
  read_only?: boolean;
}

interface ActionDraft {
  action_type: ActionType;
  title: string;
  summary: string;
  payee_or_owner: string;
  amount: string;
  due_date: string;
  reference: string;
}

interface DocumentGroup {
  documentId: number;
  actions: ActionItem[];
  matchingActions: ActionItem[];
  primary: ActionItem;
}

type ToastState = { message: string; tone?: 'success' | 'error' } | null;

const ACTION_TYPES: ActionType[] = ['PAY', 'RESPOND', 'REVIEW', 'SCHEDULE', 'SHARE', 'SIGN', 'TASK'];
const RESOLVED_STATUSES = new Set(['completed', 'dismissed', 'not_an_action']);

const FIELD_SCHEMA: Record<ActionType, {
  owner?: string;
  due: string;
  reference?: string;
  amount: boolean;
}> = {
  PAY: { owner: 'Payee', due: 'Pay by', reference: 'Invoice or reference', amount: true },
  RESPOND: { owner: 'Recipient', due: 'Respond by', reference: 'Case or reference', amount: false },
  REVIEW: { due: 'Review by', amount: false },
  SCHEDULE: { owner: 'With or provider', due: 'Event date', reference: 'Confirmation or reference', amount: false },
  SHARE: { owner: 'Recipient', due: 'Share by', amount: false },
  SIGN: { owner: 'Counterparty or recipient', due: 'Sign by', reference: 'Agreement or reference', amount: false },
  TASK: { owner: 'Owner', due: 'Due date', reference: 'Reference', amount: false },
};

function normalizedType(value?: string | null): ActionType {
  const normalized = (value || 'TASK').toUpperCase();
  return ACTION_TYPES.includes(normalized as ActionType) ? normalized as ActionType : 'TASK';
}

function normalizedStatus(value?: string | null) {
  return (value || 'pending').toLowerCase();
}

function draftFromAction(action: ActionItem): ActionDraft {
  return {
    action_type: normalizedType(action.action_type),
    title: action.title || '',
    summary: action.summary || '',
    payee_or_owner: action.payee_or_owner || action.correspondent || '',
    amount: action.amount == null ? '' : String(action.amount),
    due_date: action.due_date?.slice(0, 10) || '',
    reference: action.reference || action.extracted_data?.reference_number || '',
  };
}

function formatDate(value?: string | null) {
  if (!value) return 'No due date';
  const parsed = new Date(value.includes('T') ? value : `${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatCurrency(value?: number | null, currency = 'USD') {
  return value == null
    ? '—'
    : value.toLocaleString(undefined, { style: 'currency', currency });
}

function isOverdue(action: ActionItem) {
  if (!action.due_date) return false;
  const due = new Date(`${action.due_date.slice(0, 10)}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return due.getTime() < today.getTime();
}

function isUpcoming(action: ActionItem) {
  if (!action.due_date) return false;
  const due = new Date(`${action.due_date.slice(0, 10)}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const delta = (due.getTime() - today.getTime()) / 86400000;
  return delta > 14;
}

function statusMatches(action: ActionItem, filter: LifecycleFilter) {
  const status = normalizedStatus(action.status);
  if (filter === 'pending') return status === 'pending' || status === 'acknowledged';
  if (filter === 'snoozed') return status === 'snoozed';
  return RESOLVED_STATUSES.has(status);
}

function documentGroups(actions: ActionItem[], matching: ActionItem[]): DocumentGroup[] {
  const allByDocument = new Map<number, ActionItem[]>();
  for (const action of actions) {
    if (action.document_id == null) continue;
    const current = allByDocument.get(action.document_id) ?? [];
    current.push(action);
    allByDocument.set(action.document_id, current);
  }
  const groups = new Map<number, DocumentGroup>();
  for (const action of matching) {
    if (action.document_id == null) continue;
    const existing = groups.get(action.document_id);
    if (existing) {
      existing.matchingActions.push(action);
    } else {
      groups.set(action.document_id, {
        documentId: action.document_id,
        actions: allByDocument.get(action.document_id) ?? [action],
        matchingActions: [action],
        primary: action,
      });
    }
  }
  return [...groups.values()].sort((left, right) => {
    const leftDue = left.matchingActions.map((action) => action.due_date || '9999-12-31').sort()[0];
    const rightDue = right.matchingActions.map((action) => action.due_date || '9999-12-31').sort()[0];
    return leftDue.localeCompare(rightDue) || left.documentId - right.documentId;
  });
}

function nextMondayAtNine() {
  const date = new Date();
  const days = ((8 - date.getDay()) % 7) || 7;
  date.setDate(date.getDate() + days);
  date.setHours(9, 0, 0, 0);
  return date.toISOString();
}

export default function ActionWorkspace() {
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedActionId = Number(searchParams.get('action')) || null;
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [lifecycleFilter, setLifecycleFilter] = useState<LifecycleFilter>('pending');
  const [dueFilter, setDueFilter] = useState<DueFilter>('all');
  const [typeFilter, setTypeFilter] = useState<'all' | ActionType>('all');
  const [query, setQuery] = useState('');
  const [selectedActionId, setSelectedActionId] = useState<number | null>(linkedActionId);
  const [tab, setTab] = useState<WorkspaceTab>('actions');
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [draft, setDraft] = useState<ActionDraft | null>(null);
  const [savedDraft, setSavedDraft] = useState<ActionDraft | null>(null);
  const [newActionOpen, setNewActionOpen] = useState(false);
  const [newAction, setNewAction] = useState<ActionDraft>({
    action_type: 'TASK',
    title: '',
    summary: '',
    payee_or_owner: '',
    amount: '',
    due_date: '',
    reference: '',
  });
  const [confirmNotAction, setConfirmNotAction] = useState<number | null>(null);
  const [confirmAllDone, setConfirmAllDone] = useState(false);
  const [paymentCheck, setPaymentCheck] = useState<PaymentCheck | null>(null);
  const [paymentLoading, setPaymentLoading] = useState(false);
  const [confirmPayment, setConfirmPayment] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusResponse, firstPage] = await Promise.all([
        endpoints.actionQueue.status() as Promise<QueueStatus>,
        endpoints.actionQueue.actions('limit=200&offset=0&include_resolved_no_action=true') as Promise<ActionListResponse>,
      ]);
      const loaded = firstPage.actions ?? [];
      const total = firstPage.total ?? loaded.length;
      for (let offset = loaded.length; offset < total; offset += 200) {
        const page = await endpoints.actionQueue.actions(
          `limit=200&offset=${offset}&include_resolved_no_action=true`,
        ) as ActionListResponse;
        loaded.push(...(page.actions ?? []));
        if (!(page.actions ?? []).length) break;
      }
      const supported = loaded.filter((action) => !['FILE', 'ARCHIVE'].includes((action.action_type || '').toUpperCase()));
      setQueueStatus(statusResponse);
      setActions(supported);
      setSelectedActionId((current) => {
        if (current && supported.some((action) => action.id === current)) return current;
        return supported.find((action) => statusMatches(action, 'pending'))?.id ?? supported[0]?.id ?? null;
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load actions.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), getToastDuration(toast.tone));
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const matchingActions = useMemo(() => actions.filter((action) => {
    if (!statusMatches(action, lifecycleFilter)) return false;
    if (typeFilter !== 'all' && normalizedType(action.action_type) !== typeFilter) return false;
    if (dueFilter === 'overdue' && !isOverdue(action)) return false;
    if (dueFilter === 'upcoming' && !isUpcoming(action)) return false;
    if (query.trim()) {
      const needle = query.trim().toLowerCase();
      const haystack = [
        action.document_title,
        action.title,
        action.correspondent,
        action.document_type,
        action.action_type,
      ].filter(Boolean).join(' ').toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  }), [actions, dueFilter, lifecycleFilter, query, typeFilter]);

  const groups = useMemo(() => documentGroups(actions, matchingActions), [actions, matchingActions]);
  const selectedAction = useMemo(
    () => actions.find((action) => action.id === selectedActionId) ?? null,
    [actions, selectedActionId],
  );
  const ocrSummary = useOcrSummary(selectedAction?.document_id ?? null);
  const selectedGroup = useMemo(
    () => selectedAction?.document_id == null
      ? null
      : groups.find((group) => group.documentId === selectedAction.document_id)
        ?? documentGroups(actions, actions.filter((action) => action.document_id === selectedAction.document_id))[0]
        ?? null,
    [actions, groups, selectedAction],
  );
  const selectedGroupIndex = selectedGroup
    ? groups.findIndex((group) => group.documentId === selectedGroup.documentId)
    : -1;
  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft);

  useEffect(() => {
    if (!selectedAction) {
      setDraft(null);
      setSavedDraft(null);
      return;
    }
    const nextDraft = draftFromAction(selectedAction);
    setDraft(nextDraft);
    setSavedDraft(nextDraft);
    setNewActionOpen(false);
  }, [selectedAction]);

  useEffect(() => {
    if (!selectedAction || paymentCheck?.state !== 'checking') return;
    const poll = window.setInterval(() => {
      void endpoints.actionQueue.paymentCheck(String(selectedAction.id))
        .then((response) => {
          setPaymentCheck((response as { payment_check: PaymentCheck }).payment_check);
        })
        .catch(() => undefined);
    }, 5_000);
    return () => window.clearInterval(poll);
  }, [selectedAction, paymentCheck?.state]);

  useEffect(() => {
    if (!selectedAction || normalizedType(selectedAction.action_type) !== 'PAY' || !selectedAction.obligation_id) {
      setPaymentCheck(null);
      setPaymentLoading(false);
      return;
    }
    let active = true;
    setPaymentLoading(true);
    void endpoints.actionQueue.paymentCheck(String(selectedAction.id))
      .then((response) => {
        if (active) setPaymentCheck((response as { payment_check: PaymentCheck }).payment_check);
      })
      .catch(() => {
        if (active) setPaymentCheck(null);
      })
      .finally(() => {
        if (active) setPaymentLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedAction]);

  useEffect(() => {
    if (!selectedAction) return;
    setSearchParams((current) => {
      if (current.get('action') === String(selectedAction.id)) return current;
      const nextParams = new URLSearchParams(current);
      nextParams.set('action', String(selectedAction.id));
      return nextParams;
    }, { replace: true });
  }, [selectedAction, setSearchParams]);

  useEffect(() => {
    if (!groups.length) return;
    const selectedVisible = selectedAction?.document_id != null
      && groups.some((group) => group.documentId === selectedAction.document_id);
    if (!selectedVisible) setSelectedActionId(groups[0].primary.id);
  }, [groups, selectedAction?.document_id]);

  const selectAction = (actionId: number) => {
    if (dirty) {
      setToast({ message: 'Save or discard the selected action changes before switching.', tone: 'error' });
      return;
    }
    setSelectedActionId(actionId);
    setTab('actions');
  };

  const patchAction = async (
    actionId: number,
    payload: Record<string, unknown>,
    successMessage: string,
  ) => {
    setBusy(`action-${actionId}`);
    try {
      await endpoints.actionQueue.updateAction(String(actionId), payload);
      setToast({ message: successMessage, tone: 'success' });
      await loadData();
    } catch (updateError) {
      setToast({
        message: updateError instanceof Error ? updateError.message : 'Could not update action.',
        tone: 'error',
      });
    } finally {
      setBusy(null);
    }
  };

  const changeStatus = async (action: ActionItem, status: ActionStatus, snoozedUntil?: string) => {
    if (dirty && action.id === selectedActionId) {
      setToast({ message: 'Save action changes before choosing an outcome.', tone: 'error' });
      return;
    }
    if (status === 'not_an_action') {
      setConfirmNotAction(action.id);
      return;
    }
    await patchAction(action.id, {
      status,
      dry_run: false,
      version: action.version,
      ...(snoozedUntil ? { snoozed_until: snoozedUntil } : {}),
    }, status === 'completed'
      ? `${action.title || 'Action'} marked done.`
      : status === 'dismissed'
        ? `${action.title || 'Action'} marked won't do.`
        : status === 'snoozed'
          ? `${action.title || 'Action'} scheduled for later.`
          : `${action.title || 'Action'} moved to Pending.`);
  };

  const confirmNotActionOutcome = async () => {
    const action = actions.find((candidate) => candidate.id === confirmNotAction);
    if (!action) return;
    setConfirmNotAction(null);
    setBusy(`action-${action.id}`);
    try {
      await endpoints.actionQueue.feedback(String(action.id), { feedback_type: 'not_an_action' });
      setToast({ message: `${action.title || 'Action'} marked Not an action.`, tone: 'success' });
      await loadData();
    } catch (feedbackError) {
      setToast({
        message: feedbackError instanceof Error ? feedbackError.message : 'Could not save feedback.',
        tone: 'error',
      });
    } finally {
      setBusy(null);
    }
  };

  const saveAction = async () => {
    if (!selectedAction || !draft) return;
    if (!draft.title.trim()) {
      setToast({ message: 'Enter an action title before saving.', tone: 'error' });
      return;
    }
    const payload: Record<string, unknown> = {
      version: selectedAction.version,
      action_type: draft.action_type,
      title: draft.title.trim(),
      summary: draft.summary.trim() || null,
      payee_or_owner: draft.payee_or_owner.trim() || null,
      amount: draft.amount === '' ? null : Number(draft.amount),
      due_date: draft.due_date || null,
      reference: draft.reference.trim() || null,
    };
    await patchAction(selectedAction.id, payload, 'Action changes saved.');
  };

  const addAction = async () => {
    if (!selectedAction || !newAction.title.trim()) return;
    setBusy('new-action');
    try {
      const created = await endpoints.actionQueue.splitAction(String(selectedAction.id), {
        action_type: newAction.action_type,
        title: newAction.title.trim(),
        summary: newAction.summary.trim() || null,
        payee_or_owner: newAction.payee_or_owner.trim() || null,
        amount: newAction.amount === '' ? null : Number(newAction.amount),
        due_date: newAction.due_date || null,
        reference: newAction.reference.trim() || null,
        urgency: 'LOW',
      }) as ActionItem;
      setNewActionOpen(false);
      setNewAction({
        action_type: 'TASK',
        title: '',
        summary: '',
        payee_or_owner: '',
        amount: '',
        due_date: '',
        reference: '',
      });
      await loadData();
      setSelectedActionId(created.id);
      setToast({ message: 'Action added to this document.', tone: 'success' });
    } catch (createError) {
      setToast({
        message: createError instanceof Error ? createError.message : 'Could not add action.',
        tone: 'error',
      });
    } finally {
      setBusy(null);
    }
  };

  const markAllDone = async () => {
    if (!selectedGroup) return;
    const ids = selectedGroup.actions
      .filter((action) => !RESOLVED_STATUSES.has(normalizedStatus(action.status)))
      .map((action) => action.id);
    setConfirmAllDone(false);
    setBusy('all-done');
    try {
      await endpoints.actionQueue.bulk({ action: 'complete', action_ids: ids });
      setToast({ message: `${ids.length} actions marked done.`, tone: 'success' });
      await loadData();
    } catch (bulkError) {
      setToast({
        message: bulkError instanceof Error ? bulkError.message : 'Could not complete all actions.',
        tone: 'error',
      });
    } finally {
      setBusy(null);
    }
  };

  const checkForPayment = async () => {
    if (!selectedAction || !paymentCheck) return;
    setBusy(`payment-${selectedAction.id}`);
    try {
      const response = await endpoints.actionQueue.checkPayment(String(selectedAction.id), {
        revision: paymentCheck.revision,
        idempotency_key: `owl-check-payment:${selectedAction.id}:${paymentCheck.revision}`,
      }) as { payment: PaymentCheck };
      setPaymentCheck(response.payment);
      setToast({
        message: response.payment.state === 'posted_match'
          ? 'Posted payment candidate found. Confirm it before completing the action.'
          : response.payment.message || 'Payment check completed.',
        tone: 'success',
      });
    } catch (paymentError) {
      setToast({
        message: paymentError instanceof Error ? paymentError.message : 'Could not check for payment.',
        tone: 'error',
      });
    } finally {
      setBusy(null);
    }
  };

  const confirmPaymentAndComplete = async () => {
    if (!selectedAction || !paymentCheck) return;
    setConfirmPayment(false);
    setBusy(`payment-${selectedAction.id}`);
    try {
      await endpoints.actionQueue.confirmPaymentAndComplete(String(selectedAction.id), {
        revision: paymentCheck.revision,
        idempotency_key: `owl-confirm-payment:${selectedAction.id}:${paymentCheck.revision}`,
      });
      setToast({ message: 'Payment confirmed and action completed.', tone: 'success' });
      await loadData();
    } catch (paymentError) {
      setToast({
        message: paymentError instanceof Error
          ? paymentError.message
          : 'Could not confirm the payment.',
        tone: 'error',
      });
    } finally {
      setBusy(null);
    }
  };

  const nextDocument = () => {
    if (dirty) {
      setToast({ message: 'Save or discard changes before moving to another document.', tone: 'error' });
      return;
    }
    const next = groups[selectedGroupIndex + 1];
    if (!next) {
      setToast({ message: 'This is the last document in the current queue.' });
      return;
    }
    setSelectedActionId(next.primary.id);
  };

  const counts = useMemo(() => ({
    pending: actions.filter((action) => statusMatches(action, 'pending')).length,
    snoozed: actions.filter((action) => statusMatches(action, 'snoozed')).length,
    resolved: actions.filter((action) => statusMatches(action, 'resolved')).length,
  }), [actions]);

  if (loading && !actions.length) {
    return <div className="document-workspace-loading"><SkeletonLoader variant="table" /></div>;
  }
  if (error) return <ErrorState message={error} onRetry={() => void loadData()} />;

  const previewUrl = selectedAction?.document_id
    ? `${endpoints.documents.previewUrl(selectedAction.document_id)}#page=1&zoom=page-fit`
    : '';
  const actionTypes = ACTION_TYPES.map((type) => ({
    type,
    count: actions.filter((action) => statusMatches(action, lifecycleFilter) && normalizedType(action.action_type) === type).length,
  }));

  return (
    <div className={`document-workspace-page${railCollapsed ? ' rail-collapsed' : ''}`}>
      <aside className="document-workspace-rail" aria-label="Action documents">
        <header className="document-workspace-rail-header">
          <div>
            <h1>Actions</h1>
            <p>{groups.length} documents · {matchingActions.length} {lifecycleFilter === 'snoozed' ? 'reminder' : lifecycleFilter} actions</p>
          </div>
          <button
            type="button"
            className="document-workspace-collapse"
            onClick={() => setRailCollapsed((current) => !current)}
            aria-label={railCollapsed ? 'Expand action documents' : 'Collapse action documents'}
          >
            {railCollapsed ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}
          </button>
        </header>
        {!railCollapsed ? (
          <>
            <div className="document-workspace-tools">
              <label className="document-workspace-search">
                <Search size={16} />
                <input
                  aria-label="Search action documents"
                  placeholder="Find document, action, or correspondent"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <div className="workspace-filter-group">
                <span>Status</span>
                <div className="workspace-segments">
                  {([
                    ['pending', 'Pending', counts.pending],
                    ['snoozed', 'Remind later', counts.snoozed],
                    ['resolved', 'Resolved', counts.resolved],
                  ] as const).map(([value, label, count]) => (
                    <button
                      type="button"
                      key={value}
                      className={lifecycleFilter === value ? 'active' : ''}
                      onClick={() => setLifecycleFilter(value)}
                    >
                      {label} <b>{count}</b>
                    </button>
                  ))}
                </div>
              </div>
              <div className="workspace-filter-group">
                <span>Due window</span>
                <div className="workspace-chips">
                  {([
                    ['all', 'All'],
                    ['overdue', 'Overdue'],
                    ['upcoming', 'Upcoming'],
                  ] as const).map(([value, label]) => (
                    <button
                      type="button"
                      key={value}
                      className={dueFilter === value ? 'active' : ''}
                      onClick={() => setDueFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="workspace-filter-group">
                <span>Action type</span>
                <div className="workspace-chips">
                  <button
                    type="button"
                    className={typeFilter === 'all' ? 'active' : ''}
                    onClick={() => setTypeFilter('all')}
                  >
                    All <b>{actions.filter((action) => statusMatches(action, lifecycleFilter)).length}</b>
                  </button>
                  {actionTypes.map(({ type, count }) => (
                    <button
                      type="button"
                      key={type}
                      className={typeFilter === type ? 'active' : ''}
                      onClick={() => setTypeFilter(type)}
                    >
                      {type.charAt(0) + type.slice(1).toLowerCase()} <b>{count}</b>
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="document-workspace-queue">
              {groups.length ? groups.map((group) => {
                const selected = selectedAction?.document_id === group.documentId;
                const types = [...new Set(group.actions.map((action) => normalizedType(action.action_type)))];
                const pending = group.actions.filter((action) => statusMatches(action, 'pending')).length;
                const snoozed = group.actions.filter((action) => statusMatches(action, 'snoozed')).length;
                const resolved = group.actions.filter((action) => statusMatches(action, 'resolved')).length;
                const amount = group.matchingActions.reduce((sum, action) => sum + (action.amount || 0), 0);
                const lifecycle = [
                  pending ? `${pending} pending` : '',
                  snoozed ? `${snoozed} reminder${snoozed === 1 ? '' : 's'}` : '',
                  resolved ? `${resolved} resolved` : '',
                ].filter(Boolean).join(' · ');
                return (
                  <button
                    type="button"
                    key={group.documentId}
                    className={`document-workspace-queue-item${selected ? ' active' : ''}`}
                    onClick={() => selectAction(group.matchingActions[0]?.id ?? group.primary.id)}
                    aria-current={selected || undefined}
                  >
                    <img src={endpoints.statements.documentThumb(String(group.documentId))} alt="" loading="lazy" />
                    <span>
                      <strong>{group.primary.document_title || `Document #${group.documentId}`}</strong>
                      <small>{group.primary.correspondent || 'Correspondent not set'} · {group.primary.document_type || 'Type not set'}</small>
                      <em>{types.length > 1 ? `${group.actions.length} actions · ${types.join(' + ')}` : types[0]}</em>
                      <small>{lifecycle}</small>
                      <span className="document-workspace-queue-meta">
                        <b className={group.matchingActions.some(isOverdue) ? 'overdue' : ''}>
                          {formatDate(group.matchingActions[0]?.due_date)}
                        </b>
                        <strong>{amount ? formatCurrency(amount) : '—'}</strong>
                      </span>
                    </span>
                    <ChevronRight size={17} aria-hidden="true" />
                  </button>
                );
              }) : (
                <EmptyState title="No matching documents" desc="Try another status, due-window, type, or search filter." />
              )}
            </div>
          </>
        ) : null}
      </aside>

      <main className="document-workspace-main">
        {!selectedAction || !selectedGroup ? (
          <EmptyState title="Select a document" desc="Choose a document to review its actions and source evidence." />
        ) : (
          <>
            <header className="document-workspace-header">
              <div>
                <span>Document {selectedGroupIndex >= 0 ? selectedGroupIndex + 1 : '—'} of {groups.length} · Paperless #{selectedGroup.documentId}</span>
                <h2>{selectedAction.document_title || `Document #${selectedGroup.documentId}`}</h2>
              </div>
              <dl>
                <div><dt>Date</dt><dd>{formatDate(selectedAction.document_date)}</dd></div>
                <div><dt>Correspondent</dt><dd>{selectedAction.correspondent || 'Not set'}</dd></div>
                <div><dt>Type</dt><dd>{selectedAction.document_type || 'Not set'}</dd></div>
              </dl>
              <a className="btn ghost" href={selectedAction.preview_url || previewUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={16} /> Paperless
              </a>
            </header>

            <div className="document-workspace-content">
              <section className="document-workspace-preview" aria-label="Source document">
                {previewUrl ? <iframe title={`Preview of ${selectedAction.document_title || 'document'}`} src={previewUrl} /> : (
                  <div className="document-workspace-preview-empty"><FileText size={42} /><span>No document preview available</span></div>
                )}
                {previewUrl ? (
                  <a href={previewUrl} target="_blank" rel="noopener noreferrer"><Maximize2 size={15} /> Open preview</a>
                ) : null}
              </section>

              <section className="document-workspace-panel">
                <div className="document-workspace-tabs" role="tablist" aria-label="Document workspace sections">
                  {([
                    ['metadata', 'Metadata'],
                    ['actions', `Actions ${selectedGroup.actions.length}`],
                    ['related', `Related ${selectedAction.linked_document_count ?? selectedAction.linked_documents?.length ?? 0}`],
                    ['ocr', 'OCR'],
                  ] as const).map(([value, label]) => (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={tab === value}
                      className={tab === value ? 'active' : ''}
                      key={value}
                      onClick={() => setTab(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <div className="document-workspace-tab-content">
                  {tab === 'actions' ? (
                    <div className="action-workspace-tab">
                      <div className="action-set-header">
                        <div><strong>Actions on this document</strong><span>Each action has an independent lifecycle.</span></div>
                        <Button size="sm" onClick={() => setNewActionOpen((current) => !current)}>
                          <Plus size={15} /> {newActionOpen ? 'Cancel' : 'Add action'}
                        </Button>
                      </div>
                      {newActionOpen ? (
                        <ActionForm
                          draft={newAction}
                          onChange={setNewAction}
                          footer={(
                            <Button
                              variant="primary"
                              size="sm"
                              disabled={!newAction.title.trim() || busy === 'new-action'}
                              onClick={() => void addAction()}
                            >
                              Add action
                            </Button>
                          )}
                        />
                      ) : null}
                      <div className="action-set-list">
                        {selectedGroup.actions.map((action) => (
                          <div className="action-set-row" key={action.id}>
                            <button
                              type="button"
                              className={action.id === selectedAction.id ? 'active' : ''}
                              onClick={() => selectAction(action.id)}
                            >
                              <span><strong>{normalizedType(action.action_type)} · {action.title || `Action #${action.id}`}</strong><small>{formatDate(action.due_date)}</small></span>
                              <em>{normalizedStatus(action.status) === 'not_an_action' ? 'Not an action' : normalizedStatus(action.status)}</em>
                            </button>
                            <ActionOutcomeMenu
                              action={action}
                              disabled={busy !== null}
                              onStatus={(status, until) => void changeStatus(action, status, until)}
                              onHistory={() => {
                                selectAction(action.id);
                                document.querySelector<HTMLDetailsElement>('#selected-action-history')?.setAttribute('open', '');
                              }}
                            />
                          </div>
                        ))}
                      </div>

                      {draft ? (
                        <>
                          <div className="selected-action-context">
                            <div>
                              <span>{normalizedStatus(selectedAction.status) === 'not_an_action' ? 'Not an action' : normalizedStatus(selectedAction.status)}</span>
                              <span>{selectedAction.is_primary === false ? 'Manual or split action' : 'Inferred'}</span>
                              <span>{formatDate(selectedAction.due_date)}</span>
                            </div>
                            <strong>{Math.round(selectedAction.confidence ?? 0)}% confidence</strong>
                          </div>
                          <ActionForm
                            draft={draft}
                            onChange={setDraft}
                            footer={(
                              <div className="action-save-row">
                                <Button
                                  variant="primary"
                                  size="sm"
                                  disabled={!dirty || busy !== null}
                                  onClick={() => void saveAction()}
                                >
                                  Save action
                                </Button>
                                <span>{dirty ? 'Unsaved changes' : 'Saved'}</span>
                              </div>
                            )}
                          />
                          {normalizedType(selectedAction.action_type) === 'PAY' && selectedAction.obligation_id ? (
                            <PaymentCheckPanel
                              paymentCheck={paymentCheck}
                              loading={paymentLoading}
                              busy={busy === `payment-${selectedAction.id}`}
                              eligible={Boolean(
                                selectedAction.amount
                                && (selectedAction.payee_or_owner || selectedAction.correspondent)
                                && (selectedAction.due_date || selectedAction.document_date),
                              )}
                              onCheck={() => void checkForPayment()}
                              onConfirm={() => setConfirmPayment(true)}
                            />
                          ) : null}
                          <QuickWorkLinks action={selectedAction} />
                          <details className="action-disclosure">
                            <summary>Why OWL suggested this <ChevronDown size={16} /></summary>
                            <p>{selectedAction.ai_reasoning || 'No classifier explanation was recorded.'}</p>
                          </details>
                          <details className="action-disclosure" id="selected-action-history">
                            <summary><span><History size={15} /> Action history</span><ChevronDown size={16} /></summary>
                            {selectedAction.history?.length ? (
                              <ol>
                                {selectedAction.history.map((event) => (
                                  <li key={event.id}>
                                    <time>{formatDate(event.created_at)}</time>
                                    <span>{event.event_type || `${event.previous_status || 'unknown'} → ${event.new_status || 'unknown'}`}</span>
                                  </li>
                                ))}
                              </ol>
                            ) : <p>No lifecycle changes have been recorded.</p>}
                          </details>
                        </>
                      ) : null}
                    </div>
                  ) : null}

                  {tab === 'metadata' ? (
                    <div className="workspace-metadata">
                      <div className="workspace-panel-heading">
                        <div><h3>Document metadata</h3><p>Paperless remains authoritative.</p></div>
                        <a className="btn" href={`#/inbox-review?document=${selectedGroup.documentId}&tab=metadata`}>Edit in Inbox</a>
                      </div>
                      <dl>
                        <div><dt>Title</dt><dd>{selectedAction.document_title || 'Not set'}</dd></div>
                        <div><dt>Correspondent</dt><dd>{selectedAction.correspondent || 'Not set'}</dd></div>
                        <div><dt>Document type</dt><dd>{selectedAction.document_type || 'Not set'}</dd></div>
                        <div><dt>Document date</dt><dd>{formatDate(selectedAction.document_date)}</dd></div>
                      </dl>
                      <p className="workspace-scope-note">Resolving an action never files the document or changes Paperless metadata.</p>
                    </div>
                  ) : null}

                  {tab === 'related' ? (
                    <div className="workspace-related">
                      <div className="workspace-panel-heading"><div><h3>Related evidence</h3><p>Documents connected to this obligation.</p></div></div>
                      {selectedAction.linked_documents?.length ? selectedAction.linked_documents.map((document) => (
                        <a
                          key={document.document_id}
                          href={document.preview_url || endpoints.documents.previewUrl(document.document_id)}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <img src={document.thumbnail_url || endpoints.statements.documentThumb(String(document.document_id))} alt="" />
                          <span><strong>{document.title || `Document #${document.document_id}`}</strong><small>{document.role} · {document.correspondent || 'Unknown correspondent'}</small></span>
                          <ExternalLink size={16} />
                        </a>
                      )) : <EmptyState title="No related evidence" desc="No related documents are attached to this action." />}
                    </div>
                  ) : null}

                  {tab === 'ocr' ? (
                    <div className="workspace-ocr">
                      <OcrSummaryPanel documentId={selectedGroup.documentId} state={ocrSummary} />
                    </div>
                  ) : null}
                </div>
              </section>
            </div>

            <footer className="document-workspace-lifecycle">
              <div>
                <span className="lifecycle-dot" />
                <span><strong>{selectedAction.title || `Action #${selectedAction.id}`}</strong><small>{selectedGroup.actions.length > 1 ? 'These outcomes apply only to the selected action.' : 'Choose an outcome or move to the next document.'}</small></span>
              </div>
              {RESOLVED_STATUSES.has(normalizedStatus(selectedAction.status)) ? (
                <Button disabled={busy !== null} onClick={() => void changeStatus(selectedAction, 'pending')}>Move back to Pending</Button>
              ) : (
                <div className="lifecycle-buttons">
                  <Button
                    variant="ghost"
                    disabled={busy !== null || dirty}
                    onClick={() => setConfirmNotAction(selectedAction.id)}
                  >
                    Not an action
                  </Button>
                  <Button variant="ghost" disabled={busy !== null || dirty} onClick={() => void changeStatus(selectedAction, 'dismissed')}>Won't do</Button>
                  <Popover.Root>
                    <Popover.Trigger asChild>
                      <Button disabled={busy !== null || dirty}>Remind me later</Button>
                    </Popover.Trigger>
                    <Popover.Portal>
                      <Popover.Content className="workspace-popover" sideOffset={6}>
                        <button type="button" onClick={() => void changeStatus(selectedAction, 'snoozed', nextMondayAtNine())}>Next Monday at 9:00 AM</button>
                        <label>Pick a date<input type="date" onChange={(event) => {
                          if (!event.target.value) return;
                          void changeStatus(selectedAction, 'snoozed', `${event.target.value}T09:00:00`);
                        }} /></label>
                      </Popover.Content>
                    </Popover.Portal>
                  </Popover.Root>
                  <Button disabled={busy !== null} onClick={nextDocument}>Next doc</Button>
                  <div className="done-split-production">
                    <Button variant="primary" disabled={busy !== null || dirty} onClick={() => void changeStatus(selectedAction, 'completed')}>
                      <Check size={16} /> Done
                    </Button>
                    {selectedGroup.actions.filter((action) => !RESOLVED_STATUSES.has(normalizedStatus(action.status))).length > 1 ? (
                      <Popover.Root>
                        <Popover.Trigger asChild>
                          <Button variant="primary" aria-label="More completion options"><ChevronDown size={15} /></Button>
                        </Popover.Trigger>
                        <Popover.Portal>
                          <Popover.Content className="workspace-popover" sideOffset={6} align="end">
                            <button type="button" onClick={() => void changeStatus(selectedAction, 'completed')}>Mark selected action done</button>
                            <button type="button" onClick={() => setConfirmAllDone(true)}>Mark all pending actions done…</button>
                          </Popover.Content>
                        </Popover.Portal>
                      </Popover.Root>
                    ) : null}
                  </div>
                </div>
              )}
            </footer>
          </>
        )}
      </main>

      {confirmNotAction != null ? (
        <div className="workspace-dialog-backdrop">
          <section role="dialog" aria-modal="true" aria-labelledby="not-action-title" className="workspace-dialog">
            <h2 id="not-action-title">Mark as Not an action?</h2>
            <p>Use this when OWL incorrectly identified work that does not exist. This records classifier feedback without filing or changing the document.</p>
            <div><Button onClick={() => setConfirmNotAction(null)}>Cancel</Button><Button variant="primary" onClick={() => void confirmNotActionOutcome()}>Save feedback</Button></div>
          </section>
        </div>
      ) : null}

      {confirmAllDone && selectedGroup ? (
        <div className="workspace-dialog-backdrop">
          <section role="dialog" aria-modal="true" aria-labelledby="all-done-title" className="workspace-dialog">
            <h2 id="all-done-title">Mark every pending action done?</h2>
            <p>This completes each pending action attached to the current document:</p>
            <ul>{selectedGroup.actions.filter((action) => !RESOLVED_STATUSES.has(normalizedStatus(action.status))).map((action) => <li key={action.id}>{action.title}</li>)}</ul>
            <div><Button onClick={() => setConfirmAllDone(false)}>Cancel</Button><Button variant="primary" onClick={() => void markAllDone()}>Mark all done</Button></div>
          </section>
        </div>
      ) : null}

      {confirmPayment && selectedAction && paymentCheck ? (
        <div className="workspace-dialog-backdrop">
          <section role="dialog" aria-modal="true" aria-labelledby="confirm-payment-title" className="workspace-dialog">
            <h2 id="confirm-payment-title">Confirm this posted payment?</h2>
            <p>OWL will record your confirmation and complete the selected action and obligation in one operation.</p>
            <div>
              <Button onClick={() => setConfirmPayment(false)}>Cancel</Button>
              <Button variant="primary" onClick={() => void confirmPaymentAndComplete()}>
                Confirm payment and complete
              </Button>
            </div>
          </section>
        </div>
      ) : null}

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
      {queueStatus?.read_only ? <div className="workspace-read-only">Paperless writeback is currently read-only.</div> : null}
    </div>
  );
}

function PaymentCheckPanel({
  paymentCheck,
  loading,
  busy,
  eligible,
  onCheck,
  onConfirm,
}: {
  paymentCheck: PaymentCheck | null;
  loading: boolean;
  busy: boolean;
  eligible: boolean;
  onCheck: () => void;
  onConfirm: () => void;
}) {
  const candidate = paymentCheck?.candidates[0];
  const checking = busy || paymentCheck?.state === 'checking';
  const stateLabel: Record<PaymentCheckState, string> = {
    unchecked: 'Ready to check',
    checking: 'Checking accounts',
    posted_match: 'Posted payment found',
    pending_match: 'Pending payment found',
    multiple_matches: 'Multiple possible payments',
    no_match: 'No payment found',
    partial_payment: 'Partial payment found',
    source_unavailable: 'Payment source unavailable',
    stale: 'Payment result is stale',
    conflict: 'Conflicting payment evidence',
    possible_double_payment: 'Possible double payment',
    confirmed: 'Payment confirmed',
  };
  return (
    <section className={`payment-check-card state-${paymentCheck?.state || 'unchecked'}`} aria-label="Payment check">
      <div className="payment-check-heading">
        <div>
          <span>Payment reconciliation</span>
          <strong aria-live="polite">{loading ? 'Loading payment status…' : stateLabel[paymentCheck?.state || 'unchecked']}</strong>
        </div>
        <Button
          size="sm"
          disabled={!eligible || loading || checking}
          onClick={onCheck}
        >
          <RefreshCw size={15} className={checking ? 'spin' : ''} />
          {paymentCheck?.state === 'checking'
            ? 'Checking…'
            : paymentCheck?.state === 'unchecked'
              ? 'Check for payment'
              : 'Check again'}
        </Button>
      </div>
      <p>
        {!eligible
          ? 'Add a payee, amount, and due or bill date before checking for payment.'
          : paymentCheck?.message || 'Search all financial accounts except any configured exclusions for a matching transaction.'}
      </p>
      {candidate ? (
        <dl className="payment-candidate">
          <div><dt>Payee</dt><dd>{candidate.payee || 'Not provided'}</dd></div>
          <div><dt>Amount</dt><dd>{candidate.amount == null ? 'Not provided' : formatCurrency(candidate.amount, candidate.currency || 'USD')}</dd></div>
          <div><dt>Transaction</dt><dd>{formatDate(candidate.transaction_date)} · {candidate.status || 'unknown'}</dd></div>
          <div><dt>Account</dt><dd>{candidate.account_display_name || 'Eligible account'}{candidate.account_last_four ? ` •••• ${candidate.account_last_four}` : ''}</dd></div>
          <div><dt>Confidence</dt><dd>{candidate.confidence || 'Not scored'}{candidate.score == null ? '' : ` · ${candidate.score.toFixed(0)}%`}</dd></div>
          <div><dt>Match reasons</dt><dd>{candidate.match_reasons?.join(', ') || 'Not provided'}</dd></div>
        </dl>
      ) : null}
      {paymentCheck?.state === 'posted_match' ? (
        <div className="payment-check-confirm">
          <span>A posted match does not complete this obligation until you confirm it.</span>
          <Button variant="primary" size="sm" disabled={busy} onClick={onConfirm}>
            Confirm payment and complete
          </Button>
        </div>
      ) : null}
    </section>
  );
}

function ActionForm({
  draft,
  onChange,
  footer,
}: {
  draft: ActionDraft;
  onChange: (draft: ActionDraft) => void;
  footer: React.ReactNode;
}) {
  const schema = FIELD_SCHEMA[draft.action_type];
  return (
    <div className="action-workspace-form">
      <label><span>Type</span><select value={draft.action_type} onChange={(event) => onChange({ ...draft, action_type: event.target.value as ActionType })}>{ACTION_TYPES.map((type) => <option key={type}>{type}</option>)}</select></label>
      <label className="wide"><span>Title</span><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} /></label>
      {schema.owner ? <label><span>{schema.owner}</span><input value={draft.payee_or_owner} onChange={(event) => onChange({ ...draft, payee_or_owner: event.target.value })} /></label> : null}
      {schema.amount ? <label><span>Amount</span><input type="number" min="0" step="0.01" value={draft.amount} onChange={(event) => onChange({ ...draft, amount: event.target.value })} /></label> : null}
      <label><span>{schema.due}</span><input type="date" value={draft.due_date} onChange={(event) => onChange({ ...draft, due_date: event.target.value })} /></label>
      {schema.reference ? <label><span>{schema.reference}</span><input value={draft.reference} onChange={(event) => onChange({ ...draft, reference: event.target.value })} /></label> : null}
      <label className="wide"><span>Details</span><textarea rows={3} value={draft.summary} onChange={(event) => onChange({ ...draft, summary: event.target.value })} /></label>
      <div className="wide">{footer}</div>
    </div>
  );
}

function ActionOutcomeMenu({
  action,
  disabled,
  onStatus,
  onHistory,
}: {
  action: ActionItem;
  disabled: boolean;
  onStatus: (status: ActionStatus, snoozedUntil?: string) => void;
  onHistory: () => void;
}) {
  const resolved = RESOLVED_STATUSES.has(normalizedStatus(action.status));
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" className="action-set-menu-trigger" aria-label={`More outcomes for ${action.title || `action ${action.id}`}`} disabled={disabled}>
          <MoreHorizontal size={18} />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="workspace-popover" sideOffset={5} align="end">
          {resolved ? <button type="button" onClick={() => onStatus('pending')}>Move back to Pending</button> : (
            <>
              <button type="button" onClick={() => onStatus('completed')}>Mark done</button>
              <button type="button" onClick={() => onStatus('snoozed', nextMondayAtNine())}>Remind later</button>
              <button type="button" onClick={() => onStatus('not_an_action')}>Not an action</button>
              <button type="button" onClick={() => onStatus('dismissed')}>Won't do</button>
            </>
          )}
          <button type="button" onClick={onHistory}>View history</button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function QuickWorkLinks({ action }: { action: ActionItem }) {
  const links: Array<{ label: string; href: string }> = [];
  if (action.recommended_cta?.url) links.push({ label: action.recommended_cta.label || 'Open action link', href: action.recommended_cta.url });
  if (action.extracted_data?.payment_url) links.push({ label: 'Pay online', href: action.extracted_data.payment_url });
  if (action.extracted_data?.phone) links.push({ label: `Call ${action.extracted_data.phone}`, href: `tel:${action.extracted_data.phone}` });
  if (action.extracted_data?.email) links.push({ label: `Email ${action.extracted_data.email}`, href: `mailto:${action.extracted_data.email}` });
  for (const link of action.extracted_data?.links ?? []) {
    if (!/^https?:\/\//i.test(link.url)) continue;
    if (!links.some((candidate) => candidate.href === link.url)) links.push({ label: link.label || 'Open link', href: link.url });
  }
  if (!links.length) return null;
  return (
    <div className="action-work-links">
      <strong>Do the work</strong>
      <div>{links.map((link) => <a key={link.href} href={link.href} target={link.href.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer">{link.label}<ExternalLink size={14} /></a>)}</div>
      <p>Links and contact details are extracted from this document.</p>
    </div>
  );
}
