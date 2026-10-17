import {
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  ExternalLink,
  FileCheck2,
  FileClock,
  FilePlus2,
  Maximize2,
  Minimize2,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { Button, EmptyState, ErrorState, SkeletonLoader, Toast } from '../components/ui';
import DocumentRelationshipsPanel from '../components/DocumentRelationshipsPanel';
import { endpoints } from '../lib/api';
import {
  ACCOUNT_IDENTIFIER_CLASSES,
  type AccountIdentifierClass,
} from '../lib/accountIdentifier';
import { getToastDuration } from '../lib/toast';
import '../styles/intake-review.css';

type ReviewTab = 'metadata' | 'actions' | 'related' | 'ocr';
type Disposition = 'save' | 'defer' | 'file_only' | 'confirm_and_file';
type TaxonomyKind = 'tag' | 'correspondent' | 'document_type' | 'storage_path';
type QueueFilter = 'all' | 'actions' | 'missing_type';

interface TaxonomyValue {
  id: number;
  name: string;
  colour?: string | null;
  color?: string | null;
  data_type?: string | null;
}

interface IntakeListItem {
  id: number;
  title: string;
  created?: string | null;
  modified?: string | null;
  correspondent_name?: string | null;
  document_type_name?: string | null;
  thumbnail_url: string;
  pending_action_count: number;
}

interface IntakeQueueResponse {
  documents: IntakeListItem[];
  total: number;
  next_cursor: string | null;
}

const QUEUE_PAGE_SIZE = 100;

interface IntakeAction {
  id: number;
  document_id: number;
  action_type: string;
  title: string;
  summary?: string | null;
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

interface ReviewField {
  field: number;
  field_key?: string | null;
  name: string;
  value: unknown;
  data_type?: string | null;
  editable: boolean;
  audit: boolean;
}

interface IntakeCore {
  document: {
    id: number;
    title: string;
    created?: string | null;
    modified: string;
    correspondent?: number | null;
    document_type?: number | null;
    storage_path?: number | null;
    tags?: number[];
    review_fields?: ReviewField[];
  };
  taxonomies: {
    tags: TaxonomyValue[];
    correspondents: TaxonomyValue[];
    document_types: TaxonomyValue[];
    storage_paths: TaxonomyValue[];
    custom_fields: TaxonomyValue[];
  };
  preview_url: string;
  paperless_url?: string | null;
  write_enabled: boolean;
}

interface OcrState {
  quality_score?: number | null;
  quality_band: 'green' | 'amber' | 'red' | 'unknown';
  poor_quality: boolean;
  text: string;
}

interface MetadataDraft {
  title: string;
  created: string;
  correspondent: string;
  document_type: string;
  storage_path: string;
  tags: string[];
  customFields: Record<string, string>;
}

interface ActionDraft {
  action_type: string;
  title: string;
  details: string;
  payee_or_owner: string;
  amount: string;
  due_date: string;
  reference: string;
}

const ACTION_TYPES = ['PAY', 'RESPOND', 'REVIEW', 'SCHEDULE', 'SHARE', 'SIGN', 'TASK'] as const;
type ActionType = typeof ACTION_TYPES[number];

interface ActionFieldSchema {
  ownerLabel?: string;
  dueDateLabel: string;
  referenceLabel?: string;
  amount: boolean;
}

const ACTION_FIELD_SCHEMAS: Record<ActionType, ActionFieldSchema> = {
  PAY: {
    ownerLabel: 'Payee',
    dueDateLabel: 'Pay by',
    referenceLabel: 'Invoice or reference',
    amount: true,
  },
  RESPOND: {
    ownerLabel: 'Recipient',
    dueDateLabel: 'Respond by',
    referenceLabel: 'Case or reference',
    amount: false,
  },
  REVIEW: {
    dueDateLabel: 'Review by',
    amount: false,
  },
  SCHEDULE: {
    ownerLabel: 'With or provider',
    dueDateLabel: 'Event date',
    referenceLabel: 'Confirmation or reference',
    amount: false,
  },
  SHARE: {
    ownerLabel: 'Recipient',
    dueDateLabel: 'Share by',
    amount: false,
  },
  SIGN: {
    ownerLabel: 'Counterparty or recipient',
    dueDateLabel: 'Sign by',
    referenceLabel: 'Agreement or reference',
    amount: false,
  },
  TASK: {
    ownerLabel: 'Owner',
    dueDateLabel: 'Due date',
    referenceLabel: 'Reference',
    amount: false,
  },
};

const EMPTY_ACTION: ActionDraft = {
  action_type: 'TASK',
  title: '',
  details: '',
  payee_or_owner: '',
  amount: '',
  due_date: '',
  reference: '',
};

function initialMetadata(core: IntakeCore): MetadataDraft {
  return {
    title: core.document.title || '',
    created: core.document.created?.slice(0, 10) || '',
    correspondent: core.document.correspondent?.toString() || '',
    document_type: core.document.document_type?.toString() || '',
    storage_path: core.document.storage_path?.toString() || '',
    tags: (core.document.tags || []).map(String),
    customFields: Object.fromEntries(
      (core.document.review_fields || []).map((field) => [
        String(field.field),
        field.value == null ? '' : String(field.value),
      ]),
    ),
  };
}

function actionDraft(action: IntakeAction): ActionDraft {
  return {
    action_type: action.action_type || 'TASK',
    title: action.title || '',
    details: action.summary || '',
    payee_or_owner: action.payee_or_owner || '',
    amount: action.amount == null ? '' : String(action.amount),
    due_date: action.due_date?.slice(0, 10) || '',
    reference: action.reference || '',
  };
}

function actionType(value: string): ActionType {
  return ACTION_TYPES.includes(value as ActionType) ? value as ActionType : 'TASK';
}

function actionFieldSchema(value: string): ActionFieldSchema {
  return ACTION_FIELD_SCHEMAS[actionType(value)];
}

function actionDraftValid(draft: ActionDraft): boolean {
  const amount = Number(draft.amount);
  return Boolean(
    draft.title.trim()
    && (
      actionType(draft.action_type) !== 'PAY'
      || (draft.amount.trim() && Number.isFinite(amount) && amount >= 0)
    ),
  );
}

function actionMutation(draft: ActionDraft) {
  const schema = actionFieldSchema(draft.action_type);
  return {
    action_type: actionType(draft.action_type),
    title: draft.title.trim(),
    summary: draft.details.trim() || null,
    payee_or_owner: schema.ownerLabel ? draft.payee_or_owner.trim() || null : null,
    amount: schema.amount && draft.amount ? Number(draft.amount) : null,
    due_date: draft.due_date || null,
    reference: schema.referenceLabel ? draft.reference.trim() || null : null,
  };
}

function optionName(values: TaxonomyValue[], id: string): string {
  return values.find((value) => String(value.id) === id)?.name || 'Not set';
}

function formatDate(value?: string | null): string {
  if (!value) return 'Date not set';
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The operation failed.';
}

function tagHierarchy(name: string): { parents: string[]; leaf: string } {
  const parts = name.split(/\s*(?:::|\/|>)\s*/).filter(Boolean);
  return { parents: parts.slice(0, -1), leaf: parts.at(-1) || name };
}

function normalizeTagColour(tag?: TaxonomyValue): string | null {
  const raw = tag?.colour || tag?.color;
  if (!raw) return null;
  const match = raw.trim().match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return null;
  const hex = match[1].length === 3
    ? match[1].split('').map((value) => value + value).join('')
    : match[1];
  return `#${hex}`;
}

function contrastText(colour: string | null): string {
  if (!colour) return '#243147';
  const hex = colour.slice(1);
  const [r, g, b] = [0, 2, 4].map((index) => Number.parseInt(hex.slice(index, index + 2), 16));
  return (r * 299 + g * 587 + b * 114) / 1000 > 150 ? '#172033' : '#ffffff';
}

function matchesQueueFilter(item: IntakeListItem, query: string, filter: QueueFilter): boolean {
  if (filter === 'actions' && item.pending_action_count === 0) return false;
  if (filter === 'missing_type' && item.document_type_name) return false;
  const needle = query.trim().toLocaleLowerCase();
  return !needle || [item.title, item.correspondent_name, item.document_type_name]
    .some((value) => value?.toLocaleLowerCase().includes(needle));
}

function CurrencyInput({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const [focused, setFocused] = useState(false);
  const numericValue = Number(value.replaceAll(',', '').replaceAll('$', ''));
  const displayValue = !focused && value && Number.isFinite(numericValue)
    ? numericValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : value;
  return (
    <div className="intake-currency-input">
      <span aria-hidden="true">$</span>
      <input
        aria-label={label}
        inputMode="decimal"
        value={displayValue}
        disabled={disabled}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onChange={(event) => onChange(event.target.value.replace(/[^0-9.,-]/g, '').replaceAll(',', ''))}
      />
    </div>
  );
}

function ActionFields({
  draft,
  disabled = false,
  onChange,
}: {
  draft: ActionDraft;
  disabled?: boolean;
  onChange: (draft: ActionDraft) => void;
}) {
  const schema = actionFieldSchema(draft.action_type);
  return (
    <div className="intake-action-grid">
      <label>
        <span>Type</span>
        <select
          value={draft.action_type}
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, action_type: event.target.value })}
        >
          {ACTION_TYPES.map((type) => <option key={type}>{type}</option>)}
        </select>
      </label>
      <label className="intake-action-title">
        <span>Title</span>
        <input
          value={draft.title}
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, title: event.target.value })}
        />
      </label>
      {schema.ownerLabel ? (
        <label>
          <span>{schema.ownerLabel}</span>
          <input
            value={draft.payee_or_owner}
            disabled={disabled}
            onChange={(event) => onChange({ ...draft, payee_or_owner: event.target.value })}
          />
        </label>
      ) : null}
      {schema.amount ? (
        <label>
          <span>Amount</span>
          <input
            type="number"
            min="0"
            step="0.01"
            required
            value={draft.amount}
            disabled={disabled}
            onChange={(event) => onChange({ ...draft, amount: event.target.value })}
          />
        </label>
      ) : null}
      <label>
        <span>{schema.dueDateLabel}</span>
        <input
          type="date"
          value={draft.due_date}
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, due_date: event.target.value })}
        />
      </label>
      {schema.referenceLabel ? (
        <label>
          <span>{schema.referenceLabel}</span>
          <input
            value={draft.reference}
            disabled={disabled}
            onChange={(event) => onChange({ ...draft, reference: event.target.value })}
          />
        </label>
      ) : null}
      <label className="intake-action-details">
        <span>Details</span>
        <textarea
          value={draft.details}
          disabled={disabled}
          rows={3}
          maxLength={10_000}
          placeholder="What needs to be done, special instructions, or useful context"
          onChange={(event) => onChange({ ...draft, details: event.target.value })}
        />
      </label>
    </div>
  );
}

function SuggestionLinks({
  field,
  suggestions,
  busy,
  onApply,
}: {
  field: Suggestion['field'];
  suggestions: Suggestion[];
  busy: boolean;
  onApply: (suggestion: Suggestion) => void;
}) {
  const matches = suggestions.filter((suggestion) => suggestion.field === field);
  if (!matches.length) return null;
  return (
    <div className="intake-field-suggestions" aria-label={`Suggestions for ${field.replace('_', ' ')}`}>
      <span>Suggested:</span>
      {matches.map((suggestion) => (
        <button
          type="button"
          key={`${suggestion.source}-${suggestion.id ?? suggestion.name}`}
          disabled={busy}
          onClick={() => onApply(suggestion)}
          title={suggestion.source === 'paperless_ai' ? 'Paperless AI suggestion' : 'Paperless suggestion'}
        >
          {suggestion.name}{suggestion.create ? ' (new)' : ''}
        </button>
      ))}
    </div>
  );
}

function CreatableCombobox({
  label,
  value,
  values,
  disabled,
  onChange,
  onCreate,
}: {
  label: string;
  value: string;
  values: TaxonomyValue[];
  disabled: boolean;
  onChange: (value: string) => void;
  onCreate: (name: string) => Promise<void>;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const selected = values.find((item) => String(item.id) === value);
  const filtered = values
    .filter((item) => item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
    .slice(0, 30);
  const exact = values.some((item) => item.name.trim().toLocaleLowerCase() === query.trim().toLocaleLowerCase());
  return (
    <div className="intake-field intake-combobox">
      <label>
        <span>{label}</span>
        <div className="intake-combobox-input">
          <Search aria-hidden="true" size={16} />
          <input
            role="combobox"
            aria-label={label}
            aria-expanded={open}
            aria-controls={`${label.replaceAll(' ', '-').toLowerCase()}-options`}
            value={open ? query : selected?.name || ''}
            placeholder={`Search ${label.toLowerCase()}…`}
            disabled={disabled}
            onFocus={() => {
              setQuery('');
              setOpen(true);
            }}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          />
          {value ? (
            <button
              type="button"
              aria-label={`Clear ${label.toLowerCase()}`}
              disabled={disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onChange('')}
            >
              <X size={15} />
            </button>
          ) : null}
        </div>
      </label>
      {open ? (
        <div className="intake-combobox-menu" id={`${label.replaceAll(' ', '-').toLowerCase()}-options`} role="listbox">
          {filtered.map((item) => (
            <button
              type="button"
              role="option"
              aria-selected={String(item.id) === value}
              key={item.id}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange(String(item.id));
                setOpen(false);
              }}
            >
              <span>{item.name}</span>
              {String(item.id) === value ? <Check size={16} /> : null}
            </button>
          ))}
          {query.trim() && !exact ? (
            <button
              type="button"
              className="intake-create-option"
              disabled={creating}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                setCreating(true);
                void onCreate(query.trim()).finally(() => {
                  setCreating(false);
                  setOpen(false);
                });
              }}
            >
              <Plus size={16} />
              {creating ? 'Creating…' : `Create “${query.trim()}”`}
            </button>
          ) : null}
          {!filtered.length && (!query.trim() || exact) ? <p>No matching values</p> : null}
        </div>
      ) : null}
    </div>
  );
}

function TagPicker({
  selectedIds,
  values,
  disabled,
  onChange,
  onCreate,
}: {
  selectedIds: string[];
  values: TaxonomyValue[];
  disabled: boolean;
  onChange: (values: string[]) => void;
  onCreate: (name: string) => Promise<void>;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const available = values
    .filter((tag) => !selectedIds.includes(String(tag.id)))
    .filter((tag) => tag.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
    .slice(0, 40);
  const exact = values.some((tag) => tag.name.trim().toLocaleLowerCase() === query.trim().toLocaleLowerCase());
  return (
    <section className="intake-tags">
      <div className="intake-field-label">Tags</div>
      <div className="intake-combobox intake-tag-combobox">
        <div className="intake-tag-control">
          {selectedIds.map((tagId) => {
            const tag = values.find((item) => String(item.id) === tagId);
            if (!tag) return null;
            const colour = normalizeTagColour(tag);
            return (
              <span
                className="intake-tag-pill"
                key={tagId}
                style={{
                  backgroundColor: colour || undefined,
                  borderColor: colour || undefined,
                  color: contrastText(colour),
                }}
              >
                {tag.name}
                <button
                  type="button"
                  aria-label={`Remove ${tag.name}`}
                  disabled={disabled}
                  onClick={() => onChange(selectedIds.filter((value) => value !== tagId))}
                >
                  <X size={13} />
                </button>
              </span>
            );
          })}
          <Search aria-hidden="true" size={16} />
          <input
            role="combobox"
            aria-label="Add tag"
            aria-expanded={open}
            value={query}
            placeholder="Find or create a tag…"
            disabled={disabled}
            onFocus={() => setOpen(true)}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          />
        </div>
        {open ? (
          <div className="intake-combobox-menu" role="listbox">
            {available.map((tag) => {
              const hierarchy = tagHierarchy(tag.name);
              const colour = normalizeTagColour(tag);
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected="false"
                  key={tag.id}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    onChange([...selectedIds, String(tag.id)]);
                    setQuery('');
                  }}
                >
                  <i style={{ backgroundColor: colour || undefined }} />
                  <span className="intake-tag-option">
                    {hierarchy.parents.length ? <small>{hierarchy.parents.join(' / ')}</small> : null}
                    <strong>{hierarchy.leaf}</strong>
                  </span>
                  {hierarchy.parents.length ? <ChevronRight size={15} /> : null}
                </button>
              );
            })}
            {query.trim() && !exact ? (
              <button
                type="button"
                className="intake-create-option"
                disabled={creating}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  setCreating(true);
                  void onCreate(query.trim()).finally(() => {
                    setCreating(false);
                    setOpen(false);
                    setQuery('');
                  });
                }}
              >
                <Plus size={16} />
                {creating ? 'Creating…' : `Create “${query.trim()}”`}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
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
      <ActionFields draft={draft} disabled={busy} onChange={setDraft} />
      <div className="intake-action-controls">
        <Button size="sm" variant="primary" disabled={busy || !actionDraftValid(draft)} onClick={() => void onSave(action, draft)}><Save size={15} /> Save action</Button>
        {completed ? (
          <Button size="sm" disabled={busy} onClick={() => void onStatus(action, 'pending')}><RotateCcw size={15} /> Undo completion</Button>
        ) : (
          <Button size="sm" disabled={busy || inactive} onClick={() => void onStatus(action, 'completed')}><CheckCircle2 size={15} /> Mark done</Button>
        )}
        {action.origin === 'inferred' ? (
          <Button size="sm" disabled={busy || action.status === 'not_an_action'} onClick={() => void onStatus(action, 'not_an_action')}><X size={15} /> Not an action</Button>
        ) : (
          <Button size="sm" variant="danger" disabled={busy} onClick={() => void onDelete(action)}><Trash2 size={15} /> Delete</Button>
        )}
      </div>
      {action.history?.length ? (
        <details className="intake-action-history">
          <summary>Audit history ({action.history.length})</summary>
          <ol>
            {action.history.map((event) => (
              <li key={event.id}>
                <strong>{event.event_type.replaceAll('_', ' ')}</strong>
                {event.previous_status || event.new_status ? `: ${event.previous_status || 'none'} → ${event.new_status || 'none'}` : ''}
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
  const requestedDocument = searchParams.get('document') || documentId;
  const requestedTab = searchParams.get('tab');
  const [tab, setTab] = useState<ReviewTab>(
    requestedTab === 'actions' || requestedTab === 'related' || requestedTab === 'ocr'
      ? requestedTab
      : 'metadata',
  );
  const [queue, setQueue] = useState<IntakeListItem[]>([]);
  const [queueTotal, setQueueTotal] = useState(0);
  const [nextQueueCursor, setNextQueueCursor] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(requestedDocument ? Number(requestedDocument) : null);
  const [focusedId, setFocusedId] = useState<number | null>(selectedId);
  const [queueQuery, setQueueQuery] = useState('');
  const [queueFilter, setQueueFilter] = useState<QueueFilter>('all');
  const [pinnedFilteredId, setPinnedFilteredId] = useState<number | null>(null);
  const [core, setCore] = useState<IntakeCore | null>(null);
  const [metadata, setMetadata] = useState<MetadataDraft | null>(null);
  const [baseline, setBaseline] = useState<MetadataDraft | null>(null);
  const [accountIdentifierClass, setAccountIdentifierClass] =
    useState<AccountIdentifierClass>('service_account');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestionSources, setSuggestionSources] = useState<Record<string, { available: boolean; error?: string | null }>>({});
  const [suggestionsCached, setSuggestionsCached] = useState(false);
  const [actions, setActions] = useState<IntakeAction[]>([]);
  const [ocr, setOcr] = useState<OcrState | null>(null);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [loadingMoreQueue, setLoadingMoreQueue] = useState(false);
  const [loadingCore, setLoadingCore] = useState(false);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [loadingActions, setLoadingActions] = useState(false);
  const [loadingOcr, setLoadingOcr] = useState(false);
  const [queueError, setQueueError] = useState('');
  const [queueMoreError, setQueueMoreError] = useState('');
  const [coreError, setCoreError] = useState('');
  const [suggestionError, setSuggestionError] = useState('');
  const [actionError, setActionError] = useState('');
  const [ocrError, setOcrError] = useState('');
  const [busyKey, setBusyKey] = useState('');
  const [toast, setToast] = useState<{ message: string; tone?: 'success' | 'error' } | null>(null);
  const [newActionOpen, setNewActionOpen] = useState(false);
  const [newAction, setNewAction] = useState<ActionDraft>(EMPTY_ACTION);
  const [previewExpanded, setPreviewExpanded] = useState(false);
  const queueButtonRefs = useRef(new Map<number, HTMLButtonElement>());
  const queueRequestId = useRef(0);
  const requestId = useRef(0);

  const dirty = useMemo(() => Boolean(metadata && baseline && JSON.stringify(metadata) !== JSON.stringify(baseline)), [baseline, metadata]);
  const pendingActions = actions.filter((action) => !['completed', 'not_an_action', 'dismissed'].includes(action.status));
  const selectedQueueItem = queue.find((item) => item.id === selectedId);
  const filteredQueue = useMemo(() => {
    return queue.filter(
      (item) => item.id === pinnedFilteredId || matchesQueueFilter(item, '', queueFilter),
    );
  }, [pinnedFilteredId, queue, queueFilter]);

  const showToast = useCallback((message: string, tone: 'success' | 'error' = 'success') => {
    setToast({ message, tone });
    const duration = getToastDuration(tone);
    if (duration) window.setTimeout(() => setToast(null), duration);
  }, []);

  const loadQueue = useCallback(async (
    pageCount = 1,
    search = '',
    filter: QueueFilter = 'all',
  ): Promise<IntakeListItem[]> => {
    const currentRequest = ++queueRequestId.current;
    setLoadingQueue(true);
    setQueueError('');
    try {
      const documents: IntakeListItem[] = [];
      let cursor: string | null = null;
      let total = 0;
      let pagesLoaded = 0;
      do {
        const response = await endpoints.intake.documents({
          pageSize: QUEUE_PAGE_SIZE,
          cursor,
          search,
          filter,
        }) as IntakeQueueResponse;
        documents.push(...response.documents);
        total = response.total;
        cursor = response.next_cursor;
        pagesLoaded += 1;
      } while (cursor && pagesLoaded < pageCount);
      if (queueRequestId.current !== currentRequest) return documents;
      setQueue(documents);
      setQueueTotal(total);
      setNextQueueCursor(cursor);
      setSelectedId((current) => current ?? documents[0]?.id ?? null);
      setFocusedId((current) => current ?? documents[0]?.id ?? null);
      return documents;
    } catch (error) {
      if (queueRequestId.current === currentRequest) setQueueError(errorMessage(error));
      return [];
    } finally {
      if (queueRequestId.current === currentRequest) setLoadingQueue(false);
    }
  }, []);

  const loadMoreQueue = useCallback(async () => {
    if (!nextQueueCursor || loadingMoreQueue) return;
    setLoadingMoreQueue(true);
    setQueueMoreError('');
    try {
      const response = await endpoints.intake.documents({
        pageSize: QUEUE_PAGE_SIZE,
        cursor: nextQueueCursor,
        search: queueQuery,
        filter: queueFilter,
      }) as IntakeQueueResponse;
      setQueue((current) => {
        const existingIds = new Set(current.map((item) => item.id));
        return [...current, ...response.documents.filter((item) => !existingIds.has(item.id))];
      });
      setQueueTotal(response.total);
      setNextQueueCursor(response.next_cursor);
    } catch (error) {
      setQueueMoreError(errorMessage(error));
    } finally {
      setLoadingMoreQueue(false);
    }
  }, [loadingMoreQueue, nextQueueCursor, queueFilter, queueQuery]);

  const loadDocument = useCallback(async (id: number) => {
    const currentRequest = ++requestId.current;
    setCore(null);
    setMetadata(null);
    setBaseline(null);
    setAccountIdentifierClass('service_account');
    setSuggestions([]);
    setActions([]);
    setOcr(null);
    setCoreError('');
    setSuggestionError('');
    setActionError('');
    setOcrError('');
    setLoadingCore(true);
    setLoadingSuggestions(true);
    setLoadingActions(true);
    setLoadingOcr(true);

    void endpoints.intake.documentCore(id)
      .then((response) => {
        if (requestId.current !== currentRequest) return;
        const nextCore = response as IntakeCore;
        const nextMetadata = initialMetadata(nextCore);
        setCore(nextCore);
        setMetadata(nextMetadata);
        setBaseline(nextMetadata);
      })
      .catch((error) => {
        if (requestId.current === currentRequest) setCoreError(errorMessage(error));
      })
      .finally(() => {
        if (requestId.current === currentRequest) setLoadingCore(false);
      });
    void endpoints.intake.documentSuggestions(id)
      .then((response) => {
        if (requestId.current !== currentRequest) return;
        const result = response as {
          suggestions: Suggestion[];
          suggestion_sources: Record<string, { available: boolean; error?: string | null }>;
          cached: boolean;
        };
        setSuggestions(result.suggestions);
        setSuggestionSources(result.suggestion_sources);
        setSuggestionsCached(result.cached);
      })
      .catch((error) => {
        if (requestId.current === currentRequest) setSuggestionError(errorMessage(error));
      })
      .finally(() => {
        if (requestId.current === currentRequest) setLoadingSuggestions(false);
      });
    void endpoints.intake.documentActions(id)
      .then((response) => {
        if (requestId.current !== currentRequest) return;
        const result = response as { actions: IntakeAction[]; ocr_quality: Omit<OcrState, 'text'> };
        setActions(result.actions);
        setOcr((current) => ({ text: current?.text || '', ...result.ocr_quality }));
      })
      .catch((error) => {
        if (requestId.current === currentRequest) setActionError(errorMessage(error));
      })
      .finally(() => {
        if (requestId.current === currentRequest) setLoadingActions(false);
      });
    void endpoints.intake.documentOcr(id)
      .then((response) => {
        if (requestId.current === currentRequest) setOcr(response as OcrState);
      })
      .catch((error) => {
        if (requestId.current === currentRequest) setOcrError(errorMessage(error));
      })
      .finally(() => {
        if (requestId.current === currentRequest) setLoadingOcr(false);
      });
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(
      () => void loadQueue(1, queueQuery, queueFilter),
      queueQuery.trim() ? 250 : 0,
    );
    return () => window.clearTimeout(timeout);
  }, [loadQueue, queueFilter, queueQuery]);
  useEffect(() => {
    if (selectedId != null) void loadDocument(selectedId);
  }, [loadDocument, selectedId]);
  useEffect(() => {
    if (!previewExpanded) return undefined;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPreviewExpanded(false);
    };
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [previewExpanded]);

  const updateUrl = (document: number | null, nextTab = tab) => {
    const nextParams = new URLSearchParams(searchParams);
    if (document == null) nextParams.delete('document');
    else nextParams.set('document', String(document));
    if (nextTab === 'metadata') nextParams.delete('tab');
    else nextParams.set('tab', nextTab);
    setSearchParams(nextParams, { replace: true });
  };

  const chooseTab = (next: ReviewTab) => {
    setTab(next);
    updateUrl(selectedId, next);
  };

  const activateDocument = (id: number) => {
    setPinnedFilteredId(null);
    setSelectedId(id);
    setFocusedId(id);
    updateUrl(id);
  };

  const selectDocument = (id: number) => {
    if (dirty && !window.confirm('Discard unsaved metadata changes and open another document?')) return;
    activateDocument(id);
  };

  const moveQueueFocus = (direction: -1 | 1) => {
    if (!filteredQueue.length) return;
    const currentIndex = Math.max(0, filteredQueue.findIndex((item) => item.id === focusedId));
    const nextIndex = Math.min(filteredQueue.length - 1, Math.max(0, currentIndex + direction));
    const nextId = filteredQueue[nextIndex].id;
    setFocusedId(nextId);
    queueButtonRefs.current.get(nextId)?.focus();
  };

  const createTaxonomy = async (kind: TaxonomyKind, name: string) => {
    try {
      const response = await endpoints.intake.createTaxonomy(kind, name) as { value: TaxonomyValue };
      const key = kind === 'tag' ? 'tags' : `${kind}s` as keyof IntakeCore['taxonomies'];
      setCore((current) => current ? {
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

  const applySuggestion = async (suggestion: Suggestion) => {
    if (!metadata) return;
    if (suggestion.create && suggestion.id == null) {
      const kind = suggestion.field === 'tags' ? 'tag' : suggestion.field as TaxonomyKind;
      setBusyKey(`suggestion-${suggestion.field}-${suggestion.name}`);
      try {
        await createTaxonomy(kind, suggestion.name);
      } finally {
        setBusyKey('');
      }
      return;
    }
    setMetadata({
      ...metadata,
      [suggestion.field]: suggestion.field === 'tags'
        ? Array.from(new Set([...metadata.tags, String(suggestion.id)]))
        : suggestion.id != null ? String(suggestion.id) : suggestion.value || suggestion.name,
    });
  };

  const save = async (disposition: Disposition) => {
    if (!core || !metadata) return;
    setBusyKey(disposition);
    const currentIndex = queue.findIndex((item) => item.id === core.document.id);
    const nextDocument = queue[currentIndex + 1] || queue[currentIndex - 1];
    try {
      const response = await endpoints.intake.saveDocument(core.document.id, {
        expected_modified: core.document.modified,
        metadata: {
          title: metadata.title,
          created: metadata.created || null,
          correspondent: metadata.correspondent ? Number(metadata.correspondent) : null,
          document_type: metadata.document_type ? Number(metadata.document_type) : null,
          storage_path: metadata.storage_path ? Number(metadata.storage_path) : null,
          tags: metadata.tags.map(Number),
          ...((core.document.review_fields || []).some((field) => field.editable) ? {
            custom_fields: (core.document.review_fields || [])
              .filter((field) => field.editable)
              .map((field) => {
                const value = metadata.customFields[String(field.field)] || '';
                return {
                  field: field.field,
                  value: ['float', 'decimal', 'integer', 'monetary'].includes(field.data_type || '')
                    ? value ? Number(value) : null
                    : value || null,
                  ...(field.field_key === 'account_identifier'
                    ? { identifier_class: accountIdentifierClass }
                    : {}),
                };
              }),
          } : {}),
        },
        disposition,
        action_versions: disposition === 'confirm_and_file'
          ? Object.fromEntries(pendingActions.map((action) => [action.id, action.version]))
          : {},
      }) as { filed: boolean };
      showToast(disposition === 'defer' ? 'Changes saved; document kept in the Inbox.' : response.filed ? 'Document filed.' : 'Changes saved.');
      const refreshedQueue = await loadQueue(
        Math.max(1, Math.ceil(queue.length / QUEUE_PAGE_SIZE)),
        queueQuery,
        queueFilter,
      );
      const matchingQueue = refreshedQueue;
      if (response.filed || disposition === 'defer') {
        setPinnedFilteredId(null);
        const nextMatching = matchingQueue.find((item) => item.id !== core.document.id)
          || (nextDocument && refreshedQueue.find((item) => item.id === nextDocument.id));
        if (nextMatching) activateDocument(nextMatching.id);
        else if (response.filed) {
          setSelectedId(null);
          updateUrl(null);
          setCore(null);
        } else {
          await loadDocument(core.document.id);
        }
      } else {
        const refreshedCurrent = refreshedQueue.find((item) => item.id === core.document.id);
        const shouldPinCurrent = !refreshedCurrent
          || !matchesQueueFilter(refreshedCurrent, '', queueFilter);
        if (shouldPinCurrent && !refreshedCurrent && selectedQueueItem) {
          setQueue([selectedQueueItem, ...refreshedQueue]);
        }
        setPinnedFilteredId(shouldPinCurrent ? core.document.id : null);
        await loadDocument(core.document.id);
      }
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const updateAction = async (action: IntakeAction, draft: ActionDraft) => {
    setBusyKey(`action-${action.id}`);
    try {
      const updated = await endpoints.actionQueue.updateAction(String(action.id), {
        version: action.version,
        ...actionMutation(draft),
      }) as IntakeAction;
      setActions((current) => current.map((item) => item.id === updated.id ? updated : item));
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
      const updated = await endpoints.actionQueue.updateAction(String(action.id), { version: action.version, status }) as IntakeAction;
      setActions((current) => current.map((item) => item.id === updated.id ? updated : item));
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
      setActions((current) => current.filter((item) => item.id !== action.id));
      showToast('Action deleted.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const addAction = async () => {
    if (!core) return;
    setBusyKey('new-action');
    try {
      const created = await endpoints.intake.createAction(core.document.id, {
        ...actionMutation(newAction),
      }) as IntakeAction;
      setActions((current) => [...current, created]);
      setNewAction(EMPTY_ACTION);
      setNewActionOpen(false);
      showToast('Action added.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const refreshAnalysis = async () => {
    if (!core) return;
    if (dirty) {
      showToast('Save or discard metadata changes before refreshing analysis.', 'error');
      return;
    }
    setBusyKey('refresh');
    try {
      const response = await endpoints.intake.refresh(core.document.id, core.document.modified) as {
        actions: { actions: IntakeAction[]; ocr_quality: Omit<OcrState, 'text'> };
        suggestions: Suggestion[];
        suggestion_sources: Record<string, { available: boolean; error?: string | null }>;
      };
      setActions(response.actions.actions);
      setOcr((current) => ({ text: current?.text || '', ...response.actions.ocr_quality }));
      setSuggestions(response.suggestions);
      setSuggestionSources(response.suggestion_sources);
      setSuggestionsCached(false);
      showToast('Suggestions and action analysis refreshed.');
    } catch (error) {
      showToast(errorMessage(error), 'error');
    } finally {
      setBusyKey('');
    }
  };

  const previewUrl = selectedId
    ? `${core?.preview_url || `/api/statements/documents/${selectedId}/preview`}#page=1&zoom=page-fit`
    : '';
  const title = metadata?.title || core?.document.title || selectedQueueItem?.title || 'Loading document…';

  return (
    <div className="intake-review-page">
      <aside className="intake-queue" aria-label="Inbox documents">
        <div className="intake-queue-header">
          <div>
            <span className="intake-eyebrow">Paperless inbox</span>
            <h1>Inbox Review</h1>
            <p>{queueTotal} {queueQuery.trim() || queueFilter !== 'all' ? 'matching documents' : 'awaiting review'}</p>
          </div>
          <Button
            size="sm"
            aria-label="Reload queue"
            disabled={loadingQueue}
            onClick={() => void loadQueue(
              Math.max(1, Math.ceil(queue.length / QUEUE_PAGE_SIZE)),
              queueQuery,
              queueFilter,
            )}
          ><RefreshCw size={15} /></Button>
        </div>
        <div className="intake-queue-tools">
          <label><Search size={16} /><input aria-label="Search intake documents" placeholder="Search documents…" value={queueQuery} onChange={(event) => {
            setPinnedFilteredId(null);
            setQueueQuery(event.target.value);
          }} /></label>
          <div className="intake-filter-chips" aria-label="Filter documents">
            {([
              ['all', 'All'],
              ['actions', 'Action needed'],
              ['missing_type', 'Missing type'],
            ] as const).map(([value, label]) => (
              <button key={value} type="button" className={queueFilter === value ? 'active' : ''} onClick={() => {
                setPinnedFilteredId(null);
                setQueueFilter(value);
              }}>{label}</button>
            ))}
          </div>
        </div>
        {loadingQueue ? <SkeletonLoader variant="default" /> : queueError ? (
          <ErrorState message={`Inbox unavailable: ${queueError}`} onRetry={() => void loadQueue(1, queueQuery, queueFilter)} />
        ) : queue.length === 0 ? (
          <EmptyState title="Inbox is clear" desc="No documents match the configured intake tags." />
        ) : filteredQueue.length === 0 ? (
          <EmptyState title="No matching documents" desc="Try another search or filter." />
        ) : (
          <div
            className="intake-queue-list"
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') { event.preventDefault(); moveQueueFocus(1); }
              else if (event.key === 'ArrowUp') { event.preventDefault(); moveQueueFocus(-1); }
              else if (
                event.key === 'Enter'
                && focusedId != null
                && (event.target as HTMLElement).classList.contains('intake-queue-item')
              ) { event.preventDefault(); selectDocument(focusedId); }
            }}
          >
            {filteredQueue.map((item) => (
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
                <img src={item.thumbnail_url} alt="" loading="lazy" />
                <span className="intake-queue-copy">
                  <strong>{item.title}</strong>
                  <span>{item.correspondent_name || 'Correspondent not set'}</span>
                  <span>{formatDate(item.created)} · {item.document_type_name || 'Document type not set'}</span>
                  {item.pending_action_count ? <em><CircleAlert size={13} /> Action needed · {item.pending_action_count}</em> : null}
                </span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
            ))}
            {nextQueueCursor ? (
              <div className="intake-queue-more">
                {queueMoreError ? (
                  <span role="alert">Could not load more documents: {queueMoreError}</span>
                ) : null}
                <Button
                  size="sm"
                  disabled={loadingMoreQueue}
                  onClick={() => void loadMoreQueue()}
                >
                  {loadingMoreQueue ? <RefreshCw className="intake-spin" size={14} /> : null}
                  {loadingMoreQueue ? 'Loading…' : `Load more (${queue.length} of ${queueTotal} loaded)`}
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </aside>

      <main className="intake-workspace">
        {!selectedId ? (
          <EmptyState title="Select a document" desc="Choose an Inbox document to review its metadata, actions, and OCR." />
        ) : (
          <>
            <header className="intake-document-header">
              <div className="intake-document-title"><span>Document #{selectedId}</span><h2>{title}</h2></div>
              <dl className="intake-live-metadata">
                <div><dt>Date</dt><dd>{metadata ? formatDate(metadata.created) : 'Loading…'}</dd></div>
                <div><dt>Correspondent</dt><dd>{core && metadata ? optionName(core.taxonomies.correspondents, metadata.correspondent) : 'Loading…'}</dd></div>
                <div><dt>Type</dt><dd>{core && metadata ? optionName(core.taxonomies.document_types, metadata.document_type) : 'Loading…'}</dd></div>
              </dl>
              <div className="intake-header-actions">
                {core?.paperless_url ? (
                  <a className="btn ghost" href={core.paperless_url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink size={16} /> Open in Paperless
                  </a>
                ) : null}
                <Button disabled={busyKey === 'refresh' || !core} onClick={() => void refreshAnalysis()}><RefreshCw size={16} /> {busyKey === 'refresh' ? 'Refreshing…' : 'Refresh analysis'}</Button>
              </div>
            </header>

            <div className="intake-workspace-grid">
              <section className="intake-preview" aria-label="Document preview">
                <iframe title={`Preview of ${title}`} src={previewUrl} />
                <div className="intake-preview-tools">
                  <button type="button" onClick={() => setPreviewExpanded(true)}>
                    <Maximize2 size={16} /> Expand
                  </button>
                  <a href={previewUrl} target="_blank" rel="noopener noreferrer">
                    <ExternalLink size={16} /> New tab
                  </a>
                </div>
              </section>

              <section className="intake-review-panel">
                <div className="intake-tabs" role="tablist" aria-label="Document review sections">
                  <button role="tab" aria-selected={tab === 'metadata'} className={tab === 'metadata' ? 'active' : ''} onClick={() => chooseTab('metadata')}>Metadata</button>
                  <button role="tab" aria-selected={tab === 'actions'} className={`${tab === 'actions' ? 'active ' : ''}${pendingActions.length ? 'needs-attention' : ''}`} onClick={() => chooseTab('actions')}>
                    Actions <span>{loadingActions ? '…' : pendingActions.length}</span>
                  </button>
                  <button role="tab" aria-selected={tab === 'related'} className={tab === 'related' ? 'active' : ''} onClick={() => chooseTab('related')}>
                    Related
                  </button>
                  <button role="tab" aria-selected={tab === 'ocr'} aria-label={ocr?.poor_quality ? 'OCR, needs review' : 'OCR'} className={tab === 'ocr' ? 'active' : ''} onClick={() => chooseTab('ocr')}>
                    OCR {ocr?.poor_quality ? <span className="intake-tab-alert" aria-label="Poor OCR quality"><CircleAlert size={13} /> Review</span> : null}
                  </button>
                </div>

                <div className="intake-tab-content">
                  {coreError ? <ErrorState message={`Document metadata unavailable: ${coreError}`} onRetry={() => void loadDocument(selectedId)} /> : null}
                  {tab === 'metadata' && !coreError ? (
                    loadingCore || !core || !metadata ? <SkeletonLoader variant="detail-panel" /> : (
                      <div className="intake-metadata-panel">
                        {loadingSuggestions ? <div className="intake-inline-state"><RefreshCw className="intake-spin" size={15} /> Loading Paperless suggestions in the background…</div> : null}
                        {suggestionError ? <div className="intake-inline-error">Suggestions unavailable: {suggestionError}</div> : null}
                        {!loadingSuggestions && !suggestionError && !suggestions.length ? (
                          <div className="intake-inline-state">
                            No unapplied suggestions.
                            {Object.values(suggestionSources).some((state) => !state.available) ? ' One or more suggestion services are unavailable.' : ''}
                            {suggestionsCached ? ' Checked from the saved analysis.' : ''}
                          </div>
                        ) : null}

                        <div className="intake-form-grid">
                          <label className="intake-field intake-field-wide">
                            <span>Title</span>
                            <input value={metadata.title} disabled={!core.write_enabled} onChange={(event) => setMetadata({ ...metadata, title: event.target.value })} />
                            <SuggestionLinks field="title" suggestions={suggestions} busy={busyKey.startsWith('suggestion-')} onApply={(suggestion) => void applySuggestion(suggestion)} />
                          </label>
                          <label className="intake-field">
                            <span>Document date</span>
                            <input type="date" value={metadata.created} disabled={!core.write_enabled} onChange={(event) => setMetadata({ ...metadata, created: event.target.value })} />
                            <SuggestionLinks field="created" suggestions={suggestions} busy={busyKey.startsWith('suggestion-')} onApply={(suggestion) => void applySuggestion(suggestion)} />
                          </label>
                          <div>
                            <CreatableCombobox label="Correspondent" value={metadata.correspondent} values={core.taxonomies.correspondents} disabled={!core.write_enabled} onChange={(value) => setMetadata({ ...metadata, correspondent: value })} onCreate={(name) => createTaxonomy('correspondent', name)} />
                            <SuggestionLinks field="correspondent" suggestions={suggestions} busy={busyKey.startsWith('suggestion-')} onApply={(suggestion) => void applySuggestion(suggestion)} />
                          </div>
                          <div>
                            <CreatableCombobox label="Document type" value={metadata.document_type} values={core.taxonomies.document_types} disabled={!core.write_enabled} onChange={(value) => setMetadata({ ...metadata, document_type: value })} onCreate={(name) => createTaxonomy('document_type', name)} />
                            <SuggestionLinks field="document_type" suggestions={suggestions} busy={busyKey.startsWith('suggestion-')} onApply={(suggestion) => void applySuggestion(suggestion)} />
                          </div>
                          <div>
                            <CreatableCombobox label="Storage path" value={metadata.storage_path} values={core.taxonomies.storage_paths} disabled={!core.write_enabled} onChange={(value) => setMetadata({ ...metadata, storage_path: value })} onCreate={(name) => createTaxonomy('storage_path', name)} />
                            <SuggestionLinks field="storage_path" suggestions={suggestions} busy={busyKey.startsWith('suggestion-')} onApply={(suggestion) => void applySuggestion(suggestion)} />
                          </div>
                        </div>

                        <TagPicker selectedIds={metadata.tags} values={core.taxonomies.tags} disabled={!core.write_enabled} onChange={(tags) => setMetadata({ ...metadata, tags })} onCreate={(name) => createTaxonomy('tag', name)} />
                        <SuggestionLinks field="tags" suggestions={suggestions} busy={busyKey.startsWith('suggestion-')} onApply={(suggestion) => void applySuggestion(suggestion)} />

                        {(core.document.review_fields || []).length ? (
                          <div className="intake-form-grid intake-review-fields">
                            {(core.document.review_fields || []).map((field) => field.editable ? (
                              field.field_key === 'account_identifier' ? (
                                <div className="intake-field" key={field.field}>
                                  <label className="intake-field-label" htmlFor={`review-field-${field.field}`}>
                                    {field.name}
                                  </label>
                                  <input
                                    id={`review-field-${field.field}`}
                                    value={metadata.customFields[String(field.field)] || ''}
                                    disabled={!core.write_enabled}
                                    onChange={(event) => setMetadata({
                                      ...metadata,
                                      customFields: {
                                        ...metadata.customFields,
                                        [String(field.field)]: event.target.value,
                                      },
                                    })}
                                  />
                                  <label className="intake-field-label" htmlFor={`review-field-class-${field.field}`}>
                                    Identifier type
                                  </label>
                                  <select
                                    id={`review-field-class-${field.field}`}
                                    value={accountIdentifierClass}
                                    disabled={!core.write_enabled}
                                    onChange={(event) =>
                                      setAccountIdentifierClass(event.target.value as AccountIdentifierClass)
                                    }
                                  >
                                    {ACCOUNT_IDENTIFIER_CLASSES.map((option) => (
                                      <option key={option.value} value={option.value}>
                                        {option.label}
                                      </option>
                                    ))}
                                  </select>
                                  <small>The full value is stored exactly in Paperless.</small>
                                </div>
                              ) : (
                                <label className="intake-field" key={field.field}>
                                  <span>{field.name}</span>
                                  {field.name === 'Document Amount' ? (
                                  <CurrencyInput
                                    label={field.name}
                                    value={metadata.customFields[String(field.field)] || ''}
                                    disabled={!core.write_enabled}
                                    onChange={(value) => setMetadata({ ...metadata, customFields: { ...metadata.customFields, [String(field.field)]: value } })}
                                  />
                                ) : (
                                  <input
                                    type={field.data_type === 'date' ? 'date' : ['float', 'decimal', 'integer', 'monetary'].includes(field.data_type || '') ? 'number' : 'text'}
                                    step={['float', 'decimal', 'monetary'].includes(field.data_type || '') ? '0.01' : undefined}
                                    value={metadata.customFields[String(field.field)] || ''}
                                    disabled={!core.write_enabled}
                                    onChange={(event) => setMetadata({ ...metadata, customFields: { ...metadata.customFields, [String(field.field)]: event.target.value } })}
                                  />
                                  )}
                                </label>
                              )
                            ) : (
                              <div className="intake-readonly-field" key={field.field}>
                                <span>{field.name}{field.audit ? ' · audit' : ''}</span>
                                <strong>{String(field.value ?? 'Not set')}</strong>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    )
                  ) : null}

                  {tab === 'actions' ? (
                    <div className="intake-actions-panel">
                      <div className="intake-actions-summary">
                        <div><h3>Document actions</h3><p>{pendingActions.length} pending · {actions.length - pendingActions.length} resolved</p></div>
                        <Button size="sm" onClick={() => setNewActionOpen((open) => !open)}>{newActionOpen ? <X size={15} /> : <Plus size={15} />} {newActionOpen ? 'Cancel' : 'New action'}</Button>
                      </div>
                      {actionError ? <ErrorState message={`Actions unavailable: ${actionError}`} onRetry={() => void loadDocument(selectedId)} /> : null}
                      {loadingActions ? <div className="intake-inline-state"><RefreshCw className="intake-spin" size={15} /> Loading action analysis…</div> : null}
                      {newActionOpen ? (
                        <div className="intake-new-action">
                          <ActionFields draft={newAction} onChange={setNewAction} />
                          <Button variant="primary" disabled={busyKey === 'new-action' || !actionDraftValid(newAction)} onClick={() => void addAction()}><FilePlus2 size={16} /> {busyKey === 'new-action' ? 'Adding…' : 'Add action'}</Button>
                        </div>
                      ) : null}
                      {!loadingActions && !actionError && actions.length ? actions.map((action) => (
                        <ActionEditor key={action.id} action={action} busy={busyKey === `action-${action.id}`} onSave={updateAction} onStatus={updateActionStatus} onDelete={deleteAction} />
                      )) : null}
                      {!loadingActions && !actionError && !actions.length ? <EmptyState title="No action analysis yet" desc="Refresh analysis or add an action manually." /> : null}
                    </div>
                  ) : null}

                  {tab === 'related' ? (
                    <DocumentRelationshipsPanel
                      document={{
                        id: selectedId,
                        title,
                        created: metadata?.created || core?.document.created,
                        document_type_name: core && metadata
                          ? optionName(core.taxonomies.document_types, metadata.document_type)
                          : selectedQueueItem?.document_type_name,
                      }}
                      onToast={showToast}
                    />
                  ) : null}

                  {tab === 'ocr' ? (
                    <div className="intake-ocr-panel">
                      {ocrError ? <ErrorState message={`OCR unavailable: ${ocrError}`} onRetry={() => void loadDocument(selectedId)} /> : null}
                      {loadingOcr && !ocr ? <div className="intake-inline-state"><RefreshCw className="intake-spin" size={15} /> Loading searchable text…</div> : null}
                      {ocr ? (
                        <>
                          <div className={`intake-ocr-score ${ocr.quality_band}`}>
                            <div><strong>{ocr.quality_score ?? '—'}</strong><span>OCR quality</span></div>
                            <p>Green 90–100 · Amber 75–89 · Red below 75.{ocr.poor_quality ? ' This document needs OCR review.' : ''}</p>
                          </div>
                          {ocr.poor_quality ? <div className="intake-ocr-alert" role="alert"><CircleAlert size={17} /> Poor OCR can make metadata and action analysis stale or incomplete.</div> : null}
                          <div className="intake-ocr-actions">
                            <Button onClick={() => { window.location.hash = `#/ocr-quality/documents/${selectedId}`; }}>Review or re-run OCR</Button>
                            <span>After accepting new OCR, return here and refresh analysis.</span>
                          </div>
                          <label className="intake-ocr-text"><span>Searchable text</span><textarea readOnly value={ocr.text || 'No searchable text is available.'} /></label>
                        </>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </section>
            </div>

            <footer className="intake-footer">
              <div className="intake-footer-left">
                <Button disabled={!core?.write_enabled || Boolean(busyKey)} onClick={() => void save('defer')}><FileClock size={16} /> Defer & next</Button>
                <span className={dirty ? 'intake-dirty' : ''}>{dirty ? 'Unsaved changes' : 'All changes saved'}</span>
                {core && !core.write_enabled ? <span className="intake-write-warning">Paperless writes are disabled.</span> : null}
              </div>
              <div className="intake-footer-actions">
                <Button disabled={!core?.write_enabled || Boolean(busyKey)} onClick={() => void save('save')}><Save size={16} /> Save changes</Button>
                {pendingActions.length ? (
                  <>
                    <Button disabled={!core?.write_enabled || Boolean(busyKey)} onClick={() => void save('file_only')}><FileCheck2 size={16} /> File only</Button>
                    <Button variant="primary" disabled={!core?.write_enabled || Boolean(busyKey)} onClick={() => void save('confirm_and_file')}><CheckCircle2 size={16} /> Confirm action{pendingActions.length === 1 ? '' : 's'} & file</Button>
                  </>
                ) : (
                  <Button variant="primary" disabled={!core?.write_enabled || Boolean(busyKey)} onClick={() => void save('file_only')}><FileCheck2 size={16} /> Save & file</Button>
                )}
              </div>
            </footer>
          </>
        )}
      </main>
      {previewExpanded && selectedId ? (
        <div className="intake-preview-overlay" role="dialog" aria-modal="true" aria-label={`Expanded preview of ${title}`}>
          <div className="intake-preview-overlay-header">
            <div><span>Document #{selectedId}</span><strong>{title}</strong></div>
            <div>
              {core?.paperless_url ? (
                <a href={core.paperless_url} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} /> Paperless</a>
              ) : null}
              <a href={previewUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} /> New tab</a>
              <button type="button" autoFocus onClick={() => setPreviewExpanded(false)}><Minimize2 size={16} /> Close</button>
            </div>
          </div>
          <iframe title={`Expanded preview of ${title}`} src={previewUrl} />
        </div>
      ) : null}
      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </div>
  );
}
