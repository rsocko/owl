/**
 * MergeSeriesFlow — Merge the current series with another from the same correspondent.
 *
 * Side-by-side timelines, merged preview, guard warnings, confirm.
 */
import { useCallback, useEffect, useState } from 'react';
import { Button, Card } from '../ui';
import { endpoints } from '../../lib/api';
import type { SeriesInfo, SeriesDoc, TimelineEntry } from './StatementGroupingDetail';

interface Props {
  series: SeriesInfo;
  documents: SeriesDoc[];
  similarSeries: SeriesInfo[];
  onComplete: () => void;
  onCancel: () => void;
}

interface TargetDetail {
  series: SeriesInfo;
  documents: SeriesDoc[];
  timeline: TimelineEntry[];
}

export function MergeSeriesFlow({ series, documents, similarSeries, onComplete, onCancel }: Props) {
  const [targetId, setTargetId] = useState<string | null>(null);
  const [targetDetail, setTargetDetail] = useState<TargetDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTarget = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const data = await endpoints.statements.seriesDetail(id) as TargetDetail & { similar_series: SeriesInfo[]; anomaly_indicators: string[] };
      setTargetDetail({ series: data.series, documents: data.documents, timeline: data.timeline });
    } catch {
      setTargetDetail(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (targetId) void fetchTarget(targetId);
    else setTargetDetail(null);
  }, [targetId, fetchTarget]);

  // Guard checks
  const computeGuards = () => {
    if (!targetDetail) return { warnings: [], confidence: 0 };
    const warnings: string[] = [];
    let confidence = 80;

    // Check for duplicate periods
    const sourcePeriods = new Set(documents.map(d => d.period_label).filter(Boolean));
    const targetPeriods = new Set(targetDetail.documents.map(d => d.period_label).filter(Boolean));
    const overlap = [...sourcePeriods].filter(p => targetPeriods.has(p));
    if (overlap.length > 0) {
      warnings.push(`⚠️ Duplicate periods detected: ${overlap.join(', ')}. These may be separate accounts.`);
      confidence -= 30;
    }

    // Check for different account numbers
    const sourceAccounts = new Set(documents.map(d => d.account_hint).filter(Boolean));
    const targetAccounts = new Set(targetDetail.documents.map(d => d.account_hint).filter(Boolean));
    const allAccounts = new Set([...sourceAccounts, ...targetAccounts]);
    if (allAccounts.size > 1) {
      warnings.push(`⚠️ Different account numbers detected: ${[...allAccounts].join(', ')}`);
      confidence -= 20;
    }

    // Same correspondent boost
    if (series.correspondent_name === targetDetail.series.correspondent_name) {
      confidence += 10;
    }

    confidence = Math.max(0, Math.min(100, confidence));
    return { warnings, confidence };
  };

  const handleMerge = async () => {
    if (!targetId) return;
    setBusy(true);
    setError(null);
    try {
      await endpoints.statements.seriesMerge({
        source_series_id: series.id,
        target_series_id: targetId,
      });
      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Merge failed');
    } finally {
      setBusy(false);
    }
  };

  const guards = targetId ? computeGuards() : null;

  // Merged timeline preview: interleave by date
  const mergedTimeline = targetDetail
    ? [...documents, ...targetDetail.documents]
        .sort((a, b) => (a.statement_date || '').localeCompare(b.statement_date || ''))
    : [];

  return (
    <Card title="🔗 Merge Series">
      <div className="sg-merge-root">
        {error && <div className="sg-merge-error">{error}</div>}

        {/* Target series picker */}
        {!targetId ? (
          <div className="sg-merge-picker">
            <p>Select a series to merge with <strong>{series.name}</strong>:</p>
            <div className="sg-merge-options">
              {similarSeries.map(s => (
                <button key={s.id} className="sg-merge-option" onClick={() => setTargetId(s.id)}>
                  <div className="sg-merge-option-name">{s.name}</div>
                  <div className="sg-merge-option-meta">
                    {s.frequency} · {s.document_count} docs
                    {s.account_identifier && ` · ${s.account_identifier}`}
                  </div>
                </button>
              ))}
            </div>
            <Button onClick={onCancel}>Cancel</Button>
          </div>
        ) : loading ? (
          <div className="sg-loading">Loading target series…</div>
        ) : targetDetail ? (
          <div className="sg-merge-comparison">
            {/* Side-by-side */}
            <div className="sg-merge-sides">
              <div className="sg-merge-side">
                <div className="sg-merge-side-title" style={{ color: 'var(--series-a)' }}>
                  Source: {series.name}
                </div>
                <div className="sg-merge-side-meta">
                  {series.document_count} documents · {series.frequency}
                  {series.account_identifier && ` · ${series.account_identifier}`}
                </div>
                <div className="sg-merge-side-docs">
                  {documents.slice(0, 5).map(d => (
                    <div key={d.document_id} className="sg-merge-doc-item">
                      {d.title || `Doc ${d.document_id}`}
                      <span className="text-muted"> · {d.statement_date}</span>
                    </div>
                  ))}
                  {documents.length > 5 && (
                    <div className="text-muted">+ {documents.length - 5} more</div>
                  )}
                </div>
              </div>

              <div className="sg-merge-arrow">+</div>

              <div className="sg-merge-side">
                <div className="sg-merge-side-title" style={{ color: 'var(--series-b)' }}>
                  Target: {targetDetail.series.name}
                </div>
                <div className="sg-merge-side-meta">
                  {targetDetail.series.document_count} documents · {targetDetail.series.frequency}
                  {targetDetail.series.account_identifier && ` · ${targetDetail.series.account_identifier}`}
                </div>
                <div className="sg-merge-side-docs">
                  {targetDetail.documents.slice(0, 5).map(d => (
                    <div key={d.document_id} className="sg-merge-doc-item">
                      {d.title || `Doc ${d.document_id}`}
                      <span className="text-muted"> · {d.statement_date}</span>
                    </div>
                  ))}
                  {targetDetail.documents.length > 5 && (
                    <div className="text-muted">+ {targetDetail.documents.length - 5} more</div>
                  )}
                </div>
              </div>
            </div>

            {/* Merged preview */}
            <div className="sg-merge-preview">
              <div className="sg-merge-preview-title">
                Merged Result: {mergedTimeline.length} documents
              </div>
              <div className="sg-merge-preview-docs">
                {mergedTimeline.slice(0, 8).map(d => (
                  <div key={d.document_id} className="sg-merge-doc-item">
                    {d.title || `Doc ${d.document_id}`}
                    <span className="text-muted"> · {d.statement_date}</span>
                  </div>
                ))}
                {mergedTimeline.length > 8 && (
                  <div className="text-muted">+ {mergedTimeline.length - 8} more</div>
                )}
              </div>
            </div>

            {/* Guards */}
            {guards && (
              <div className="sg-merge-guards">
                <div className={`sg-merge-confidence ${guards.confidence >= 60 ? 'good' : 'poor'}`}>
                  Confidence: {guards.confidence}%
                </div>
                {guards.warnings.map((w, i) => (
                  <div key={i} className="sg-merge-warning">{w}</div>
                ))}
              </div>
            )}

            {/* Actions */}
            <div className="sg-merge-actions">
              <Button variant="success" onClick={() => void handleMerge()} disabled={busy}>
                🔗 Confirm Merge
              </Button>
              <Button onClick={() => setTargetId(null)} disabled={busy}>Back</Button>
              <Button onClick={onCancel} disabled={busy}>Cancel</Button>
            </div>
          </div>
        ) : (
          <div className="sg-error">Failed to load target series</div>
        )}
      </div>
    </Card>
  );
}
