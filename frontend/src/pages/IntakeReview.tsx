import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Button, EmptyState, ErrorState, SkeletonLoader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/intake-review.css';

type ReviewTab = 'metadata' | 'actions' | 'ocr';
type Disposition = 'save' | 'defer' | 'file_only' | 'confirm_and_file';
type TaxonomyKind = 'tag' | 'correspondent' | 'document_type' | 'storage_path';

interface TaxonomyValue {
  id: number;
  name: string;
  colour?: string | null;
  data_type?: string | null;
}

interface IntakeListItem {
  id: number;
  title: string;
  created?: string | null;
  modified?: string | null;
  document_summary?: {
    correspondent?: string | null;
    document_type?: string | null;
  };
}

interface IntakeQueueResponse {
  documents: IntakeListItem[];
  total: number;
  monitor_tags: string[];
}

interface IntakeAction {
  id: number;
  document_id: number;
  action_type: string;
  title: string;
  payee_or_owner?: string | null;
  amount?: number | null;
  due_date?: string | null;
  reference?: string | null;
  status: string;
  version: number;
  origin: 'inferred' | 'user';
  confidence?: number | null;
  history?: Array<{
    id: number;
    event_type: string;
    previous_status?: string | null;
    new_status?: string | null;
    created_at?: string | null;
  }>;
}

interface Suggestion {
  field: 'title' | 'created' | 'correspondent' | 'document_type' | 'storage_path' | 'tags';
  source: 'paperless_ml' | 'paperless_ai';
  id?: number | null;
  name: string;
  value?: string;
  create?: boolean;
}

interface IntakeDetail {
  document: {
    id: number;
    title: string;
    created?: string | null;
    modified: string;
    correspondent?: number | null;
    document_type?: number | null;
    storage_path?: number | null;
    tags?: number[];
    custom_fields?: Array<{ field: number; value: unknown }>;
    correspondent_value?: TaxonomyValue | null;
    document_type_value?: TaxonomyValue | null;
    storage_path_value?: TaxonomyValue | null;
  };
  taxonomies: {
    tags: TaxonomyValue[];
    correspondents: TaxonomyValue[];
    document_types: TaxonomyValue[];
    storage_paths: TaxonomyValue[];
    custom_fields: TaxonomyValue[];
  };
  suggestions: Suggestion[];
  suggestion_sources: Record<string, { available: boolean; error?: string | null }>;
  actions: IntakeAction[];
  ocr: {
    quality_score?: number | null;
    quality_band: 'green' | 'amber' | 'red' | 'unknown';
    poor_quality: boolean;
    text: string;
  };
  analysis: {
    available: boolean;
    processed_at?: string | null;
    disposition?: string | null;
    source_modified?: string | null;
  };
  preview_url: string;
  write_enabled: boolean;
  monitor_tags: string[];
}

interface MetadataDraft {
  title: string;
  created: string;
  correspondent: string;
  document_type: string;
  storage_path: string;
  tags: string[];
}

interface ActionDraft {
  action_type: string;
  title: string;
  payee_or_owner: string;
  amount: string;
  due_date: string;
  reference: string;
}

const ACTION_TYPES = ['PAY', 'RESPOND', 'REVIEW', 'SCHEDULE', 'SHARE', 'SIGN', 'TASK'] as const;

function initialMetadata(detail: IntakeDetail): MetadataDraft {
  return {
    title: detail.document.title || '',
    created: detail.document.created?.slice(0, 10) || '',
    correspondent: detail.document.correspondent?.toString() || '',
    document_type: detail.document.document_type?.toString() || '',
    storage_path: detail.document.storage_path?.toString() || '',
    tags: (detail.document.tags || []).map(String),
  };
}

function actionDraft(action: IntakeAction): ActionDraft {
  return {
    action_type: action.action_type || 'TASK',
    title: action.title || '',
    payee_or_owner: action.payee_or_owner || '',
    amount: action.amount == null ? '' : String(action.amount),
    due_date: action.due_date?.slice(0, 10) || '',
    reference: action.reference || '',
  };
}

function optionName(values: TaxonomyValue[], id: string): string {
  return values.find((value) => String(value.id) === id)?.name || 'None';
}

function formatDate(value?: string | null): string {
  if (!value) return 'No date';
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The operation failed.';
}

function TaxonomySelect({
  label,
  value,
  values,
  disabled,
  allowClear = true,
  onChange,
  onCreate,
}: {
  label: string;
  value: string;
  values: TaxonomyValue[];
  disabled: boolean;
  allowClear?: boolean;
  onChange: (value: string) => void;
  onCreate: (name: string) => Promise<void>;
}) {
  const [createName, setCreateName] = useState('');
  const [creating, setCreating] = useState(false);
  return (
    <div className="intake-field">
      <label>
        <span>{label}</span>
        <select
          aria-label={label}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {allowClear ? <option value="">None</option> : null}
          {values.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
      </label>
      <div className="intake-create-row">
        <input
          aria-label={`New ${label.toLowerCase()} name`}
          placeholder={`Create ${label.toLowerCase()}`}
          value={createName}
          disabled={disabled || creating}
          onChange={(event) => setCreateName(event.target.value)}
        />
        <Button
          size="sm"
          disabled={disabled || creating || !createName.trim()}
          onClick={() => {
            setCreating(true);
            void onCreate(createName)
              .then(() => setCreateName(''))
              .finally(() => setCreating(false));
          }}
        >
          {creating ? 'Creating…' : 'Create'}
        </Button>
      </div>
    </div>
  );
}

function ActionEditor({
  action,
  busy,
  onSave,
  onStatus,
  onDelete,
}: {
  action: IntakeAction;
  busy: boolean;
  onSave: (action: IntakeAction, draft: ActionDraft) => Promise<void>;
  onStatus: (action: IntakeAction, status: string) => Promise<void>;
  onDelete: (action: IntakeAction) => Promise<void>;
}) {
  const [draft, setDraft] = useState(() => actionDraft(action));
  useEffect(() => setDraft(actionDraft(action)), [action]);
  const completed = action.status === 'completed';
  const inactive = completed || ['not_an_action', 'dismissed'].includes(action.status);
  return (
    <article className={`intake-action-card${inactive ? ' resolved' : ''}`}>
      <div className="intake-action-heading">
        <div>
          <span className={`intake-action-status ${action.status}`}>{action.status.replaceAll('_', ' ')}</span>
          <span className="intake-action-origin">{action.origin === 'user' ? 'Added by you' : 'Inferred'}</span>
        </div>
        {action.confidence != null ? <span>{action.confidence}% confidence</span> : null}
      </div>
      <div className="intake-action-grid">
        <label>
          <span>Type</span>
          <select
            value={draft.action_type}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, action_type: event.target.value })}
          >
            {ACTION_TYPES.map((type) => <option key={type}>{type}</option>)}
          </select>
        </label>
        <label className="intake-action-title">
          <span>Title</span>
          <input
            value={draft.title}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          />
        </label>
        <label>
          <span>Payee or owner</span>
          <input
            value={draft.payee_or_owner}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, payee_or_owner: event.target.value })}
          />
        </label>
        <label>
          <span>Amount</span>
          <input
            type="number"
            min="0"
            step="0.01"
            value={draft.amount}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, amount: event.target.value })}
          />
        </label>
        <label>
          <span>Due date</span>
          <input
            type="date"
            value={draft.due_date}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, due_date: event.target.value })}
          />
        </label>
        <label>
          <span>Reference</span>
          <input
            value={draft.reference}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, reference: event.target.value })}
          />
        </label>
      </div>
      <div className="intake-action-controls">
        <Button size="sm" variant="primary" disabled={busy || !draft.title.trim()} onClick={() => void onSave(action, draft)}>
          Save action
        </Button>
        {completed ? (
          <Button size="sm" disabled={busy} onClick={() => void onStatus(action, 'pending')}>
            Undo completion
          </Button>
        ) : (
          <Button size="sm" disabled={busy || inactive} onClick={() => void onStatus(action, 'completed')}>
            Mark done
          </Button>
        )}
        {action.origin === 'inferred' ? (
          <Button size="sm" disabled={busy || action.status === 'not_an_action'} onClick={() => void onStatus(action, 'not_an_action')}>
            Not an action
          </Button>
        ) : (
          <Button size="sm" variant="danger" disabled={busy} onClick={() => void onDelete(action)}>
            Delete
          </Button>
        )}
      </div>
      {action.history?.length ? (
        <details className="intake-action-history">
          <summary>Audit history ({action.history.length})</summary>
          <ol>
            {action.history.map((event) => (
              <li key={event.id}>
                <strong>{event.event_type.replaceAll('_', ' ')}</strong>
                {event.previous_status || event.new_status
                  ? `: ${event.previous_status || 'none'} → ${event.new_status || 'none'}`
                  : ''}
                {event.created_at ? <time>{new Date(event.created_at).toLocaleString()}</time> : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </article>
  );
}

export default function IntakeReview() {
  const { documentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab');
  const [tab, setTab] = useState<ReviewTab>(
    requestedTab === 'actions' || requestedTab === 'ocr' ? requestedTab : 'metadata',
  );
  const [queue, setQueue] = useState<IntakeListItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(
    documentId ? Number(documentId) : null,
  );
  const [focusedId, setFocusedId] = useState<number | null>(selectedId);
  const [detail, setDetail] = useState<IntakeDetail | null>(null);
  const [metadata, setMetadata] = useState<MetadataDraft | null>(null);
  const [baseline, setBaseline] = useState<MetadataDraft | null>(null);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [queueError, setQueueError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [busyKey, setBusyKey] = useState('');
  const [toast, setToast] = useState<{ message: string; tone?: 'success' | 'error' } | null>(null);
  const [newActionOpen, setNewActionOpen] = useState(false);
  const [newAction, setNewAction] = useState<ActionDraft>({
    action_type: 'TASK',
    title: '',
    payee_or_owner: '',
    amount: '',
    due_date: '',
    reference: '',
  });
  const queueButtonRefs = useRef(new Map<number, HTMLButtonElement>());

  const dirty = useMemo(
    () => Boolean(metadata && baseline && JSON.stringify(metadata) !== JSON.stringify(baseline)),
    [baseline, metadata],
  );

  const loadQueue = useCallback(async () => {
    setLoadingQueue(true);
    setQueueError('');
    try {
      const response = await endpoints.intake.documents() as IntakeQueueResponse;
      setQueue(response.documents);
      setSelectedId((current) => current ?? response.documents[0]?.id ?? null);
      setFocusedId((current) => current ?? response.documents[0]?.id ?? null);
    } catch (error) {
      setQueueError(errorMessage(error));
    } finally {
      setLoadingQueue(false);
    }
  }, []);

  const loadDetail = useCallback(async (id: number) => {
    setLoadingDetail(true);
    setDetailError('');
    try {
      const response = await endpoints.intake.document(id) as IntakeDetail;
      const nextMetadata = initialMetadata(response);
      setDetail(response);
      setMetadata(nextMetadata);
      setBaseline(nextMetadata);
    } catch (error) {
      setDetail(null);
      setDetailError(errorMessage(error));
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => { void loadQueue(); }, [loadQueue]);
  useEffect(() => {
    if (selectedId != null) void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  const showToast = useCallback((message: string, tone: 'success' | 'error' = 'success') => {
    setToast({ message, tone });
    const duration = getToastDuration(tone);
    if (duration) window.setTimeout(() => setToast(null), duration);
  }, []);

  const chooseTab = (next: ReviewTab) => {
    setTab(next);
    const nextParams = new URLSearchParams(searchParams);
    if (next === 'metadata') nextParams.delete('tab');
    else nextParams.set('tab', next);
    setSearchParams(nextParams, { replace: true });
  };

  const selectDocument = (id: number) => {
    if (dirty && !window.confirm('Discard unsaved metadata changes and open another document?')) return;
    setSelectedId(id);
    setFocusedId(id);
  };

  const moveQueueFocus = (direction: -1 | 1) => {
    if (!queue.length) return;
    const currentIndex = Math.max(0, queue.findIndex((item) => item.id === focusedId));
    const nextIndex = Math.min(queue.length - 1, Math.max(0, currentIndex + direction));
    const nextId = queue[nextIndex].id;
    setFocusedId(nextId);
    queueButtonRefs.current.get(nextId)?.focus();
  };

  const applySuggestion = async (suggestion: Suggestion) => {
    if (!metadata) return;
    if (suggestion.create && suggestion.id == null) {
      const kind = suggestion.field === 'tags' ? 'tag' : suggestion.field as TaxonomyKind;
      setBusyKey(`suggestion-${suggestion.field}-${suggestion.name}`);
      try {
        const response = await endpoints.intake.createTaxonomy(kind, suggestion.name) as { value: TaxonomyValue };
        const value = response.value;
        setDetail((current) => current ? {
          ...current,
          taxonomies: {
            ...current.taxonomies,
            [suggestion.field === 'tags' ? 'tags' : `${suggestion.field}s`]:
              [...current.taxonomies[suggestion.field === 'tags' ? 'tags' : `${suggestion.field}s` as keyof IntakeDetail['taxonomies']] as TaxonomyValue[], value],
          },
        } : current);
        setMetadata((current) => current ? {
          ...current,
          [suggestion.field]: suggestion.field === 'tags'
            ? [...current.tags, String(value.id)]
            : String(value.id),
        } : current);
      } catch (error) {
        showToast(errorMessage(error), 'error');
      } finally {
        setBusyKey('');
      }
      return;
    }
    setMetadata({
      ...metadata,
      [suggestion.field]: suggestion.field === 'tags'
        ? Array.from(new Set([...metadata.tags, String(suggestion.id)]))
        : suggestion.id != null
          ? String(suggestion.id)
          : suggestion.value || suggestion.name,
    });
  };

  const save = async (disposition: Disposition) => {
    if (!detail || !metadata) return;
    setBusyKey(disposition);
    try {
      const response = await endpoints.intake.saveDocument(detail.document.id, {
        expected_modified: detail.document.modified,
        metadata: {
          title: metadata.title,
          created: metadata.created || null,
          correspondent: metadata.correspondent ? Number(metadata.correspondent) : null,
          document_type: metadata.document_type ? Number(metadata.document_type) : null,
          storage_path: metadata.storage_path ? Number(metadata.storage_path) : null,
          tags: metadata.tags.map(Number),
        },
        disposition,
        action_versions: disposition === 'confirm_and_file'
          ? Object.fromEntries(
            detail.actions
              .filter((action) => !['completed', 'not_an_action', 'dismissed'].includes(action.status))
              .map((action) => [action.id, action.version]),
          )
          : {},
      }) as { document: IntakeDetail['document']; filed: boolean };
      showToast(
        disposition === 'defer'
          ? 'Changes saved; document kept in the Inbox.'
          : response.filed
            ? 'Document filed.'
            : 'Changes saved.',
      );
      if (response.filed || disposition === 'defer') {
        const currentIndex = queue.findIndex((item) => item.id === detail.document.id);
        await loadQueue();
        const next = queue[currentIndex + 1] || queue[currentIndex - 1];
        if (next) setSelectedId(next.id);
        else if (response.filed) {
          setSelectedId(null);
          setDetail(null);
        } else {
          await loadDetail(detail.document.id);
        }
      } else {
        await loadDetail(detail.document.id);
      }
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const createTaxonomy = async (kind: TaxonomyKind, name: string) => {
    try {
      const response = await endpoints.intake.createTaxonomy(kind, name) as { value: TaxonomyValue };
      const key = kind === 'tag' ? 'tags' : `${kind}s` as keyof IntakeDetail['taxonomies'];
      setDetail((current) => current ? {
        ...current,
        taxonomies: { ...current.taxonomies, [key]: [...current.taxonomies[key], response.value] },
      } : current);
      setMetadata((current) => current ? {
        ...current,
        [kind === 'tag' ? 'tags' : kind]: kind === 'tag'
          ? [...current.tags, String(response.value.id)]
          : String(response.value.id),
      } : current);
      showToast(`${response.value.name} created.`);
    } catch (error) {
      showToast(errorMessage(error), 'error');
      throw error;
    }
  };

  const updateAction = async (action: IntakeAction, draft: ActionDraft) => {
    setBusyKey(`action-${action.id}`);
    try {
      const updated = await endpoints.actionQueue.updateAction(String(action.id), {
        version: action.version,
        action_type: draft.action_type,
        title: draft.title.trim(),
        payee_or_owner: draft.payee_or_owner.trim() || null,
        amount: draft.amount ? Number(draft.amount) : null,
        due_date: draft.due_date || null,
        reference: draft.reference.trim() || null,
      }) as IntakeAction;
      setDetail((current) => current ? {
        ...current,
        actions: current.actions.map((item) => item.id === updated.id ? updated : item),
      } : current);
      showToast('Action saved.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const updateActionStatus = async (action: IntakeAction, status: string) => {
    setBusyKey(`action-${action.id}`);
    try {
      const updated = await endpoints.actionQueue.updateAction(String(action.id), {
        version: action.version,
        status,
      }) as IntakeAction;
      setDetail((current) => current ? {
        ...current,
        actions: current.actions.map((item) => item.id === updated.id ? updated : item),
      } : current);
      showToast(status === 'completed' ? 'Action marked done.' : status === 'pending' ? 'Action restored.' : 'Action updated.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const deleteAction = async (action: IntakeAction) => {
    setBusyKey(`action-${action.id}`);
    try {
      await endpoints.intake.deleteAction(action.id);
      setDetail((current) => current ? {
        ...current,
        actions: current.actions.filter((item) => item.id !== action.id),
      } : current);
      showToast('Action deleted.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const addAction = async () => {
    if (!detail) return;
    setBusyKey('new-action');
    try {
      const created = await endpoints.intake.createAction(detail.document.id, {
        action_type: newAction.action_type,
        title: newAction.title.trim(),
        payee_or_owner: newAction.payee_or_owner.trim() || null,
        amount: newAction.amount ? Number(newAction.amount) : null,
        due_date: newAction.due_date || null,
        reference: newAction.reference.trim() || null,
      }) as IntakeAction;
      setDetail((current) => current ? { ...current, actions: [...current.actions, created] } : current);
      setNewAction({
        action_type: 'TASK',
        title: '',
        payee_or_owner: '',
        amount: '',
        due_date: '',
        reference: '',
      });
      setNewActionOpen(false);
      showToast('Action added.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const refreshAnalysis = async () => {
    if (!detail) return;
    if (dirty) {
      showToast('Save or discard metadata changes before refreshing analysis.', 'error');
      return;
    }
    setBusyKey('refresh');
    try {
      const response = await endpoints.intake.refresh(
        detail.document.id,
        detail.document.modified,
      ) as { review: IntakeDetail };
      const nextMetadata = initialMetadata(response.review);
      setDetail(response.review);
      setMetadata(nextMetadata);
      setBaseline(nextMetadata);
      showToast('Suggestions and action analysis refreshed.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const pendingActions = detail?.actions.filter(
    (action) => !['completed', 'not_an_action', 'dismissed'].includes(action.status),
  ) || [];

  return (
    <div className="intake-review-page">
      <aside className="intake-queue" aria-label="Inbox documents">
        <div className="intake-queue-header">
          <div>
            <h1>Intake Review</h1>
            <p>{queue.length} document{queue.length === 1 ? '' : 's'} awaiting review</p>
          </div>
          <Button size="sm" disabled={loadingQueue} onClick={() => void loadQueue()}>Reload</Button>
        </div>
        {loadingQueue ? <SkeletonLoader variant="default" /> : queueError ? (
          <ErrorState message={`Inbox unavailable: ${queueError}`} onRetry={() => void loadQueue()} />
        ) : queue.length === 0 ? (
          <EmptyState title="Inbox is clear" desc="No documents match the configured intake tags." />
        ) : (
          <div
            className="intake-queue-list"
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                moveQueueFocus(1);
              } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                moveQueueFocus(-1);
              } else if (event.key === 'Enter' && focusedId != null) {
                event.preventDefault();
                selectDocument(focusedId);
              }
            }}
          >
            {queue.map((item) => (
              <button
                key={item.id}
                ref={(node) => {
                  if (node) queueButtonRefs.current.set(item.id, node);
                  else queueButtonRefs.current.delete(item.id);
                }}
                type="button"
                className={`intake-queue-item${item.id === selectedId ? ' active' : ''}`}
                aria-current={item.id === selectedId ? 'true' : undefined}
                onFocus={() => setFocusedId(item.id)}
                onClick={() => selectDocument(item.id)}
              >
                <strong>{item.title}</strong>
                <span>{item.document_summary?.correspondent || 'No correspondent'}</span>
                <span>{formatDate(item.created)} · {item.document_summary?.document_type || 'No type'}</span>
              </button>
            ))}
          </div>
        )}
      </aside>

      <main className="intake-workspace">
        {loadingDetail ? <SkeletonLoader variant="detail-panel" /> : detailError ? (
          <ErrorState message={`Document unavailable: ${detailError}`} onRetry={selectedId ? () => void loadDetail(selectedId) : undefined} />
        ) : !detail || !metadata ? (
          <EmptyState title="Select a document" desc="Choose an Inbox document to review its metadata, actions, and OCR." />
        ) : (
          <>
            <header className="intake-document-header">
              <div className="intake-document-title">
                <span>Document #{detail.document.id}</span>
                <h2>{metadata.title || 'Untitled document'}</h2>
              </div>
              <dl className="intake-live-metadata">
                <div><dt>Date</dt><dd>{formatDate(metadata.created)}</dd></div>
                <div><dt>Correspondent</dt><dd>{optionName(detail.taxonomies.correspondents, metadata.correspondent)}</dd></div>
                <div><dt>Type</dt><dd>{optionName(detail.taxonomies.document_types, metadata.document_type)}</dd></div>
              </dl>
              <Button
                disabled={busyKey === 'refresh'}
                onClick={() => void refreshAnalysis()}
              >
                {busyKey === 'refresh' ? 'Refreshing…' : 'Refresh analysis'}
              </Button>
            </header>

            <div className="intake-workspace-grid">
              <section className="intake-preview" aria-label="Document preview">
                <iframe title={`Preview of ${detail.document.title}`} src={detail.preview_url} />
              </section>

              <section className="intake-review-panel">
                <div className="intake-tabs" role="tablist" aria-label="Document review sections">
                  <button role="tab" aria-selected={tab === 'metadata'} className={tab === 'metadata' ? 'active' : ''} onClick={() => chooseTab('metadata')}>
                    Metadata
                  </button>
                  <button role="tab" aria-selected={tab === 'actions'} className={tab === 'actions' ? 'active' : ''} onClick={() => chooseTab('actions')}>
                    Actions <span>{pendingActions.length}</span>
                  </button>
                  <button
                    role="tab"
                    aria-selected={tab === 'ocr'}
                    aria-label={detail.ocr.poor_quality ? 'OCR, needs review' : 'OCR'}
                    className={tab === 'ocr' ? 'active' : ''}
                    onClick={() => chooseTab('ocr')}
                  >
                    OCR
                    {detail.ocr.poor_quality ? <span className="intake-tab-alert" aria-label="Poor OCR quality">Needs review</span> : null}
                  </button>
                </div>

                <div className="intake-tab-content">
                  {tab === 'metadata' ? (
                    <div className="intake-metadata-panel">
                      {detail.suggestions.length ? (
                        <section className="intake-suggestions" aria-label="Paperless suggestions">
                          <h3>Suggestions</h3>
                          <div>
                            {detail.suggestions.map((suggestion) => (
                              <button
                                type="button"
                                key={`${suggestion.source}-${suggestion.field}-${suggestion.id ?? suggestion.name}`}
                                disabled={busyKey.startsWith('suggestion-')}
                                onClick={() => void applySuggestion(suggestion)}
                              >
                                <span>{suggestion.field.replace('_', ' ')}</span>
                                <strong>{suggestion.name}</strong>
                                <small>{suggestion.source === 'paperless_ai' ? 'Paperless AI' : 'Paperless ML'}</small>
                              </button>
                            ))}
                          </div>
                        </section>
                      ) : (
                        <div className="intake-inline-state">
                          No unapplied suggestions.
                          {Object.entries(detail.suggestion_sources).some(([, state]) => !state.available)
                            ? ' One or more Paperless suggestion services are unavailable.'
                            : ''}
                        </div>
                      )}

                      <div className="intake-form-grid">
                        <label className="intake-field intake-field-wide">
                          <span>Title</span>
                          <input value={metadata.title} disabled={!detail.write_enabled} onChange={(event) => setMetadata({ ...metadata, title: event.target.value })} />
                        </label>
                        <label className="intake-field">
                          <span>Document date</span>
                          <input type="date" value={metadata.created} disabled={!detail.write_enabled} onChange={(event) => setMetadata({ ...metadata, created: event.target.value })} />
                        </label>
                        <TaxonomySelect
                          label="Correspondent"
                          value={metadata.correspondent}
                          values={detail.taxonomies.correspondents}
                          disabled={!detail.write_enabled}
                          onChange={(value) => setMetadata({ ...metadata, correspondent: value })}
                          onCreate={(name) => createTaxonomy('correspondent', name)}
                        />
                        <TaxonomySelect
                          label="Document type"
                          value={metadata.document_type}
                          values={detail.taxonomies.document_types}
                          disabled={!detail.write_enabled}
                          onChange={(value) => setMetadata({ ...metadata, document_type: value })}
                          onCreate={(name) => createTaxonomy('document_type', name)}
                        />
                        <TaxonomySelect
                          label="Storage path"
                          value={metadata.storage_path}
                          values={detail.taxonomies.storage_paths}
                          disabled={!detail.write_enabled}
                          onChange={(value) => setMetadata({ ...metadata, storage_path: value })}
                          onCreate={(name) => createTaxonomy('storage_path', name)}
                        />
                      </div>

                      <section className="intake-tags">
                        <h3>Tags</h3>
                        <div className="intake-tag-list">
                          {metadata.tags.map((tagId) => (
                            <span key={tagId}>
                              {optionName(detail.taxonomies.tags, tagId)}
                              <button
                                type="button"
                                aria-label={`Remove ${optionName(detail.taxonomies.tags, tagId)}`}
                                disabled={!detail.write_enabled}
                                onClick={() => setMetadata({ ...metadata, tags: metadata.tags.filter((value) => value !== tagId) })}
                              >
                                Remove
                              </button>
                            </span>
                          ))}
                        </div>
                        <select
                          aria-label="Add tag"
                          value=""
                          disabled={!detail.write_enabled}
                          onChange={(event) => {
                            if (event.target.value) setMetadata({ ...metadata, tags: [...metadata.tags, event.target.value] });
                          }}
                        >
                          <option value="">Add an existing tag…</option>
                          {detail.taxonomies.tags.filter((tagValue) => !metadata.tags.includes(String(tagValue.id))).map((tagValue) => (
                            <option key={tagValue.id} value={tagValue.id}>{tagValue.name}</option>
                          ))}
                        </select>
                        <div className="intake-create-tag">
                          <TaxonomySelect
                            label="Tag"
                            value=""
                            values={[]}
                            disabled={!detail.write_enabled}
                            allowClear
                            onChange={() => undefined}
                            onCreate={(name) => createTaxonomy('tag', name)}
                          />
                        </div>
                      </section>

                      <section className="intake-custom-fields">
                        <h3>OWL and custom fields</h3>
                        {detail.document.custom_fields?.length ? (
                          <dl>
                            {detail.document.custom_fields.map((field) => (
                              <div key={field.field}>
                                <dt>{detail.taxonomies.custom_fields.find((definition) => definition.id === field.field)?.name || `Field ${field.field}`}</dt>
                                <dd>{String(field.value ?? 'Not set')}</dd>
                              </div>
                            ))}
                          </dl>
                        ) : <p>No custom fields are set on this document.</p>}
                      </section>
                    </div>
                  ) : null}

                  {tab === 'actions' ? (
                    <div className="intake-actions-panel">
                      <div className="intake-actions-summary">
                        <div>
                          <h3>Document actions</h3>
                          <p>{pendingActions.length} pending · {detail.actions.length - pendingActions.length} resolved</p>
                        </div>
                        <Button size="sm" onClick={() => setNewActionOpen((open) => !open)}>
                          {newActionOpen ? 'Cancel new action' : 'New action'}
                        </Button>
                      </div>
                      {newActionOpen ? (
                        <div className="intake-new-action">
                          <div className="intake-action-grid">
                            <label><span>Type</span><select value={newAction.action_type} onChange={(event) => setNewAction({ ...newAction, action_type: event.target.value })}>{ACTION_TYPES.map((type) => <option key={type}>{type}</option>)}</select></label>
                            <label className="intake-action-title"><span>Title</span><input value={newAction.title} onChange={(event) => setNewAction({ ...newAction, title: event.target.value })} /></label>
                            <label><span>Payee or owner</span><input value={newAction.payee_or_owner} onChange={(event) => setNewAction({ ...newAction, payee_or_owner: event.target.value })} /></label>
                            <label><span>Amount</span><input type="number" min="0" step="0.01" value={newAction.amount} onChange={(event) => setNewAction({ ...newAction, amount: event.target.value })} /></label>
                            <label><span>Due date</span><input type="date" value={newAction.due_date} onChange={(event) => setNewAction({ ...newAction, due_date: event.target.value })} /></label>
                            <label><span>Reference</span><input value={newAction.reference} onChange={(event) => setNewAction({ ...newAction, reference: event.target.value })} /></label>
                          </div>
                          <Button variant="primary" disabled={busyKey === 'new-action' || !newAction.title.trim()} onClick={() => void addAction()}>
                            {busyKey === 'new-action' ? 'Adding…' : 'Add action'}
                          </Button>
                        </div>
                      ) : null}
                      {detail.actions.length ? detail.actions.map((action) => (
                        <ActionEditor
                          key={action.id}
                          action={action}
                          busy={busyKey === `action-${action.id}`}
                          onSave={updateAction}
                          onStatus={updateActionStatus}
                          onDelete={deleteAction}
                        />
                      )) : <EmptyState title="No action analysis yet" desc="Refresh analysis or add an action manually." />}
                    </div>
                  ) : null}

                  {tab === 'ocr' ? (
                    <div className="intake-ocr-panel">
                      <div className={`intake-ocr-score ${detail.ocr.quality_band}`}>
                        <div>
                          <strong>{detail.ocr.quality_score ?? '—'}</strong>
                          <span>OCR quality</span>
                        </div>
                        <p>
                          Green 90–100 · Amber 75–89 · Red below 75.
                          {detail.ocr.poor_quality ? ' This document needs OCR review.' : ''}
                        </p>
                      </div>
                      {detail.ocr.poor_quality ? (
                        <div className="intake-ocr-alert" role="alert">
                          Poor OCR can make metadata and action analysis stale or incomplete.
                        </div>
                      ) : null}
                      <div className="intake-ocr-actions">
                        <Button onClick={() => { window.location.hash = `#/ocr-quality/documents/${detail.document.id}`; }}>
                          Review or re-run OCR
                        </Button>
                        <span>After accepting new OCR, return here and refresh analysis.</span>
                      </div>
                      <label className="intake-ocr-text">
                        <span>Searchable text</span>
                        <textarea readOnly value={detail.ocr.text || 'No searchable text is available.'} />
                      </label>
                    </div>
                  ) : null}
                </div>
              </section>
            </div>

            <footer className="intake-footer">
              <div>
                <span className={dirty ? 'intake-dirty' : ''}>{dirty ? 'Unsaved metadata changes' : 'All metadata changes saved'}</span>
                {!detail.write_enabled ? <span className="intake-write-warning">Paperless writes are disabled.</span> : null}
              </div>
              <div className="intake-footer-actions">
                <Button disabled={!detail.write_enabled || Boolean(busyKey)} onClick={() => void save('save')}>Save changes</Button>
                <Button disabled={!detail.write_enabled || Boolean(busyKey)} onClick={() => void save('defer')}>Defer & next</Button>
                {pendingActions.length ? (
                  <>
                    <Button disabled={!detail.write_enabled || Boolean(busyKey)} onClick={() => void save('file_only')}>File only</Button>
                    <Button variant="primary" disabled={!detail.write_enabled || Boolean(busyKey)} onClick={() => void save('confirm_and_file')}>
                      Confirm action{pendingActions.length === 1 ? '' : 's'} & file
                    </Button>
                  </>
                ) : (
                  <Button variant="primary" disabled={!detail.write_enabled || Boolean(busyKey)} onClick={() => void save('file_only')}>Save & file</Button>
                )}
              </div>
            </footer>
          </>
        )}
      </main>
      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </div>
  );
}
