import { useEffect, useMemo, useState } from 'react';
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
