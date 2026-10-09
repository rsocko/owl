import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Badge, Breadcrumb, Button, Card, EmptyState, ErrorState, PageHeader, SkeletonLoader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import { SeriesTimeline } from '../components/triage/SeriesTimeline';
import { SplitSeriesFlow } from '../components/triage/SplitSeriesFlow';
import { MergeSeriesFlow } from '../components/triage/MergeSeriesFlow';
import { StatementAccountGroups } from '../components/triage/StatementAccountGroups';
import DocumentPreview from '../components/DocumentPreview';
import type { SeriesInfo, SeriesDoc, TimelineEntry } from '../components/triage/StatementGroupingDetail';
import '../styles/statement-series-detail.css';

interface SeriesDetailResponse {
  series: SeriesInfo;
  documents: SeriesDoc[];
  timeline: TimelineEntry[];
  similar_series: SeriesInfo[];
  anomaly_indicators: string[];
  excluded_documents?: SeriesDoc[];
  membership_complete?: boolean;
}

interface ProviderOverride {
  provider_key?: string;
  status?: string;
  display_name?: string | null;
  frequency_override?: string | null;
  anchor_day_override?: number | null;
  notes?: string | null;
  updated_at?: string | null;
}

type ActiveFlow = 'none' | 'split' | 'merge' | 'rename';

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong while loading the statement series.';
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function statusTone(status?: string): 'ok' | 'warning' | 'danger' | 'info' | 'muted' {
  switch (status) {
    case 'confirmed':
      return 'ok';
    case 'needs_review':
    case 'flagged':
      return 'warning';
    case 'rejected':
      return 'danger';
    case 'pending':
      return 'muted';
    default:
      return 'info';
  }
}

const ACCOUNT_COLORS = ['var(--series-a)', 'var(--series-b)', 'var(--series-c)', 'var(--series-d)'];

export default function StatementSeriesDetail() {
  const { seriesId } = useParams();
  const navigate = useNavigate();
  const decodedSeriesId = decodeURIComponent(seriesId ?? '');

  const [detail, setDetail] = useState<SeriesDetailResponse | null>(null);
  const [overrides, setOverrides] = useState<Record<string, ProviderOverride>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [updatingDocumentId, setUpdatingDocumentId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);

  const [activeFlow, setActiveFlow] = useState<ActiveFlow>('none');
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());

  const [overrideStatus, setOverrideStatus] = useState('pending');
  const [displayName, setDisplayName] = useState('');
  const [frequencyOverride, setFrequencyOverride] = useState('');
  const [anchorDay, setAnchorDay] = useState('');
  const [notes, setNotes] = useState('');

  // Rename form state
  const [renameForm, setRenameForm] = useState({ name: '', account_identifier: '' });

  const loadDetail = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [seriesResponse, overridesResponse] = (await Promise.all([
        endpoints.statements.seriesDetail(decodedSeriesId),
        endpoints.statements.providerOverrides(),
      ])) as [SeriesDetailResponse, Record<string, ProviderOverride>];
      setDetail(seriesResponse);
      setOverrides(overridesResponse ?? {});
      setRenameForm({
        name: seriesResponse.series.name,
        account_identifier: seriesResponse.series.account_identifier || '',
      });
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [decodedSeriesId]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const series = detail?.series;
  const documents = useMemo(() => detail?.documents ?? [], [detail?.documents]);
  const timeline = useMemo(() => detail?.timeline ?? [], [detail?.timeline]);
  const similarSeries = useMemo(() => detail?.similar_series ?? [], [detail?.similar_series]);
  const anomalyIndicators = useMemo(
    () => detail?.anomaly_indicators ?? [],
    [detail?.anomaly_indicators],
  );
  const excludedDocuments = useMemo(
    () => detail?.excluded_documents ?? [],
    [detail?.excluded_documents],
  );

  const seriesOverride = overrides[decodedSeriesId] ?? overrides[series?.id ?? ''];
  const canonicalSeriesKey = series?.id ?? decodedSeriesId;
  const seriesName = seriesOverride?.display_name || series?.name || decodedSeriesId || 'Statement series';
  const correspondentName = series?.correspondent_name || 'Unknown correspondent';
  const isCandidate = series?.source === 'discovery';
  const membershipComplete = detail?.membership_complete ?? !isCandidate;
  const detectedDocumentCount = series?.detected_document_count ?? series?.document_count ?? documents.length;
  const isDiscoverySample = isCandidate && !membershipComplete;
  const documentListTitle = isCandidate
    ? isDiscoverySample
      ? `Sample documents (${documents.length} of ${detectedDocumentCount})`
      : `Documents to include (${documents.length})`
    : `Documents in series (${documents.length})`;

  // Derive unique accounts and color map
  const accounts = useMemo(() =>
    Array.from(new Set(documents.map(d => d.account_hint).filter(Boolean))) as string[],
    [documents],
  );
  const accountColorMap = useMemo(() =>
    Object.fromEntries(accounts.map((a, i) => [a, ACCOUNT_COLORS[i % ACCOUNT_COLORS.length]])),
    [accounts],
  );

  const statusBadge = useMemo(() => {
    if (isCandidate) return { label: 'Needs review', tone: 'warning' as const };
    if (seriesOverride?.status) {
      return { label: seriesOverride.status, tone: statusTone(seriesOverride.status) };
    }
    if (series?.manually_curated) return { label: 'Curated', tone: 'ok' as const };
    if (anomalyIndicators.length > 0) return { label: 'Needs review', tone: 'warning' as const };
    if (series) return { label: 'Tracking', tone: 'info' as const };
    return { label: 'Unknown', tone: 'muted' as const };
  }, [seriesOverride?.status, series, anomalyIndicators, isCandidate]);

  useEffect(() => {
    setOverrideStatus(seriesOverride?.status ?? 'pending');
    setDisplayName(seriesOverride?.display_name ?? series?.name ?? '');
    setFrequencyOverride(seriesOverride?.frequency_override ?? series?.frequency ?? '');
    setAnchorDay(seriesOverride?.anchor_day_override ? String(seriesOverride.anchor_day_override) : '');
    setNotes(seriesOverride?.notes ?? '');
  }, [seriesOverride, series]);

  // ---- Actions ----

  const toggleDocSelection = (docId: string) => {
    setSelectedDocs(prev => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  };

  const selectAllByAccount = (account: string) => {
    const ids = documents.filter(d => d.account_hint === account).map(d => d.document_id);
    setSelectedDocs(new Set(ids));
  };

  const handleRename = async () => {
    if (!renameForm.name) return;
    setBusy(true);
    try {
      await endpoints.statements.seriesRename(decodedSeriesId, {
        name: renameForm.name || undefined,
        account_identifier: renameForm.account_identifier || undefined,
      });
      setActiveFlow('none');
      await loadDetail();
      setToast({ message: 'Series renamed successfully.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const handleSplitComplete = async () => {
    setActiveFlow('none');
    setSelectedDocs(new Set());
    await loadDetail();
    setToast({
      message: isCandidate
        ? 'Candidate split. Both groups remain unconfirmed.'
        : 'Series split completed.',
      tone: 'success',
    });
  };

  const handleMergeComplete = async () => {
    setActiveFlow('none');
    await loadDetail();
    setToast({
      message: isCandidate ? 'Candidates merged for continued review.' : 'Series merge completed.',
      tone: 'success',
    });
  };

  const handleCandidateDocument = async (documentId: string, exclude: boolean) => {
    setUpdatingDocumentId(documentId);
    try {
      if (exclude) {
        await endpoints.statements.candidateExcludeDocument(decodedSeriesId, documentId);
      } else {
        await endpoints.statements.candidateRestoreDocument(decodedSeriesId, documentId);
      }
      await loadDetail();
      setToast({
        message: exclude ? 'Document excluded from this candidate.' : 'Document restored to this candidate.',
        tone: 'success',
      });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setUpdatingDocumentId(null);
    }
  };

  const handleConfirmCandidate = async () => {
    setBusy(true);
    try {
      await endpoints.statements.candidateConfirm(decodedSeriesId);
      await loadDetail();
      setToast({ message: 'Statement series confirmed.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const handleSaveOverride = useCallback(async () => {
    try {
      setSaving(true);
      await endpoints.statements.setProviderOverride(canonicalSeriesKey, {
        status: overrideStatus,
        display_name: displayName || undefined,
        frequency_override: frequencyOverride || undefined,
        anchor_day_override: anchorDay ? Number(anchorDay) : undefined,
        notes: notes || undefined,
      });
      await loadDetail();
      setToast({ message: 'Provider override saved.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setSaving(false);
    }
  }, [anchorDay, canonicalSeriesKey, displayName, frequencyOverride, loadDetail, notes, overrideStatus]);

  const handleClearOverride = useCallback(async () => {
    try {
      setClearing(true);
      await endpoints.statements.clearProviderOverride(canonicalSeriesKey);
      await loadDetail();
      setToast({ message: 'Provider override cleared.', tone: 'success' });
    } catch (err) {
      setToast({ message: getErrorMessage(err), tone: 'error' });
    } finally {
      setClearing(false);
    }
  }, [canonicalSeriesKey, loadDetail]);

  return (
    <>
      <Breadcrumb
        items={[
          { label: 'Statements', to: '/statements' },
          { label: 'Series Detail' },
        ]}
      />
      <PageHeader
        title={seriesName}
        desc={isCandidate
          ? `Review ${correspondentName} documents before creating this ${series?.frequency || 'recurring'} series`
          : `${correspondentName} · ${series?.frequency || 'Unknown cadence'} statement series`}
        actions={
          <div className="btn-group">
            <Button onClick={() => navigate('/statements')}>Back to Statements</Button>
          </div>
        }
      />

      {loading ? <SkeletonLoader variant="cards" /> : null}
      {!loading && error ? <ErrorState message={error} onRetry={() => void loadDetail()} /> : null}
      {!loading && !error && detail ? (
        <>
          {/* Series info grid */}
          <div className="statement-series-info section">
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Correspondent</div>
              <div className="statement-series-info-value">{correspondentName}</div>
            </div>
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Frequency</div>
              <div className="statement-series-info-value">{series?.frequency || 'Unknown cadence'}</div>
            </div>
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">{isCandidate ? 'Documents to include' : 'Documents'}</div>
              <div className="statement-series-info-value">
                {documents.length}
                {isCandidate && (
                  <span className="statement-series-info-sub">
                    {' '}of {detectedDocumentCount} detected
                    {excludedDocuments.length > 0 && ` · ${excludedDocuments.length} excluded`}
                  </span>
                )}
                {series?.first_seen && series?.last_seen && (
                  <span className="statement-series-info-sub"> · {series.first_seen} – {series.last_seen}</span>
                )}
              </div>
            </div>
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Account</div>
              <div className="statement-series-info-value">
                {series?.account_identifier || (accounts.length > 1 ? '⚠️ Multiple detected' : accounts[0] || '—')}
              </div>
            </div>
            <div className="statement-series-info-item">
              <div className="statement-series-info-label">Status</div>
              <div className="statement-series-info-value">
                <Badge tone={statusBadge.tone}>{statusBadge.label}</Badge>
              </div>
            </div>
          </div>

          {/* Anomaly indicators */}
          {anomalyIndicators.length > 0 && (
            <div className="statement-series-anomalies section">
              {anomalyIndicators.map((ind, i) => (
                <Badge key={i} tone="warning">{ind}</Badge>
              ))}
            </div>
          )}

          {accounts.length > 1 && (
            <div className="section">
              <StatementAccountGroups
                correspondentName={series?.correspondent_name ?? seriesName}
                documents={documents}
                accounts={accounts}
                accountColorMap={accountColorMap}
              />
            </div>
          )}

          {isDiscoverySample && (
            <div className="statement-series-review-note section" role="status">
              This candidate was discovered before full membership tracking was enabled. Run discovery again to review and confirm all {detectedDocumentCount} documents.
            </div>
          )}

          {/* Action bar with candidate review or series management actions */}
          <div className="statement-series-action-bar section">
            <span className="statement-series-action-label">{isCandidate ? 'Candidate review' : 'Actions'}</span>
            {isCandidate ? (
              <>
                <Button
                  disabled={busy || !membershipComplete || documents.length < 2}
                  onClick={() => setActiveFlow(activeFlow === 'split' ? 'none' : 'split')}
                >
                  Split candidate
                </Button>
                <Button
                  variant="success"
                  disabled={busy || !membershipComplete || documents.length === 0}
                  onClick={() => void handleConfirmCandidate()}
                >
                  {busy ? 'Confirming…' : 'Confirm and create series'}
                </Button>
              </>
            ) : (
              <>
                <Button
                  disabled={busy}
                  onClick={() => setActiveFlow(activeFlow === 'split' ? 'none' : 'split')}
                >
                  ✂️ Split Series
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => setActiveFlow(activeFlow === 'rename' ? 'none' : 'rename')}
                >
                  ✏️ Rename
                </Button>
                <Button variant="success" onClick={() => navigate('/statements')}>
                  ✓ Looks Correct
                </Button>
              </>
            )}
          </div>

          {/* Rename flow (inline) */}
          {activeFlow === 'rename' && (
            <Card title="✏️ Rename Series" className="section">
              <div className="statement-series-rename-form">
                <div className="form-group">
                  <label htmlFor="rename-name">Series name</label>
                  <input
                    id="rename-name"
                    type="text"
                    value={renameForm.name}
                    onChange={e => setRenameForm(prev => ({ ...prev, name: e.target.value }))}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="rename-account">Account identifier</label>
                  <input
                    id="rename-account"
                    type="text"
                    value={renameForm.account_identifier}
                    placeholder="e.g., ending 4321"
                    onChange={e => setRenameForm(prev => ({ ...prev, account_identifier: e.target.value }))}
                  />
                </div>
                <div className="btn-group">
                  <Button variant="primary" onClick={() => void handleRename()} disabled={busy || !renameForm.name}>
                    Save
                  </Button>
                  <Button onClick={() => setActiveFlow('none')}>Cancel</Button>
                </div>
              </div>
            </Card>
          )}

          {/* Split flow */}
          {activeFlow === 'split' && series && (
            <div className="section">
              <SplitSeriesFlow
                series={series}
                documents={documents}
                selectedDocIds={selectedDocs}
                onToggleDoc={toggleDocSelection}
                onSelectAllByAccount={selectAllByAccount}
                accounts={accounts}
                accountColorMap={accountColorMap}
                candidateMode={isCandidate}
                onComplete={() => void handleSplitComplete()}
                onCancel={() => { setActiveFlow('none'); setSelectedDocs(new Set()); }}
              />
            </div>
          )}

          {/* Merge flow */}
          {activeFlow === 'merge' && series && (
            <div className="section">
              <MergeSeriesFlow
                series={series}
                documents={documents}
                similarSeries={similarSeries}
                sourceTimeline={timeline}
                candidateMode={isCandidate}
                onComplete={() => void handleMergeComplete()}
                onCancel={() => setActiveFlow('none')}
              />
            </div>
          )}

          <div className="statement-series-layout">
            <div>
              {/* Document Timeline */}
              <Card title={isCandidate ? 'Candidate timeline' : 'Document timeline'} className="section">
                <SeriesTimeline
                  entries={timeline}
                  accounts={accounts}
                  accountColorMap={accountColorMap}
                  selectedDocIds={activeFlow === 'split' ? selectedDocs : undefined}
                  onDocClick={activeFlow === 'split' ? toggleDocSelection : undefined}
                />
              </Card>

              {/* Document list */}
              <Card title={documentListTitle} className="section">
                {documents.length === 0 ? (
                  <EmptyState
                    title={isCandidate ? 'No documents selected for this candidate' : 'No documents in this series'}
                    desc={isCandidate
                      ? 'Restore an excluded document before confirming this candidate.'
                      : 'Documents will appear here once assigned to this series.'}
                  />
                ) : (
                  <div className="statement-series-list">
                    {/* Select-all buttons per account during split */}
                    {accounts.length > 1 && activeFlow === 'split' && (
                      <div className="statement-series-doc-header">
                        {accounts.map(acct => (
                          <button key={acct} className="statement-series-select-all-btn" onClick={() => selectAllByAccount(acct)}>
                            Select all {acct}
                          </button>
                        ))}
                      </div>
                    )}

                    {documents.map(doc => {
                      const isSelected = selectedDocs.has(doc.document_id);
                      const acctColor = accountColorMap[doc.account_hint || ''] || 'var(--muted)';
                      return (
                        <div
                          key={doc.document_id}
                          className={`statement-series-list-item${isSelected ? ' selected' : ''}`}
                        >
                          {activeFlow === 'split' && (
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleDocSelection(doc.document_id)}
                              aria-label={`Select ${doc.title || doc.document_id}`}
                            />
                          )}
                          <span className="statement-series-doc-color" style={{ background: acctColor }} />
                          <div className="statement-series-document">
                            {/^\d+$/.test(doc.document_id) ? (
                              <DocumentPreview
                                documentId={Number(doc.document_id)}
                                variant="compact"
                              />
                            ) : (
                              <div className="statement-series-list-title">
                                {doc.title || `Document ${doc.document_id}`}
                              </div>
                            )}
                            <div className="statement-series-list-meta">
                              Paperless #{doc.document_id}
                              {doc.statement_date && ` · ${doc.statement_date}`}
                              {doc.period_label && ` · ${doc.period_label}`}
                            </div>
                          </div>
                          {doc.account_hint && (
                            <span
                              className="statement-series-account-badge"
                              style={{ background: `${acctColor}22`, color: acctColor }}
                            >
                              {doc.account_hint}
                            </span>
                          )}
                          {isCandidate && membershipComplete && (
                            <Button
                              size="sm"
                              variant="danger"
                              disabled={updatingDocumentId === doc.document_id}
                              onClick={() => void handleCandidateDocument(doc.document_id, true)}
                            >
                              {updatingDocumentId === doc.document_id ? 'Excluding…' : 'Exclude'}
                            </Button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>

              {isCandidate && excludedDocuments.length > 0 && (
                <Card title={`Excluded documents (${excludedDocuments.length})`} className="section">
                  <div className="statement-series-list">
                    {excludedDocuments.map(doc => (
                      <div key={doc.document_id} className="statement-series-list-item">
                        <div className="statement-series-document">
                          <div className="statement-series-list-title">
                            {doc.title || `Document ${doc.document_id}`}
                          </div>
                          <div className="statement-series-list-meta">
                            Paperless #{doc.document_id}
                            {doc.statement_date && ` · ${doc.statement_date}`}
                          </div>
                        </div>
                        <Button
                          size="sm"
                          disabled={updatingDocumentId === doc.document_id}
                          onClick={() => void handleCandidateDocument(doc.document_id, false)}
                        >
                          {updatingDocumentId === doc.document_id ? 'Restoring…' : 'Restore'}
                        </Button>
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </div>

            <div>
              {/* Provider override */}
              <Card title="Provider override" className="section">
                <div className="form-group">
                  <label htmlFor="override-status">Status</label>
                  <select id="override-status" value={overrideStatus} onChange={(event) => setOverrideStatus(event.target.value)}>
                    <option value="pending">Pending</option>
                    <option value="confirmed">Confirmed</option>
                    <option value="needs_review">Needs review</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </div>
                <div className="form-group">
                  <label htmlFor="display-name">Display name</label>
                  <input id="display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Friendly series label" />
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="frequency-override">Frequency override</label>
                    <select id="frequency-override" value={frequencyOverride} onChange={(event) => setFrequencyOverride(event.target.value)}>
                      <option value="">None</option>
                      <option value="monthly">Monthly</option>
                      <option value="quarterly">Quarterly</option>
                      <option value="annual">Annual</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label htmlFor="anchor-day">Anchor day</label>
                    <input id="anchor-day" type="number" min="1" max="31" value={anchorDay} onChange={(event) => setAnchorDay(event.target.value)} placeholder="e.g. 15" />
                  </div>
                </div>
                <div className="form-group">
                  <label htmlFor="override-notes">Notes</label>
                  <textarea id="override-notes" rows={5} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Add context for future recommendation runs." />
                </div>
                <div className="statement-series-override-meta">Last updated: {formatDateTime(seriesOverride?.updated_at)}</div>
                <div className="btn-group" style={{ marginTop: 12 }}>
                  <Button variant="primary" onClick={() => void handleSaveOverride()} disabled={saving || clearing || !canonicalSeriesKey}>
                    {saving ? 'Saving…' : 'Save override'}
                  </Button>
                  <Button onClick={() => void handleClearOverride()} disabled={saving || clearing || !seriesOverride}>
                    {clearing ? 'Clearing…' : 'Clear override'}
                  </Button>
                </div>
              </Card>

              {/* Similar series (merge candidates) */}
              <Card title={isCandidate ? 'Other candidates from this correspondent' : 'Other series from this correspondent'} className="section">
                {similarSeries.length === 0 ? (
                  <EmptyState
                    title={isCandidate ? 'No other candidates found' : 'No similar series found'}
                    desc={isCandidate
                      ? 'If discovery finds another candidate for this correspondent, it will appear here for comparison.'
                      : 'If multiple account variants exist for this correspondent, they will show up here.'}
                  />
                ) : (
                  <div className="statement-series-list">
                    {similarSeries.map(s => (
                      <div key={s.id} className="statement-series-list-item">
                        <div>
                          <div className="statement-series-list-title">{s.name}</div>
                          <div className="statement-series-list-meta">
                            {s.frequency} · {s.document_count} documents
                            {s.account_identifier && ` · ${s.account_identifier}`}
                            {s.last_seen && ` · Last: ${s.last_seen}`}
                          </div>
                        </div>
                        <div className="btn-group">
                          <Button
                            onClick={() => setActiveFlow('merge')}
                          >
                            Review merge
                          </Button>
                          <Link className="statement-series-link" to={`/statements/${encodeURIComponent(s.id)}`}>
                            Open →
                          </Link>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            </div>
          </div>
        </>
      ) : null}

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </>
  );
}
