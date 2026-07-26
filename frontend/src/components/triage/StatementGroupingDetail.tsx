/**
 * StatementGroupingDetail — Detail panel for grouping_anomaly triage items.
 *
 * Shows series header, timeline, document list, similar series,
 * and action bar with split/merge/rename/confirm operations.
 */
import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card } from '../ui';
import { endpoints } from '../../lib/api';
import { SplitSeriesFlow } from './SplitSeriesFlow';
import { MergeSeriesFlow } from './MergeSeriesFlow';
import './statement-grouping.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

export interface SeriesInfo {
  id: string;
  name: string;
  correspondent_id: number | null;
  correspondent_name: string;
  frequency: string;
  account_identifier: string | null;
  manually_curated: boolean;
  document_count: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface SeriesDoc {
  series_id: string;
  document_id: string;
  title: string | null;
  statement_date: string | null;
  period_label: string | null;
  account_hint: string | null;
}

export interface TimelineEntry {
  document_id: string;
  title: string | null;
  statement_date: string | null;
  period_label: string | null;
  account_hint: string | null;
  gap_before_days: number | null;
}

interface SeriesDetailResponse {
  series: SeriesInfo;
  documents: SeriesDoc[];
  timeline: TimelineEntry[];
  similar_series: SeriesInfo[];
  anomaly_indicators: string[];
}

interface Props {
  seriesId: string;
  triageItemId: string;
  reason: string | null;
  onResolved: (action: string) => void;
}

type ActiveFlow = 'none' | 'split' | 'merge' | 'rename';

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export function StatementGroupingDetail({ seriesId, triageItemId, reason, onResolved }: Props) {
  const [detail, setDetail] = useState<SeriesDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeFlow, setActiveFlow] = useState<ActiveFlow>('none');
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const [renameForm, setRenameForm] = useState({ name: '', account_identifier: '' });
  const [busy, setBusy] = useState(false);

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await endpoints.statements.seriesDetail(seriesId) as SeriesDetailResponse;
      setDetail(data);
      setRenameForm({
        name: data.series.name,
        account_identifier: data.series.account_identifier || '',
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load series detail');
    } finally {
      setLoading(false);
    }
  }, [seriesId]);

  useEffect(() => { void fetchDetail(); }, [fetchDetail]);

  // ---- Actions ----

  const handleConfirm = async () => {
    setBusy(true);
    try {
      await endpoints.triage.resolve(triageItemId, { action: 'confirm' });
      onResolved('confirm');
    } catch { /* toast handled by parent */ } finally {
      setBusy(false);
    }
  };

  const handleRename = async () => {
    if (!renameForm.name) return;
    setBusy(true);
    try {
      await endpoints.statements.seriesRename(seriesId, {
        name: renameForm.name || undefined,
        account_identifier: renameForm.account_identifier || undefined,
      });
      setActiveFlow('none');
      await fetchDetail();
      // Don't call onResolved — rename doesn't fix the grouping issue
    } catch { /* ignore */ } finally {
      setBusy(false);
    }
  };

  const handleSplitComplete = async () => {
    setActiveFlow('none');
    setSelectedDocs(new Set());
    await fetchDetail();
    onResolved('split');
  };

  const handleMergeComplete = async () => {
    setActiveFlow('none');
    // Don't refetch — the current series was deleted by the merge.
    // The parent will auto-advance to the next triage item.
    onResolved('merge');
  };

  const toggleDocSelection = (docId: string) => {
    setSelectedDocs(prev => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  };

  const selectAllByAccount = (account: string) => {
    if (!detail) return;
    const ids = detail.documents.filter(d => d.account_hint === account).map(d => d.document_id);
    setSelectedDocs(new Set(ids));
  };

  const handleReassign = async (documentId: string, targetSeriesId: string) => {
    setBusy(true);
    try {
      await endpoints.statements.seriesReassign(seriesId, {
        document_id: documentId,
        target_series_id: targetSeriesId,
      });
      await fetchDetail();
    } catch { /* ignore */ } finally {
      setBusy(false);
    }
  };

  // ---- Loading / Error ----

  if (loading) {
    return <div className="sg-loading">Loading series detail…</div>;
  }

  if (error || !detail) {
    return <div className="sg-error">{error || 'Series not found'}</div>;
  }

  const { series, documents, timeline, similar_series, anomaly_indicators } = detail;

  // Derive unique accounts
  const accounts = Array.from(new Set(documents.map(d => d.account_hint).filter(Boolean))) as string[];
  const COLORS = ['var(--series-a)', 'var(--series-b)', 'var(--series-c)', 'var(--series-d)'];
  const accountColorMap = Object.fromEntries(accounts.map((a, i) => [a, COLORS[i % COLORS.length]]));

  // ---- Render ----

  return (
    <div className="sg-root">
      {/* Reason banner */}
      {reason && (
        <div className="sg-reason-banner">
          <strong>🔍 Flagged:</strong> {reason}
        </div>
      )}

      {/* Series info grid */}
      <div className="sg-series-info">
        <div className="sg-info-item">
          <div className="sg-info-label">Correspondent</div>
          <div className="sg-info-value">{series.correspondent_name}</div>
        </div>
        <div className="sg-info-item">
          <div className="sg-info-label">Frequency</div>
          <div className="sg-info-value">{series.frequency}</div>
        </div>
        <div className="sg-info-item">
          <div className="sg-info-label">Documents</div>
          <div className="sg-info-value">
            {series.document_count}
            {series.first_seen && series.last_seen && (
              <span className="sg-info-sub"> ({series.first_seen} – {series.last_seen})</span>
            )}
          </div>
        </div>
        <div className="sg-info-item">
          <div className="sg-info-label">Account</div>
          <div className="sg-info-value">
            {series.account_identifier || (accounts.length > 1 ? '⚠️ Multiple detected' : accounts[0] || '—')}
          </div>
        </div>
      </div>

      {/* Action bar */}
      <div className="sg-action-bar">
        <span className="sg-action-label">Actions:</span>
        <button
          className="sg-btn sg-btn-split"
          disabled={busy}
          onClick={() => setActiveFlow(activeFlow === 'split' ? 'none' : 'split')}
        >
          ✂️ Split Series
        </button>
        <button
          className="sg-btn sg-btn-merge"
          disabled={busy || similar_series.length === 0}
          onClick={() => setActiveFlow(activeFlow === 'merge' ? 'none' : 'merge')}
        >
          🔗 Merge with Another
        </button>
        <button
          className="sg-btn sg-btn-rename"
          disabled={busy}
          onClick={() => setActiveFlow(activeFlow === 'rename' ? 'none' : 'rename')}
        >
          ✏️ Rename
        </button>
        <button
          className="sg-btn sg-btn-confirm"
          disabled={busy}
          onClick={() => void handleConfirm()}
        >
          ✓ Looks Correct
        </button>
      </div>

      {/* Anomaly indicators */}
      {anomaly_indicators.length > 0 && (
        <div className="sg-anomalies">
          {anomaly_indicators.map((ind, i) => (
            <Badge key={i} tone="warning">{ind}</Badge>
          ))}
        </div>
      )}

      {/* Rename flow (inline) */}
      {activeFlow === 'rename' && (
        <Card title="✏️ Rename Series">
          <div className="sg-rename-form">
            <label>
              Series name
              <input
                type="text"
                value={renameForm.name}
                onChange={e => setRenameForm(prev => ({ ...prev, name: e.target.value }))}
              />
            </label>
            <label>
              Account identifier
              <input
                type="text"
                value={renameForm.account_identifier}
                placeholder="e.g., ending 4321"
                onChange={e => setRenameForm(prev => ({ ...prev, account_identifier: e.target.value }))}
              />
            </label>
            <div className="sg-rename-actions">
              <Button variant="success" onClick={() => void handleRename()} disabled={busy || !renameForm.name}>
                Save
              </Button>
              <Button onClick={() => setActiveFlow('none')}>Cancel</Button>
            </div>
          </div>
        </Card>
      )}

      {/* Split flow */}
      {activeFlow === 'split' && (
        <SplitSeriesFlow
          series={series}
          documents={documents}
          selectedDocIds={selectedDocs}
          onToggleDoc={toggleDocSelection}
          onSelectAllByAccount={selectAllByAccount}
          accounts={accounts}
          accountColorMap={accountColorMap}
          onComplete={handleSplitComplete}
          onCancel={() => { setActiveFlow('none'); setSelectedDocs(new Set()); }}
        />
      )}

      {/* Merge flow */}
      {activeFlow === 'merge' && (
        <MergeSeriesFlow
          series={series}
          documents={documents}
          similarSeries={similar_series}
          onComplete={handleMergeComplete}
          onCancel={() => setActiveFlow('none')}
        />
      )}

      {/* Timeline visualization */}
      <Card title="📅 Document Timeline">
        <div className="sg-timeline-container">
          {/* Legend */}
          {accounts.length > 0 && (
            <div className="sg-timeline-legend">
              {accounts.map(acct => (
                <span key={acct} className="sg-legend-item">
                  <span className="sg-legend-dot" style={{ background: accountColorMap[acct] }} />
                  {acct}
                </span>
              ))}
              <span className="sg-legend-item">
                <span className="sg-legend-dot sg-legend-gap" />
                Large gap (&gt;60 days)
              </span>
            </div>
          )}

          {/* Timeline tracks — one row per account, or single row if no accounts */}
          {accounts.length > 1 ? (
            accounts.map(acct => {
              const acctDocs = timeline.filter(t => t.account_hint === acct);
              return (
                <div key={acct} className="sg-timeline-row">
                  <div className="sg-timeline-label">
                    <span className="sg-timeline-dot" style={{ background: accountColorMap[acct] }} />
                    {acct}
                  </div>
                  <div className="sg-timeline-track">
                    {acctDocs.map((entry, idx) => (
                      <div
                        key={entry.document_id}
                        className={`sg-timeline-doc${entry.gap_before_days && entry.gap_before_days > 60 ? ' gap' : ''}`}
                        style={{
                          left: `${(idx / Math.max(acctDocs.length - 1, 1)) * 90 + 2}%`,
                          background: entry.gap_before_days && entry.gap_before_days > 60 ? undefined : accountColorMap[acct],
                        }}
                        title={`${entry.title || ''}\n${entry.statement_date || ''}`}
                      >
                        {entry.period_label?.charAt(0) || entry.statement_date?.slice(5, 7) || '?'}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })
          ) : (
            <div className="sg-timeline-row">
              <div className="sg-timeline-label">All documents</div>
              <div className="sg-timeline-track">
                {timeline.map((entry, idx) => (
                  <div
                    key={entry.document_id}
                    className={`sg-timeline-doc${entry.gap_before_days && entry.gap_before_days > 60 ? ' gap' : ''}`}
                    style={{
                      left: `${(idx / Math.max(timeline.length - 1, 1)) * 90 + 2}%`,
                      background: entry.gap_before_days && entry.gap_before_days > 60
                        ? undefined
                        : accountColorMap[entry.account_hint || ''] || 'var(--series-a)',
                    }}
                    title={`${entry.title || ''}\n${entry.statement_date || ''}`}
                  >
                    {entry.period_label?.charAt(0) || entry.statement_date?.slice(5, 7) || '?'}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* Document list */}
      <Card title={`📋 Documents in Series (${documents.length})`}>
        <div className="sg-doc-list">
          {/* Select-all buttons per account */}
          {accounts.length > 1 && activeFlow === 'split' && (
            <div className="sg-doc-list-header">
              {accounts.map(acct => (
                <button key={acct} className="sg-select-all-btn" onClick={() => selectAllByAccount(acct)}>
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
                className={`sg-doc-item${isSelected ? ' selected' : ''}`}
              >
                {activeFlow === 'split' && (
                  <div
                    className={`sg-doc-check${isSelected ? ' checked' : ''}`}
                    onClick={() => toggleDocSelection(doc.document_id)}
                  />
                )}
                <div className="sg-doc-color" style={{ background: acctColor }} />
                <div className="sg-doc-info">
                  <div className="sg-doc-title">{doc.title || `Document ${doc.document_id}`}</div>
                  <div className="sg-doc-meta">
                    ID: {doc.document_id}
                    {doc.statement_date && ` · ${doc.statement_date}`}
                    {doc.period_label && ` · ${doc.period_label}`}
                  </div>
                </div>
                {doc.account_hint && (
                  <span className="sg-doc-account" style={{ background: `${acctColor}22`, color: acctColor }}>
                    {doc.account_hint}
                  </span>
                )}
                {similar_series.length > 0 && activeFlow === 'none' && (
                  <div className="sg-doc-actions">
                    <select
                      className="sg-reassign-select"
                      value=""
                      disabled={busy}
                      onChange={e => {
                        if (e.target.value) void handleReassign(doc.document_id, e.target.value);
                      }}
                    >
                      <option value="">↗️ Reassign…</option>
                      {similar_series.map(s => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Card>

      {/* Similar series (merge candidates) */}
      {similar_series.length > 0 && activeFlow !== 'merge' && (
        <Card title="🔗 Other Series from Same Correspondent">
          <div className="sg-similar-list">
            {similar_series.map(s => (
              <div key={s.id} className="sg-similar-item">
                <div className="sg-similar-info">
                  <div className="sg-similar-name">{s.name}</div>
                  <div className="sg-similar-meta">
                    {s.frequency} · {s.document_count} documents
                    {s.account_identifier && ` · ${s.account_identifier}`}
                    {s.last_seen && ` · Last: ${s.last_seen}`}
                  </div>
                </div>
                <button
                  className="sg-similar-action"
                  onClick={() => {
                    setActiveFlow('merge');
                  }}
                >
                  Merge into ↗
                </button>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
