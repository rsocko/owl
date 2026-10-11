import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  ConfidenceBar,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SidePanel,
  SkeletonLoader,
  Tabs,
  Toast,
} from '../components/ui';
import OrphanDetail from '../components/triage/OrphanDetail';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/relationship-maintenance.css';

type ToastState = {
  message: string;
  tone: 'success' | 'error';
};

type TriageItem = {
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
};

type TriageQueueResponse = {
  items: TriageItem[];
  count: number;
  offset: number;
  limit: number;
};

type QueueAction = {
  id: number | string;
  title?: string | null;
  summary?: string | null;
  document_title?: string | null;
  preview_url?: string | null;
  urgency?: string | null;
  status?: string | null;
  created_at?: string | null;
};

type QueueActionsResponse = {
  actions?: QueueAction[];
};

type AlertItem = {
  id: number | string;
  title?: string | null;
  description?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  action_url?: string | null;
  resolved_at?: string | null;
};

type AlertsResponse = {
  alerts?: AlertItem[];
};

type RelationshipScanCounts = {
  scan_stage?: 'discovering';
  scope?: { kind: string; description: string };
  documents_discovered?: number;
  metadata_eligible_documents?: number;
  documents_excluded?: number;
  exclusion_reasons?: Record<string, number>;
  documents_examined?: number;
  documents_total?: number;
  candidate_groups?: number;
  candidate_pairs?: number;
  relationships_created?: number;
  relationships_reused?: number;
  review_candidates?: number;
  triage_items_created?: number;
  conflicts?: number;
  failures?: number;
};

type RelationshipScanScopeKind = 'action_queue' | 'tag' | 'saved_view' | 'date_range' | 'all';

type RelationshipScanScope = {
  kind: RelationshipScanScopeKind;
  tags: string[];
  saved_view_id: number | null;
  created_after: string;
  created_before: string;
};

type PaperlessOption = { id: number; name: string };

type RelationshipScanRun = {
  run_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  attached?: boolean;
  counts: RelationshipScanCounts;
  errors: Array<{ code: string; message: string }>;
  projection: {
    status: 'not_required' | 'synced' | 'partial' | 'failed';
    error_code: string | null;
    message: string | null;
  } | null;
};

type OrphanItem = {
  id: string;
  kind: 'eob' | 'bill';
  title: string;
  subtitle: string;
  ageLabel: string;
  detailLabel: string;
  detailValue: string;
  triageItem: TriageItem;
};

type DuplicateItem = {
  id: string;
  sourceKind: 'queue' | 'alert';
  title: string;
  reason: string;
  similarity: number;
  createdAt?: string | null;
  actionId?: number | string;
  previewUrl?: string | null;
  actionUrl?: string | null;
  status: 'pending' | 'merged' | 'dismissed';
};

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

function formatCurrency(value?: number | null) {
  if (value === undefined || value === null) return '—';
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value);
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function extractSimilarity(text: string) {
  const match = text.match(/(\d{2,3})%/);
  if (!match) return 92;
  const parsed = Number(match[1]);
  return Number.isNaN(parsed) ? 92 : Math.max(0, Math.min(100, parsed));
}

function containsDuplicateSignal(text: string) {
  return /duplicate|dupe|merge/i.test(text);
}

function docTypeLabel(docType?: string | null): string {
  if (docType === 'eob') return 'EOB';
  if (docType === 'bill') return 'Bill';
  return 'Document';
}

export default function OrphansDupes() {
  const [activeTab, setActiveTab] = useState('orphans');
  const [orphans, setOrphans] = useState<OrphanItem[]>([]);
  const [duplicates, setDuplicates] = useState<DuplicateItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [selectedOrphan, setSelectedOrphan] = useState<OrphanItem | null>(null);
  const [selectedDuplicate, setSelectedDuplicate] = useState<DuplicateItem | null>(null);
  const [busyDuplicateId, setBusyDuplicateId] = useState<string | null>(null);
  const [orphanFilter, setOrphanFilter] = useState<'all' | 'eob' | 'bill'>('all');
  const [autoDetectEnabled, setAutoDetectEnabled] = useState(false);
  const [autoDetectLoading, setAutoDetectLoading] = useState(false);
  const [scanPanelOpen, setScanPanelOpen] = useState(false);
  const [scanPreview, setScanPreview] = useState<RelationshipScanCounts | null>(null);
  const [scanPreviewLoading, setScanPreviewLoading] = useState(false);
  const [scanRun, setScanRun] = useState<RelationshipScanRun | null>(null);
  const [scanStartPending, setScanStartPending] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanScope, setScanScope] = useState<RelationshipScanScope>({
    kind: 'action_queue',
    tags: [],
    saved_view_id: null,
    created_after: '',
    created_before: '',
  });
  const [paperlessTags, setPaperlessTags] = useState<PaperlessOption[]>([]);
  const [paperlessViews, setPaperlessViews] = useState<PaperlessOption[]>([]);
  const [scopeOptionsLoading, setScopeOptionsLoading] = useState(false);
  const previewRequest = useRef(0);
  const scanStartPendingRef = useRef(false);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [triageResponse, actionsResponse, alertsResponse] = await Promise.allSettled([
        endpoints.triage.queue('type=orphan_document&status=pending&limit=200') as Promise<TriageQueueResponse>,
        endpoints.actionQueue.actions('status=pending&limit=200') as Promise<QueueActionsResponse>,
        endpoints.alerts.list('resolved=true&limit=200') as Promise<AlertsResponse>,
      ]);

      const triageItems = triageResponse.status === 'fulfilled' && Array.isArray(triageResponse.value?.items) ? triageResponse.value.items : [];
      const queueActions = actionsResponse.status === 'fulfilled' && Array.isArray(actionsResponse.value.actions) ? actionsResponse.value.actions : [];
      const alerts = alertsResponse.status === 'fulfilled' && Array.isArray(alertsResponse.value.alerts) ? alertsResponse.value.alerts : [];

      const orphanRows: OrphanItem[] = triageItems.map((item) => {
        const meta = item.metadata ?? {};
        const documentType = (meta.document_type as string) ?? 'eob';
        const providerName = (meta.provider_name as string) ?? 'Unknown provider';
        const amount = meta.amount as number | null;
        const dateOfService = meta.date_of_service as string | null;
        const ageDays = (meta.document_age_days as number) ?? 0;

        return {
          id: item.id,
          kind: (documentType === 'bill' ? 'bill' : 'eob') as 'eob' | 'bill',
          title: `Unmatched ${docTypeLabel(documentType)} from ${providerName}`,
          subtitle: dateOfService ? `Service date ${dateOfService}` : 'Date of service unavailable',
          ageLabel: ageDays > 0 ? `${ageDays} days` : (item.created_at ? formatDateTime(item.created_at) : 'Pending review'),
          detailLabel: documentType === 'eob' ? 'Patient responsibility' : 'Amount',
          detailValue: formatCurrency(amount),
          triageItem: item,
        };
      });

      const queueDuplicateRows = queueActions
        .filter((action) => containsDuplicateSignal(`${action.title ?? ''} ${action.summary ?? ''} ${action.document_title ?? ''}`))
        .map((action) => ({
          id: `queue-${action.id}`,
          sourceKind: 'queue' as const,
          title: action.title ?? action.document_title ?? `Duplicate review ${action.id}`,
          reason: action.summary ?? 'Pending duplicate review from the action queue.',
          similarity: extractSimilarity(`${action.title ?? ''} ${action.summary ?? ''}`),
          createdAt: action.created_at,
          actionId: action.id,
          previewUrl: action.preview_url,
          status: 'pending' as const,
        }));

      const alertDuplicateRows = alerts
        .filter((alert) => containsDuplicateSignal(`${alert.title ?? ''} ${alert.description ?? ''}`))
        .map((alert) => ({
          id: `alert-${alert.id}`,
          sourceKind: 'alert' as const,
          title: alert.title ?? `Duplicate alert ${alert.id}`,
          reason: alert.description ?? 'Duplicate signal emitted by the alerts feed.',
          similarity: extractSimilarity(`${alert.title ?? ''} ${alert.description ?? ''}`),
          createdAt: alert.created_at,
          actionUrl: alert.action_url,
          status: alert.resolved_at ? ('dismissed' as const) : ('pending' as const),
        }));

      const dedupedAlerts = alertDuplicateRows.filter(
        (alertRow) => !queueDuplicateRows.some((queueRow) => queueRow.title === alertRow.title),
      );

      setOrphans(orphanRows);
      setDuplicates([...queueDuplicateRows, ...dedupedAlerts]);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
    // Load duplicate detection settings
    endpoints.duplicates.settings().then(
      (res) => setAutoDetectEnabled(res.auto_detect_enabled),
      () => {/* settings endpoint may not exist yet — ignore */},
    );
  }, []);

  useEffect(() => {
    if (!scanRun || !['queued', 'running'].includes(scanRun.status)) return undefined;
    const interval = window.setInterval(async () => {
      try {
        const next = await endpoints.duplicates.relationshipScanStatus(
          scanRun.run_id,
        ) as RelationshipScanRun;
        setScanRun(next);
        setScanError(null);
        if (next.status === 'completed') {
          setToast({ message: 'Relationship scan completed.', tone: 'success' });
        } else if (next.status === 'failed') {
          setScanError(next.errors[0]?.message ?? 'The relationship scan failed.');
        }
      } catch (err) {
        setScanError(getErrorMessage(err));
      }
    }, 1000);
    return () => window.clearInterval(interval);
  }, [scanRun]);

  const filteredOrphans = useMemo(
    () => orphanFilter === 'all' ? orphans : orphans.filter((o) => o.kind === orphanFilter),
    [orphans, orphanFilter],
  );
  const orphanCount = orphans.length;
  const duplicateCount = duplicates.filter((item) => item.status === 'pending').length;

  const selectedDuplicateLive = useMemo(
    () => duplicates.find((item) => item.id === selectedDuplicate?.id) ?? selectedDuplicate,
    [duplicates, selectedDuplicate],
  );

  const handleToggleAutoDetect = async () => {
    setAutoDetectLoading(true);
    try {
      const res = await endpoints.duplicates.updateSettings({ auto_detect_enabled: !autoDetectEnabled });
      setAutoDetectEnabled(res.auto_detect_enabled);
      setToast({ message: res.auto_detect_enabled ? 'Auto-detect enabled.' : 'Auto-detect disabled.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setAutoDetectLoading(false);
    }
  };

  const scanScopePayload = useMemo(() => ({
    kind: scanScope.kind,
    ...(scanScope.kind === 'tag' ? { tags: scanScope.tags } : {}),
    ...(scanScope.kind === 'saved_view' ? { saved_view_id: scanScope.saved_view_id } : {}),
    ...(scanScope.kind === 'date_range' ? {
      created_after: scanScope.created_after || null,
      created_before: scanScope.created_before || null,
    } : {}),
  }), [scanScope]);

  const scanScopeValidation = useMemo(() => {
    if (scanScope.kind === 'tag' && scanScope.tags.length === 0) {
      return 'Choose at least one Paperless tag.';
    }
    if (scanScope.kind === 'saved_view' && !scanScope.saved_view_id) {
      return 'Choose a Paperless saved view.';
    }
    if (scanScope.kind === 'date_range') {
      if (!scanScope.created_after && !scanScope.created_before) {
        return 'Enter a start date, an end date, or both.';
      }
      if (
        scanScope.created_after
        && scanScope.created_before
        && scanScope.created_after > scanScope.created_before
      ) {
        return 'The start date must be on or before the end date.';
      }
    }
    return null;
  }, [scanScope]);

  const handleRevealRelationshipScan = async () => {
    setScanPanelOpen(true);
    if (scopeOptionsLoading || paperlessTags.length || paperlessViews.length) return;
    setScopeOptionsLoading(true);
    try {
      const [tags, views] = await Promise.all([
        endpoints.actionQueue.metadataTags() as Promise<{ tags?: PaperlessOption[] } | PaperlessOption[]>,
        endpoints.actionQueue.metadataSavedViews() as Promise<{ saved_views?: PaperlessOption[] } | PaperlessOption[]>,
      ]);
      setPaperlessTags(Array.isArray(tags) ? tags : (tags.tags ?? []));
      setPaperlessViews(Array.isArray(views) ? views : (views.saved_views ?? []));
    } catch {
      // Scope metadata is optional; Action Queue and date scopes remain available.
    } finally {
      setScopeOptionsLoading(false);
    }
  };

  useEffect(() => {
    if (!scanPanelOpen || scanScopeValidation) {
      setScanPreview(null);
      return undefined;
    }
    const requestId = ++previewRequest.current;
    const timeout = window.setTimeout(async () => {
      setScanPreviewLoading(true);
      setScanError(null);
      setScanPreview(null);
      try {
        const preview = await endpoints.duplicates.previewRelationshipScan(
          scanScopePayload,
        ) as RelationshipScanCounts;
        if (requestId === previewRequest.current) setScanPreview(preview);
      } catch (err) {
        if (requestId === previewRequest.current) setScanError(getErrorMessage(err));
      } finally {
        if (requestId === previewRequest.current) setScanPreviewLoading(false);
      }
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [scanPanelOpen, scanScopePayload, scanScopeValidation]);

  const handleStartRelationshipScan = async () => {
    if (scanStartPendingRef.current) return;
    scanStartPendingRef.current = true;
    setScanStartPending(true);
    setScanError(null);
    try {
      const run = await endpoints.duplicates.startRelationshipScan(
        scanScopePayload,
      ) as RelationshipScanRun;
      setScanRun(run);
      setScanError(null);
    } catch (err) {
      setScanError(getErrorMessage(err));
    } finally {
      scanStartPendingRef.current = false;
      setScanStartPending(false);
    }
  };

  const handleOpenOrphan = (item: OrphanItem) => {
    setSelectedDuplicate(null);
    setSelectedOrphan(item);
  };

  const handleDuplicateAction = async (item: DuplicateItem, nextStatus: 'merged' | 'dismissed') => {
    setBusyDuplicateId(item.id);
    try {
      if (item.sourceKind === 'queue' && item.actionId !== undefined) {
        await endpoints.actionQueue.updateAction(String(item.actionId), {
          status: nextStatus === 'merged' ? 'completed' : 'dismissed',
          dry_run: true,
        });
      }
      // TODO: add a dedicated duplicate-review backend endpoint once the server can return actual duplicate pairs.
      setDuplicates((current) => current.map((entry) => (entry.id === item.id ? { ...entry, status: nextStatus } : entry)));
      setToast({ message: nextStatus === 'merged' ? 'Duplicate marked merged.' : 'Duplicate dismissed.', tone: 'success' });
      setSelectedDuplicate(null);
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setBusyDuplicateId(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Orphans & duplicates"
        desc="Review unmatched documents, missing recurring statements, and duplicate signals that still need a human decision."
        actions={
          <Button onClick={() => void loadData()} disabled={loading}>
            Refresh
          </Button>
        }
      />

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}

      {loading ? (
        <SkeletonLoader variant="table" rows={6} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadData()} />
      ) : (
        <>
          <div className="section" style={{ marginBottom: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
              <Card title="Orphan documents">
                <div style={{ display: 'grid', gap: 8 }}>
                  <div style={{ fontSize: '1.6rem', fontWeight: 800 }}>{orphanCount}</div>
                  <div className="text-muted">Unmatched documents that need review.</div>
                </div>
              </Card>
              <Card title="Duplicate candidates">
                <div style={{ display: 'grid', gap: 8 }}>
                  <div style={{ fontSize: '1.6rem', fontWeight: 800 }}>{duplicateCount}</div>
                  <div className="text-muted">Action queue items and alerts that mention duplicate or merge review.</div>
                  <label
                    htmlFor="auto-detect-toggle"
                    style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, cursor: 'pointer', fontSize: '0.85rem' }}
                  >
                    <input
                      id="auto-detect-toggle"
                      type="checkbox"
                      checked={autoDetectEnabled}
                      disabled={autoDetectLoading}
                      onChange={() => void handleToggleAutoDetect()}
                      style={{ width: 16, height: 16 }}
                    />
                    Auto-detect duplicates on ingestion
                  </label>
                  <div className="relationship-maintenance">
                    <div className="relationship-maintenance__intro">
                      <strong>Historical relationship maintenance</strong>
                      <span className="text-muted">
                        Find links and review candidates in documents already stored by OWL.
                      </span>
                    </div>
                    <Button
                      size="sm"
                      onClick={() => void handleRevealRelationshipScan()}
                      aria-expanded={scanPanelOpen}
                      aria-controls="historical-relationship-scan"
                    >
                      Scan existing documents for relationships
                    </Button>
                    {scanPanelOpen && (
                      <div
                        id="historical-relationship-scan"
                        className="relationship-maintenance__panel"
                        aria-live="polite"
                      >
                        <p>
                          Choose which Paperless documents OWL should examine. Documents are preserved, but
                          relationship links and Needs Review items may be created when you
                          start the scan.
                        </p>
                        <div className="relationship-maintenance__scope">
                          <label htmlFor="relationship-scan-scope">Scan scope</label>
                          <select
                            id="relationship-scan-scope"
                            value={scanScope.kind}
                            disabled={
                              scanStartPending
                              || scanRun?.status === 'queued'
                              || scanRun?.status === 'running'
                            }
                            onChange={(event) => {
                              setScanRun(null);
                              setScanScope((current) => ({
                                ...current,
                                kind: event.target.value as RelationshipScanScopeKind,
                              }));
                            }}
                          >
                            <option value="action_queue">Configured Action Queue source</option>
                            <option value="tag">Paperless tag</option>
                            <option value="saved_view">Paperless saved view</option>
                            <option value="date_range">Document date range</option>
                            <option value="all">All Paperless documents</option>
                          </select>
                          {scanScope.kind === 'tag' && (
                            <select
                              aria-label="Paperless tag"
                              value={scanScope.tags[0] ?? ''}
                              disabled={scopeOptionsLoading}
                              onChange={(event) => setScanScope((current) => ({
                                ...current,
                                tags: event.target.value ? [event.target.value] : [],
                              }))}
                            >
                              <option value="">
                                {scopeOptionsLoading ? 'Loading tags…' : 'Choose a tag'}
                              </option>
                              {paperlessTags.map((tag) => (
                                <option key={tag.id} value={tag.name}>{tag.name}</option>
                              ))}
                            </select>
                          )}
                          {scanScope.kind === 'saved_view' && (
                            <select
                              aria-label="Paperless saved view"
                              value={scanScope.saved_view_id ?? ''}
                              disabled={scopeOptionsLoading}
                              onChange={(event) => setScanScope((current) => ({
                                ...current,
                                saved_view_id: event.target.value ? Number(event.target.value) : null,
                              }))}
                            >
                              <option value="">
                                {scopeOptionsLoading ? 'Loading saved views…' : 'Choose a saved view'}
                              </option>
                              {paperlessViews.map((view) => (
                                <option key={view.id} value={view.id}>{view.name}</option>
                              ))}
                            </select>
                          )}
                          {scanScope.kind === 'date_range' && (
                            <div className="relationship-maintenance__date-range">
                              <label>
                                From
                                <input
                                  type="date"
                                  value={scanScope.created_after}
                                  onChange={(event) => setScanScope((current) => ({
                                    ...current,
                                    created_after: event.target.value,
                                  }))}
                                />
                              </label>
                              <label>
                                Through
                                <input
                                  type="date"
                                  value={scanScope.created_before}
                                  onChange={(event) => setScanScope((current) => ({
                                    ...current,
                                    created_before: event.target.value,
                                  }))}
                                />
                              </label>
                            </div>
                          )}
                          {scanScope.kind === 'all' && (
                            <p className="relationship-maintenance__warning">
                              This scope may take a while for a large library. The scan will run
                              in the background and report progress here.
                            </p>
                          )}
                          {scanScopeValidation && (
                            <span className="relationship-maintenance__validation" role="alert">
                              {scanScopeValidation}
                            </span>
                          )}
                        </div>
                        {scanPreviewLoading && (
                          <span className="relationship-maintenance__status">
                            Discovering documents and estimating candidates…
                          </span>
                        )}
                        {scanPreview && (
                          <>
                            <strong className="relationship-maintenance__scope-description">
                              {scanPreview.scope?.description ?? 'Selected Paperless scope'}
                            </strong>
                            <dl className="relationship-maintenance__metrics">
                              <div className="relationship-maintenance__metric">
                                <dt>Discovered documents</dt>
                                <dd>{scanPreview.documents_discovered ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Metadata eligible</dt>
                                <dd>{scanPreview.metadata_eligible_documents ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Excluded documents</dt>
                                <dd>{scanPreview.documents_excluded ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Candidate groups</dt>
                                <dd>{scanPreview.candidate_groups ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Candidate pairs</dt>
                                <dd>{scanPreview.candidate_pairs ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Existing links reusable</dt>
                                <dd>{scanPreview.relationships_reused ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Review candidates</dt>
                                <dd>{scanPreview.review_candidates ?? 0}</dd>
                              </div>
                            </dl>
                            {Object.keys(scanPreview.exclusion_reasons ?? {}).length > 0 && (
                              <div className="relationship-maintenance__exclusions">
                                <span>Exclusions</span>
                                <ul>
                                  {Object.entries(scanPreview.exclusion_reasons ?? {}).map(
                                    ([reason, count]) => (
                                      <li key={reason}>
                                        {reason.replaceAll('_', ' ')}: {count}
                                      </li>
                                    ),
                                  )}
                                </ul>
                              </div>
                            )}
                            {(scanPreview.documents_discovered ?? 0) === 0 && (
                              <span className="relationship-maintenance__status">
                                No documents match this scope. Choose another scope to continue.
                              </span>
                            )}
                          </>
                        )}
                        {scanRun && (
                          <>
                            <div className="relationship-maintenance__status">
                              {scanRun.status === 'queued' && 'Scan queued.'}
                              {scanRun.status === 'running' && (
                                `${scanRun.counts.scan_stage === 'discovering' ? 'Discovering' : 'Examining'} `
                                + `${scanRun.counts.scan_stage === 'discovering'
                                  ? scanRun.counts.documents_discovered ?? 0
                                  : scanRun.counts.documents_examined ?? 0} of `
                                + `${scanRun.counts.documents_total ?? '—'} documents…`
                              )}
                              {scanRun.status === 'completed' && 'Scan completed.'}
                              {scanRun.status === 'failed' && 'Scan failed.'}
                            </div>
                            <dl className="relationship-maintenance__metrics">
                              <div className="relationship-maintenance__metric">
                                <dt>Documents examined</dt>
                                <dd>{scanRun.counts.documents_examined ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Relationships created</dt>
                                <dd>{scanRun.counts.relationships_created ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Relationships reused</dt>
                                <dd>{scanRun.counts.relationships_reused ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Review items created</dt>
                                <dd>{scanRun.counts.triage_items_created ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Conflicts</dt>
                                <dd>{scanRun.counts.conflicts ?? 0}</dd>
                              </div>
                              <div className="relationship-maintenance__metric">
                                <dt>Failures</dt>
                                <dd>{scanRun.counts.failures ?? 0}</dd>
                              </div>
                            </dl>
                            {scanRun.status === 'completed'
                              && scanRun.projection
                              && !['synced', 'not_required'].includes(scanRun.projection.status)
                              && (
                                <div className="relationship-maintenance__projection-notice">
                                  <strong>Relationships are safely stored in OWL.</strong>
                                  <span>
                                    Paperless metadata sync requires repair
                                    {scanRun.projection.error_code
                                      ? ` (${scanRun.projection.error_code.replaceAll('_', ' ')})`
                                      : ''}.
                                  </span>
                                  <a href="#/action-queue/operations">Repair Paperless sync</a>
                                </div>
                              )}
                          </>
                        )}
                        {scanError && <ErrorState message={scanError} />}
                        {scanRun?.errors.length ? (
                          <ul className="relationship-maintenance__errors">
                            {scanRun.errors.map((item, index) => (
                              <li key={`${item.code}-${index}`}>{item.message}</li>
                            ))}
                          </ul>
                        ) : null}
                        <div className="btn-group">
                          <Button
                            variant="primary"
                            onClick={() => void handleStartRelationshipScan()}
                            disabled={
                              scanPreviewLoading
                              || scanStartPending
                              || Boolean(scanScopeValidation)
                              || !scanPreview
                              || (scanPreview.documents_discovered ?? 0) === 0
                              || scanRun?.status === 'queued'
                              || scanRun?.status === 'running'
                            }
                          >
                            {scanStartPending
                              ? 'Starting relationship scan…'
                              : scanRun?.status === 'queued' || scanRun?.status === 'running'
                                ? 'Scan in progress…'
                                : 'Start relationship scan'}
                          </Button>
                          <Button
                            onClick={() => setScanPanelOpen(false)}
                            disabled={
                              scanStartPending
                              || scanRun?.status === 'queued'
                              || scanRun?.status === 'running'
                            }
                          >
                            Cancel
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            </div>
          </div>

          <div className="section">
            <Tabs
              active={activeTab}
              onChange={setActiveTab}
              tabs={[
                { key: 'orphans', label: `Orphaned documents (${orphans.length})` },
                { key: 'duplicates', label: `Duplicate detections (${duplicates.length})` },
              ]}
            />
          </div>

          {activeTab === 'orphans' ? (
            <div className="section">
              <Card title="Orphaned documents">
                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                  <Button size="sm" variant={orphanFilter === 'all' ? 'primary' : undefined} onClick={() => setOrphanFilter('all')}>All ({orphans.length})</Button>
                  <Button size="sm" variant={orphanFilter === 'eob' ? 'primary' : undefined} onClick={() => setOrphanFilter('eob')}>EOBs ({orphans.filter((o) => o.kind === 'eob').length})</Button>
                  <Button size="sm" variant={orphanFilter === 'bill' ? 'primary' : undefined} onClick={() => setOrphanFilter('bill')}>Bills ({orphans.filter((o) => o.kind === 'bill').length})</Button>
                </div>
                {filteredOrphans.length === 0 ? (
                  <EmptyState title="No orphaned documents found." desc="Unmatched EOBs or bills that need review will appear here." />
                ) : (
                  <DataTable
                    rows={filteredOrphans}
                    rowKey={(row) => row.id}
                    columns={[
                      {
                        key: 'kind',
                        header: 'Type',
                        width: '120px',
                        render: (row) => <Badge tone={row.kind === 'eob' ? 'danger' : 'warning'}>{docTypeLabel(row.kind)}</Badge>,
                      },
                      {
                        key: 'item',
                        header: 'Item',
                        render: (row) => (
                          <div>
                            <div style={{ fontWeight: 700 }}>{row.title}</div>
                            <div className="text-muted" style={{ fontSize: '0.82rem', marginTop: 4 }}>{row.subtitle}</div>
                          </div>
                        ),
                      },
                      {
                        key: 'detail',
                        header: 'Detail',
                        width: '180px',
                        render: (row) => (
                          <div>
                            <div style={{ fontWeight: 600 }}>{row.detailValue}</div>
                            <div className="text-muted" style={{ fontSize: '0.8rem' }}>{row.detailLabel}</div>
                          </div>
                        ),
                      },
                      {
                        key: 'age',
                        header: 'Age',
                        width: '120px',
                        render: (row) => row.ageLabel,
                      },
                      {
                        key: 'priority',
                        header: 'Priority',
                        width: '100px',
                        render: (row) => <Badge tone={row.triageItem.priority >= 70 ? 'danger' : row.triageItem.priority >= 50 ? 'warning' : 'muted'}>{row.triageItem.priority}</Badge>,
                      },
                      {
                        key: 'actions',
                        header: 'Actions',
                        width: '120px',
                        render: (row) => (
                          <Button size="sm" variant="primary" onClick={() => handleOpenOrphan(row)}>
                            Review
                          </Button>
                        ),
                      },
                    ]}
                  />
                )}
              </Card>
            </div>
          ) : (
            <div className="section">
              <Card title="Duplicate detections">
                {duplicates.length === 0 ? (
                  <EmptyState title="No duplicate detections are available yet." desc="This UI is wired to current queue and alert signals until a dedicated duplicate-pairs endpoint lands." />
                ) : (
                  <DataTable
                    rows={duplicates}
                    rowKey={(row) => row.id}
                    columns={[
                      {
                        key: 'item',
                        header: 'Candidate',
                        render: (row) => (
                          <div>
                            <div style={{ fontWeight: 700 }}>{row.title}</div>
                            <div className="text-muted" style={{ fontSize: '0.82rem', marginTop: 4 }}>{row.reason}</div>
                          </div>
                        ),
                      },
                      {
                        key: 'similarity',
                        header: 'Similarity',
                        width: '220px',
                        render: (row) => <ConfidenceBar label="Match signal" pct={row.similarity} />,
                      },
                      {
                        key: 'source',
                        header: 'Source',
                        width: '130px',
                        render: (row) => <Badge tone="info">{row.sourceKind === 'queue' ? 'Action queue' : 'Alert feed'}</Badge>,
                      },
                      {
                        key: 'status',
                        header: 'Status',
                        width: '120px',
                        render: (row) => <Badge tone={row.status === 'pending' ? 'warning' : row.status === 'merged' ? 'success' : 'muted'}>{row.status}</Badge>,
                      },
                      {
                        key: 'actions',
                        header: 'Actions',
                        width: '220px',
                        render: (row) => (
                          <div className="btn-group" style={{ justifyContent: 'flex-end' }}>
                            <Button size="sm" onClick={() => { setSelectedOrphan(null); setSelectedDuplicate(row); }}>
                              Review
                            </Button>
                            <Button
                              size="sm"
                              variant="primary"
                              onClick={() => void handleDuplicateAction(row, 'merged')}
                              disabled={row.status !== 'pending' || busyDuplicateId !== null}
                            >
                              {busyDuplicateId === row.id ? 'Saving…' : 'Merge'}
                            </Button>
                          </div>
                        ),
                      },
                    ]}
                  />
                )}
              </Card>
            </div>
          )}
        </>
      )}

      {selectedOrphan && (
        <SidePanel title={selectedOrphan.title} onClose={() => setSelectedOrphan(null)}>
          <OrphanDetail
            triageItem={selectedOrphan.triageItem}
            onResolved={() => {
              setSelectedOrphan(null);
              void loadData();
            }}
            onSkip={() => {
              const currentIndex = filteredOrphans.findIndex((o) => o.id === selectedOrphan.id);
              const next = filteredOrphans[currentIndex + 1];
              if (next) {
                setSelectedOrphan(next);
              } else {
                setSelectedOrphan(null);
              }
            }}
          />
        </SidePanel>
      )}

      {selectedDuplicateLive && (
        <SidePanel title={selectedDuplicateLive.title} onClose={() => setSelectedDuplicate(null)}>
          <div style={{ display: 'grid', gap: 16 }}>
            <div>
              <Badge tone="warning">Duplicate signal</Badge>
              <div className="text-muted" style={{ fontSize: '0.85rem', marginTop: 10 }}>{selectedDuplicateLive.reason}</div>
            </div>

            <Card title="Similarity analysis">
              <div style={{ display: 'grid', gap: 10 }}>
                <ConfidenceBar label="Duplicate score" pct={selectedDuplicateLive.similarity} />
                <div className="text-muted" style={{ fontSize: '0.82rem' }}>
                  Seeded from the {selectedDuplicateLive.sourceKind === 'queue' ? 'action queue' : 'alert feed'} because a dedicated duplicate-pairs backend endpoint is not available yet.
                </div>
                <div className="text-muted" style={{ fontSize: '0.82rem' }}>Created {formatDateTime(selectedDuplicateLive.createdAt)}</div>
              </div>
            </Card>

            <div className="btn-group">
              <Button onClick={() => setSelectedDuplicate(null)} disabled={busyDuplicateId !== null}>Close</Button>
              {selectedDuplicateLive.previewUrl && (
                <Button onClick={() => window.open(selectedDuplicateLive.previewUrl ?? '#', '_blank', 'noopener')}>
                  Open preview
                </Button>
              )}
              {selectedDuplicateLive.actionUrl && (
                <Button onClick={() => window.open(selectedDuplicateLive.actionUrl ?? '#', '_blank', 'noopener')}>
                  Open source
                </Button>
              )}
              <Button
                variant="danger"
                onClick={() => void handleDuplicateAction(selectedDuplicateLive, 'dismissed')}
                disabled={selectedDuplicateLive.status !== 'pending' || busyDuplicateId !== null}
              >
                {busyDuplicateId === selectedDuplicateLive.id ? 'Saving…' : 'Dismiss'}
              </Button>
              <Button
                variant="primary"
                onClick={() => void handleDuplicateAction(selectedDuplicateLive, 'merged')}
                disabled={selectedDuplicateLive.status !== 'pending' || busyDuplicateId !== null}
              >
                {busyDuplicateId === selectedDuplicateLive.id ? 'Saving…' : 'Merge & link'}
              </Button>
            </div>
          </div>
        </SidePanel>
      )}
    </>
  );
}
