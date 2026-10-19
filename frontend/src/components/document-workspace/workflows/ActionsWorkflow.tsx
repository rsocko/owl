import * as Popover from '@radix-ui/react-popover';
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button, EmptyState, ErrorState, SkeletonLoader, Toast } from '../../ui';
import {
  DocumentWorkspaceFrame,
} from '../../DocumentWorkspace';
import DocumentWorkspaceDocumentView from '../DocumentWorkspaceDocumentView';
import DocumentActionsTab from '../tabs/DocumentActionsTab';
import DocumentMetadataTab from '../tabs/DocumentMetadataTab';
import DocumentOcrTab from '../tabs/DocumentOcrTab';
import DocumentRelatedTab from '../tabs/DocumentRelatedTab';
import {
  documentActionMutation,
  type DocumentActionDraft,
  type DocumentActionStatus,
} from '../../../lib/documentActions';
import OcrFocusInspector from '../../OcrFocusInspector';
import WorkspaceQueueControls from '../WorkspaceQueueControls';
import { useOcrSummary } from '../../../hooks/useOcrSummary';
import { endpoints, type DocumentSummaryModel } from '../../../lib/api';
import { getToastDuration } from '../../../lib/toast';
import '../../../styles/document-workspace.css';

type LifecycleFilter = 'pending' | 'snoozed' | 'resolved';
type DueFilter = 'all' | 'overdue' | 'upcoming';
type ActionSort = 'due_asc' | 'due_desc' | 'amount_desc' | 'recent';
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

interface DocumentGroup {
  documentId: number;
  actions: ActionItem[];
  matchingActions: ActionItem[];
  primary: ActionItem;
}

type ToastState = { message: string; tone?: 'success' | 'error' } | null;

const ACTION_TYPES: ActionType[] = ['PAY', 'RESPOND', 'REVIEW', 'SCHEDULE', 'SHARE', 'SIGN', 'TASK'];
const RESOLVED_STATUSES = new Set(['completed', 'dismissed', 'not_an_action']);

function normalizedType(value?: string | null): ActionType {
  const normalized = (value || 'TASK').toUpperCase();
  return ACTION_TYPES.includes(normalized as ActionType) ? normalized as ActionType : 'TASK';
}

function normalizedStatus(value?: string | null) {
  return (value || 'pending').toLowerCase();
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
  return [...groups.values()];
}

function groupDue(group: DocumentGroup, direction: 'asc' | 'desc') {
  const dates = group.matchingActions
    .map((action) => action.due_date?.slice(0, 10))
    .filter((value): value is string => Boolean(value))
    .sort();
  return direction === 'asc' ? dates[0] : dates.at(-1);
}

function groupAmount(group: DocumentGroup) {
  return group.matchingActions.reduce((sum, action) => sum + (action.amount ?? 0), 0);
}

function groupCreatedAt(group: DocumentGroup) {
  return Math.max(...group.matchingActions.map((action) => Date.parse(action.created_at ?? '') || 0));
}

function sortDocumentGroups(groups: DocumentGroup[], sort: ActionSort) {
  return [...groups].sort((left, right) => {
    if (sort === 'amount_desc') {
      return groupAmount(right) - groupAmount(left) || left.documentId - right.documentId;
    }
    if (sort === 'recent') {
      return groupCreatedAt(right) - groupCreatedAt(left) || left.documentId - right.documentId;
    }
    if (sort === 'due_desc') {
      const leftDue = groupDue(left, 'desc');
      const rightDue = groupDue(right, 'desc');
      if (!leftDue) return rightDue ? 1 : left.documentId - right.documentId;
      if (!rightDue) return -1;
      return rightDue.localeCompare(leftDue) || left.documentId - right.documentId;
    }
    const leftDue = groupDue(left, 'asc');
    const rightDue = groupDue(right, 'asc');
    if (!leftDue) return rightDue ? 1 : left.documentId - right.documentId;
    if (!rightDue) return -1;
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

export default function ActionsWorkflow() {
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedActionId = Number(searchParams.get('action')) || null;
  const ocrFocus = searchParams.get('inspect') === 'ocr';
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [lifecycleFilter, setLifecycleFilter] = useState<LifecycleFilter>('pending');
  const [dueFilters, setDueFilters] = useState<Array<Exclude<DueFilter, 'all'>>>([]);
  const [typeFilters, setTypeFilters] = useState<ActionType[]>([]);
  const [actionSort, setActionSort] = useState<ActionSort>('due_asc');
  const [query, setQuery] = useState('');
  const [selectedActionId, setSelectedActionId] = useState<number | null>(linkedActionId);
  const [tab, setTab] = useState<WorkspaceTab>('actions');
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [actionPanelDirty, setActionPanelDirty] = useState(false);
  const [confirmNotAction, setConfirmNotAction] = useState<number | null>(null);
  const [confirmAllDone, setConfirmAllDone] = useState(false);

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
    if (typeFilters.length && !typeFilters.includes(normalizedType(action.action_type))) return false;
    if (dueFilters.length && !dueFilters.some((filter) => (
      filter === 'overdue' ? isOverdue(action) : isUpcoming(action)
    ))) return false;
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
  }), [actions, dueFilters, lifecycleFilter, query, typeFilters]);

  const groups = useMemo(
    () => sortDocumentGroups(documentGroups(actions, matchingActions), actionSort),
    [actionSort, actions, matchingActions],
  );
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
    if (actionPanelDirty) {
      setToast({ message: 'Save the selected action before switching.', tone: 'error' });
      return;
    }
    setSelectedActionId(actionId);
    setTab('actions');
  };

  const openOcrFocus = () => {
    setRailCollapsed(true);
    setTab('ocr');
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('inspect', 'ocr');
      return next;
    }, { replace: true });
  };

  const closeOcrFocus = () => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete('inspect');
      return next;
    }, { replace: true });
    setTab('ocr');
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
    if (actionPanelDirty && action.id === selectedActionId) {
      setToast({ message: 'Save the selected action before choosing an outcome.', tone: 'error' });
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

  const nextDocument = () => {
    if (actionPanelDirty) {
      setToast({ message: 'Save the selected action before moving to the next document.', tone: 'error' });
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
  const dueOptions = ([
    ['overdue', 'Overdue', actions.filter((action) => statusMatches(action, lifecycleFilter) && isOverdue(action)).length],
    ['upcoming', 'Upcoming', actions.filter((action) => statusMatches(action, lifecycleFilter) && isUpcoming(action)).length],
  ] as const);

  const toggleDueFilter = (value: string) => {
    const filter = value as Exclude<DueFilter, 'all'>;
    setDueFilters((current) => current.includes(filter)
      ? current.filter((candidate) => candidate !== filter)
      : [...current, filter]);
  };

  const toggleTypeFilter = (value: string) => {
    const filter = value as ActionType;
    setTypeFilters((current) => current.includes(filter)
      ? current.filter((candidate) => candidate !== filter)
      : [...current, filter]);
  };

  return (
    <DocumentWorkspaceFrame
      collapsed={railCollapsed}
      className="action-document-workspace"
      railClassName="document-workspace-rail"
      rail={(
        <>
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
              <WorkspaceQueueControls
                search={{
                  value: query,
                  placeholder: 'Find document, action, or correspondent',
                  ariaLabel: 'Search action documents',
                  onChange: setQuery,
                }}
                primary={{
                  value: lifecycleFilter,
                  ariaLabel: 'Action status',
                  options: [
                    { value: 'pending', label: 'Pending', count: counts.pending },
                    { value: 'snoozed', label: 'Remind later', count: counts.snoozed },
                    { value: 'resolved', label: 'Resolved', count: counts.resolved },
                  ],
                  onChange: (value) => setLifecycleFilter(value as LifecycleFilter),
                }}
                filterLabel="Filter actions"
                filterGroups={[
                  {
                    label: 'Due window',
                    mode: 'multiple',
                    selected: dueFilters,
                    onSelect: toggleDueFilter,
                    options: dueOptions.map(([value, label, count]) => ({ value, label, count })),
                  },
                  {
                    label: 'Action type',
                    mode: 'multiple',
                    selected: typeFilters,
                    onSelect: toggleTypeFilter,
                    options: actionTypes.map(({ type, count }) => ({
                      value: type,
                      label: type.charAt(0) + type.slice(1).toLowerCase(),
                      count,
                    })),
                  },
                ]}
                activeFilters={[
                  ...dueFilters.map((filter) => {
                    const option = dueOptions.find(([value]) => value === filter);
                    return {
                      key: `due-${filter}`,
                      label: option?.[1] ?? filter,
                      count: option?.[2],
                      onRemove: () => toggleDueFilter(filter),
                    };
                  }),
                  ...typeFilters.map((filter) => {
                    const option = actionTypes.find(({ type }) => type === filter);
                    return {
                      key: `type-${filter}`,
                      label: filter.charAt(0) + filter.slice(1).toLowerCase(),
                      count: option?.count,
                      onRemove: () => toggleTypeFilter(filter),
                    };
                  }),
                ]}
                onClearFilters={() => {
                  setDueFilters([]);
                  setTypeFilters([]);
                }}
                sort={{
                  value: actionSort,
                  options: [
                    { value: 'due_asc', label: 'Oldest due first' },
                    { value: 'due_desc', label: 'Newest due first' },
                    { value: 'amount_desc', label: 'Highest amount' },
                    { value: 'recent', label: 'Recently added' },
                  ],
                  onChange: (value) => setActionSort(value as ActionSort),
                }}
              />
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
        </>
      )}
    >
        {ocrFocus && selectedGroup ? (
          <OcrFocusInspector documentId={selectedGroup.documentId} contextLabel="Action review" onReturn={closeOcrFocus} />
        ) : !selectedAction || !selectedGroup ? (
          <EmptyState title="Select a document" desc="Choose a document to review its actions and source evidence." />
        ) : (
          <>
            <DocumentWorkspaceDocumentView
              document={{
                id: selectedGroup.documentId,
                context: `Document ${selectedGroupIndex >= 0 ? selectedGroupIndex + 1 : '—'} of ${groups.length} · Paperless #${selectedGroup.documentId}`,
                title: selectedAction.document_title || `Document #${selectedGroup.documentId}`,
                date: formatDate(selectedAction.document_date),
                correspondent: selectedAction.correspondent || 'Not set',
                documentType: selectedAction.document_type || 'Not set',
              }}
              activeTab={tab}
              onTabChange={(value) => setTab(value as WorkspaceTab)}
              previewUrl={previewUrl}
              previewClassName="document-workspace-preview"
              panelClassName="document-workspace-panel"
              headerActions={(
                <a className="btn ghost" href={selectedAction.preview_url || previewUrl} target="_blank" rel="noopener noreferrer">
                  <ExternalLink size={16} /> Paperless
                </a>
              )}
              tabs={[
                {
                  key: 'metadata',
                  label: 'Metadata',
                  content: (
                    <DocumentMetadataTab
                      document={{
                        document_id: selectedGroup.documentId,
                        title: selectedAction.document_title,
                        correspondent: selectedAction.correspondent,
                        document_type: selectedAction.document_type,
                        document_date: selectedAction.document_date,
                        date_label: formatDate(selectedAction.document_date),
                      }}
                      editHref={`#/inbox?document=${selectedGroup.documentId}&tab=metadata`}
                      note="Resolving an action never files the document or changes Paperless metadata."
                    />
                  ),
                },
                {
                  key: 'actions',
                  label: `Actions ${selectedGroup.actions.length}`,
                  content: (
                    <DocumentActionsTab
                      actions={selectedGroup.actions}
                      selectedActionId={selectedAction.id}
                      onSelectAction={selectAction}
                      busy={busy !== null}
                      onDirtyChange={setActionPanelDirty}
                      onRefresh={loadData}
                      onSave={async (action, nextDraft) => {
                        setBusy(`action-${action.id}`);
                        try {
                          await endpoints.actionQueue.updateAction(String(action.id), {
                            version: action.version,
                            ...documentActionMutation(nextDraft),
                          });
                          await loadData();
                        } finally {
                          setBusy(null);
                        }
                      }}
                      onCreate={async (nextDraft: DocumentActionDraft) => {
                        setBusy('new-action');
                        try {
                          await endpoints.actionQueue.splitAction(String(selectedAction.id), documentActionMutation(nextDraft));
                          await loadData();
                        } finally {
                          setBusy(null);
                        }
                      }}
                      onStatus={(action, status: DocumentActionStatus, until) => changeStatus(action, status, until)}
                    />
                  ),
                },
                {
                  key: 'related',
                  label: `Related ${selectedAction.linked_document_count ?? selectedAction.linked_documents?.length ?? 0}`,
                  content: (
                    <DocumentRelatedTab
                      documents={selectedAction.linked_documents ?? []}
                      emptyDescription="No related documents are attached to this action."
                    />
                  ),
                },
                {
                  key: 'ocr',
                  label: 'OCR',
                  content: (
                    <DocumentOcrTab
                      documentId={selectedGroup.documentId}
                      state={ocrSummary}
                      onInspect={openOcrFocus}
                    />
                  ),
                },
              ]}
              footer={(
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
                    disabled={busy !== null || actionPanelDirty}
                    onClick={() => setConfirmNotAction(selectedAction.id)}
                  >
                    Not an action
                  </Button>
                  <Button variant="ghost" disabled={busy !== null || actionPanelDirty} onClick={() => void changeStatus(selectedAction, 'dismissed')}>Won't do</Button>
                  <Popover.Root>
                    <Popover.Trigger asChild>
                      <Button disabled={busy !== null || actionPanelDirty}>Remind me later</Button>
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
                  <Button disabled={busy !== null || actionPanelDirty} onClick={nextDocument}>Next doc</Button>
                  <div className="done-split-production">
                    <Button variant="primary" disabled={busy !== null || actionPanelDirty} onClick={() => void changeStatus(selectedAction, 'completed')}>
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
              )}
            />
          </>
        )}

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

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
      {queueStatus?.read_only ? <div className="workspace-read-only">Paperless writeback is currently read-only.</div> : null}
    </DocumentWorkspaceFrame>
  );
}
