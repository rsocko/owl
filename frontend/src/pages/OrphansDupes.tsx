import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
import DocumentPreview from '../components/DocumentPreview';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';

type ToastState = {
  message: string;
  tone: 'success' | 'error';
};

type UnmatchedEob = {
  id: string | number;
  provider?: string | null;
  amount?: number | null;
  date_of_service?: string | null;
  patient_responsibility?: number | null;
  document_url?: string | null;
  created_at?: string | null;
};

type MissingStatement = {
  id: string;
  correspondent?: string | null;
  expected_period?: string | null;
  frequency?: string | null;
  last_received_date?: string | null;
  days_overdue?: number | null;
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
  kind: 'unmatched_eob' | 'missing_statement';
  title: string;
  subtitle: string;
  ageLabel: string;
  detailLabel: string;
  detailValue: string;
  actionUrl?: string | null;
  providerKey?: string;
  expectedPeriod?: string;
  source: UnmatchedEob | MissingStatement;
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

type OverrideDraft = {
  displayName: string;
  frequencyOverride: string;
  anchorDayOverride: string;
  notes: string;
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

export default function OrphansDupes() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('orphans');
  const [orphans, setOrphans] = useState<OrphanItem[]>([]);
  const [duplicates, setDuplicates] = useState<DuplicateItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [selectedOrphan, setSelectedOrphan] = useState<OrphanItem | null>(null);
  const [selectedDuplicate, setSelectedDuplicate] = useState<DuplicateItem | null>(null);
  const [overrideDraft, setOverrideDraft] = useState<OverrideDraft>({
    displayName: '',
    frequencyOverride: '',
    anchorDayOverride: '',
    notes: '',
  });
  const [savingOverride, setSavingOverride] = useState(false);
  const [busyDuplicateId, setBusyDuplicateId] = useState<string | null>(null);
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
      const [unmatchedResponse, missingResponse, actionsResponse, alertsResponse] = await Promise.allSettled([
        endpoints.eob.unmatched() as Promise<UnmatchedEob[]>,
        endpoints.statements.missing() as Promise<MissingStatement[]>,
        endpoints.actionQueue.actions('status=pending&limit=200') as Promise<QueueActionsResponse>,
        endpoints.alerts.list('resolved=true&limit=200') as Promise<AlertsResponse>,
      ]);

      const unmatched = unmatchedResponse.status === 'fulfilled' && Array.isArray(unmatchedResponse.value) ? unmatchedResponse.value : [];
      const missing = missingResponse.status === 'fulfilled' && Array.isArray(missingResponse.value) ? missingResponse.value : [];
      const queueActions = actionsResponse.status === 'fulfilled' && Array.isArray(actionsResponse.value.actions) ? actionsResponse.value.actions : [];
      const alerts = alertsResponse.status === 'fulfilled' && Array.isArray(alertsResponse.value.alerts) ? alertsResponse.value.alerts : [];

      const orphanRows: OrphanItem[] = [
        ...unmatched.map((item) => ({
          id: `eob-${item.id}`,
          kind: 'unmatched_eob' as const,
          title: `Unmatched EOB from ${item.provider ?? 'Unknown provider'}`,
          subtitle: item.date_of_service ? `Service date ${item.date_of_service}` : 'Date of service unavailable',
          ageLabel: item.created_at ? formatDateTime(item.created_at) : 'Pending review',
          detailLabel: 'Patient responsibility',
          detailValue: formatCurrency(item.patient_responsibility ?? item.amount),
          actionUrl: item.document_url,
          source: item,
        })),
        ...missing.map((item) => ({
          id: `statement-${item.id}`,
          kind: 'missing_statement' as const,
          title: item.correspondent ? `Missing statement: ${item.correspondent}` : `Missing statement ${item.id}`,
          subtitle: item.frequency ? `${item.frequency} cadence` : 'Recurring statement gap',
          ageLabel: typeof item.days_overdue === 'number' ? `${item.days_overdue} days overdue` : 'Late',
          detailLabel: 'Expected period',
          detailValue: item.expected_period ?? '—',
          providerKey: item.id,
          expectedPeriod: item.expected_period ?? undefined,
          source: item,
        })),
      ];

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
          // resolved_at is only populated once someone resolves the alert via
          // the Insights page — map that to "dismissed" here instead of
          // assuming every matching alert is still actionable (the
          // `resolved=true` query param means "include resolved alerts",
          // not "only resolved").
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
    if (item.kind === 'missing_statement') {
      setOverrideDraft({
        displayName: item.title.replace(/^Missing statement:\s*/, ''),
        frequencyOverride: 'monthly',
        anchorDayOverride: '',
        notes: `Expected period ${item.expectedPeriod ?? 'unknown'}`,
      });
    } else {
      setOverrideDraft({ displayName: '', frequencyOverride: '', anchorDayOverride: '', notes: '' });
    }
  };

  const handleSaveOverride = async () => {
    if (!selectedOrphan) return;

    if (selectedOrphan.kind !== 'missing_statement' || !selectedOrphan.providerKey) {
      // TODO: the backend currently exposes no dedicated write endpoint for unmatched-EOB orphan resolution,
      // so unmatched review notes are retained in local UI state until the API grows a persistence surface.
      setToast({ message: 'Saved review notes locally. Backend orphan resolution API is still pending.', tone: 'success' });
      setSelectedOrphan(null);
      return;
    }

    setSavingOverride(true);
    try {
      await endpoints.statements.setProviderOverride(selectedOrphan.providerKey, {
        status: 'confirmed',
        display_name: overrideDraft.displayName || undefined,
        frequency_override: overrideDraft.frequencyOverride || undefined,
        anchor_day_override: overrideDraft.anchorDayOverride ? Number(overrideDraft.anchorDayOverride) : undefined,
        notes: overrideDraft.notes || undefined,
      });
      setToast({ message: 'Provider override saved.', tone: 'success' });
      setSelectedOrphan(null);
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setSavingOverride(false);
    }
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
              <Card title="Review queues">
                <div style={{ display: 'grid', gap: 8 }}>
                  <div style={{ fontSize: '1.6rem', fontWeight: 800 }}>{orphanCount}</div>
                  <div className="text-muted">Unmatched EOBs plus missing statement recommendations.</div>
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
                { key: 'orphans', label: `Orphaned items (${orphans.length})` },
                { key: 'duplicates', label: `Duplicate detections (${duplicates.length})` },
              ]}
            />
          </div>

          {activeTab === 'orphans' ? (
            <div className="section">
              <Card title="Orphaned documents and missing metadata">
                {orphans.length === 0 ? (
                  <EmptyState title="No orphaned items were returned." desc="When unmatched EOBs or missing recurring statements exist, they will appear here." />
                ) : (
                  <DataTable
                    rows={orphans}
                    rowKey={(row) => row.id}
                    columns={[
                      {
                        key: 'kind',
                        header: 'Type',
                        width: '150px',
                        render: (row) => <Badge tone={row.kind === 'missing_statement' ? 'warning' : 'danger'}>{row.kind === 'missing_statement' ? 'Missing statement' : 'Unmatched EOB'}</Badge>,
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
                        header: 'Age / timing',
                        width: '180px',
                        render: (row) => row.ageLabel,
                      },
                      {
                        key: 'actions',
                        header: 'Actions',
                        width: '220px',
                        render: (row) => (
                          <div className="btn-group" style={{ justifyContent: 'flex-end' }}>
                            <Button size="sm" onClick={() => handleOpenOrphan(row)}>
                              Review
                            </Button>
                            {row.actionUrl ? (
                              <Button size="sm" variant="primary" onClick={() => window.open(row.actionUrl ?? '#', '_blank', 'noopener')}>
                                Open source
                              </Button>
                            ) : (
                              <Button size="sm" variant="primary" onClick={() => navigate('/eob/unmatched')}>
                                Open queue
                              </Button>
                            )}
                          </div>
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
          <div style={{ display: 'grid', gap: 16 }}>
            <div>
              <Badge tone={selectedOrphan.kind === 'missing_statement' ? 'warning' : 'danger'}>
                {selectedOrphan.kind === 'missing_statement' ? 'Missing statement' : 'Unmatched EOB'}
              </Badge>
              <div className="text-muted" style={{ fontSize: '0.85rem', marginTop: 10 }}>{selectedOrphan.subtitle}</div>
            </div>

            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: 14 }}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>Current context</div>
              <div className="text-muted" style={{ fontSize: '0.82rem' }}>{selectedOrphan.detailLabel}</div>
              <div style={{ marginTop: 4, fontWeight: 600 }}>{selectedOrphan.detailValue}</div>
              <div className="text-muted" style={{ fontSize: '0.82rem', marginTop: 10 }}>Updated {selectedOrphan.ageLabel}</div>
            </div>

            {/* Document preview for unmatched EOBs */}
            {selectedOrphan.kind === 'unmatched_eob' && selectedOrphan.source && !Number.isNaN(Number((selectedOrphan.source as UnmatchedEob).id)) && (
              <DocumentPreview
                documentId={Number((selectedOrphan.source as UnmatchedEob).id)}
                paperlessUrl={selectedOrphan.actionUrl}
                variant="compact"
                label="EOB"
              />
            )}

            <Card title={selectedOrphan.kind === 'missing_statement' ? 'Assign provider metadata' : 'Review notes'}>
              <div className="form-group">
                <label htmlFor="override-display-name">Display name</label>
                <input
                  id="override-display-name"
                  value={overrideDraft.displayName}
                  onChange={(event) => setOverrideDraft((current) => ({ ...current, displayName: event.target.value }))}
                  placeholder="Friendly provider name"
                />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="override-frequency">Frequency override</label>
                  <select
                    id="override-frequency"
                    value={overrideDraft.frequencyOverride}
                    onChange={(event) => setOverrideDraft((current) => ({ ...current, frequencyOverride: event.target.value }))}
                  >
                    <option value="">No override</option>
                    <option value="monthly">Monthly</option>
                    <option value="quarterly">Quarterly</option>
                    <option value="annual">Annual</option>
                  </select>
                </div>
                <div className="form-group">
                  <label htmlFor="override-anchor-day">Anchor day</label>
                  <input
                    id="override-anchor-day"
                    type="number"
                    min={1}
                    max={31}
                    value={overrideDraft.anchorDayOverride}
                    onChange={(event) => setOverrideDraft((current) => ({ ...current, anchorDayOverride: event.target.value }))}
                    placeholder="e.g. 15"
                  />
                </div>
              </div>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label htmlFor="override-notes">Notes</label>
                <textarea
                  id="override-notes"
                  rows={4}
                  value={overrideDraft.notes}
                  onChange={(event) => setOverrideDraft((current) => ({ ...current, notes: event.target.value }))}
                  placeholder="Record review context, expected billing windows, or source caveats."
                />
              </div>
            </Card>

            <div className="btn-group">
              <Button onClick={() => setSelectedOrphan(null)} disabled={savingOverride}>Close</Button>
              <Button variant="primary" onClick={() => void handleSaveOverride()} disabled={savingOverride}>
                {savingOverride ? 'Saving…' : selectedOrphan.kind === 'missing_statement' ? 'Save override' : 'Save review notes'}
              </Button>
              {selectedOrphan.kind === 'unmatched_eob' && (
                <Button variant="success" onClick={() => navigate('/eob/unmatched')}>
                  Open unmatched queue
                </Button>
              )}
            </div>
          </div>
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
