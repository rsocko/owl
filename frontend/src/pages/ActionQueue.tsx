import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  FilterPills,
  PageHeader,
  SkeletonLoader,
  StatCard,
  StatGrid,
  Toast,
} from '../components/ui';
import { endpoints } from '../lib/api';
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
  status?: string | null;
  correspondent?: string | null;
  ai_reasoning?: string | null;
  preview_url?: string | null;
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);

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
    const timeout = window.setTimeout(() => setToast(null), 3000);
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

  useEffect(() => {
    if (filteredActions.length === 0) {
      setSelectedActionId(null);
      return;
    }

    if (!filteredActions.some((action) => action.id === selectedActionId)) {
      setSelectedActionId(filteredActions[0]?.id ?? null);
    }
  }, [filteredActions, selectedActionId]);

  const selectedAction = useMemo(
    () => filteredActions.find((action) => action.id === selectedActionId) ?? null,
    [filteredActions, selectedActionId],
  );

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

  const updateAction = async (actionId: number, nextStatus: 'completed' | 'dismissed' | 'pending') => {
    setBusyKey(`action-${actionId}-${nextStatus}`);
    try {
      await endpoints.actionQueue.updateAction(String(actionId), { status: nextStatus, dry_run: false });
      await loadData();
      setToast({ message: `Action marked ${nextStatus}.` });
    } catch (err) {
      setToast({
        message: err instanceof Error ? err.message : 'Failed to update action.',
        tone: 'error',
      });
    } finally {
      setBusyKey(null);
    }
  };

  const counts = status?.database ?? {};
  const progress = status?.progress;

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
          </div>
        }
      />

      <StatGrid>
        <StatCard title="Pending" metric={counts.pending ?? 0} desc="Awaiting review or downstream completion." />
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

      <div className="aq-toolbar">
        <FilterPills
          active={filter}
          onChange={(value) => setFilter(value as ActionFilter)}
          options={[
            { key: 'pending', label: `Pending (${counts.pending ?? 0})` },
            { key: 'completed', label: `Completed (${counts.completed ?? 0})` },
            { key: 'dismissed', label: `Dismissed (${counts.dismissed ?? 0})` },
            { key: 'all', label: `All (${counts.total ?? 0})` },
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

      {loading ? (
        <><SkeletonLoader variant="stat-grid" /><div className="section"><SkeletonLoader variant="table" /></div></>
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <div className="action-queue-layout">
          <Card title={`Pending actions (${filteredActions.length})`} className="aq-table-card">
            {filteredActions.length === 0 ? (
              <EmptyState
                title="No actions match this view"
                desc="Try a different status filter or rerun the pipeline to discover new actions."
              />
            ) : (
              <DataTable<ActionItem>
                rows={filteredActions}
                rowKey={(row) => String(row.id)}
                emptyLabel="No actions found"
                columns={[
                  {
                    key: 'item',
                    header: 'Action item',
                    render: (row) => (
                      <button className="aq-link-button" onClick={() => setSelectedActionId(row.id)}>
                        <div className={selectedActionId === row.id ? 'aq-row-title selected' : 'aq-row-title'}>
                          {row.title || row.document_title || `Action #${row.id}`}
                        </div>
                        <div className="text-muted">{row.correspondent || row.document_title || 'No document metadata'}</div>
                      </button>
                    ),
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
            <Card title="Selected action">
              {selectedAction ? (
                <div className="aq-detail-list">
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

                  <div className="aq-meta-list">
                    <div className="aq-meta-row"><span>Document</span><span>{selectedAction.document_title || `#${selectedAction.document_id ?? '—'}`}</span></div>
                    <div className="aq-meta-row"><span>Correspondent</span><span>{selectedAction.correspondent || '—'}</span></div>
                    <div className="aq-meta-row"><span>Amount</span><span>{formatCurrency(selectedAction.amount)}</span></div>
                    <div className="aq-meta-row"><span>Due date</span><span>{formatDate(selectedAction.due_date)}</span></div>
                    <div className="aq-meta-row"><span>Confidence</span><span>{selectedAction.confidence != null ? `${selectedAction.confidence}%` : '—'}</span></div>
                    <div className="aq-meta-row"><span>Created</span><span>{formatDateTime(selectedAction.created_at)}</span></div>
                    <div className="aq-meta-row"><span>Completed</span><span>{formatDateTime(selectedAction.completed_at)}</span></div>
                  </div>

                  {selectedAction.ai_reasoning && (
                    <div>
                      <div className="section-title">AI reasoning</div>
                      <div className="aq-reasoning">{selectedAction.ai_reasoning}</div>
                    </div>
                  )}

                  <div className="btn-group">
                    <Button
                      variant="success"
                      onClick={() => void updateAction(selectedAction.id, 'completed')}
                      disabled={busyKey !== null}
                    >
                      Mark complete
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => void updateAction(selectedAction.id, 'dismissed')}
                      disabled={busyKey !== null}
                    >
                      Dismiss
                    </Button>
                    {selectedAction.preview_url ? (
                      <a className="aq-preview-link" href={selectedAction.preview_url} target="_blank" rel="noreferrer">
                        Open in Paperless
                      </a>
                    ) : null}
                  </div>
                </div>
              ) : (
                <EmptyState title="Select an action" desc="Pick an item from the queue to inspect its due date, status, and AI reasoning." />
              )}
            </Card>

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

      {toast && <Toast message={toast.message} tone={toast.tone} />}
    </>
  );
}
