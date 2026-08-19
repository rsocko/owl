/**
 * MergeSeriesFlow — Merge the current series with another from the same correspondent.
 *
 * Side-by-side timelines, merged preview with interleaved timeline,
 * guard warnings with duplicate period highlighting, and prominent confidence score.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card } from '../ui';
import { endpoints } from '../../lib/api';
import DocumentSummary from '../DocumentSummary';
import { SeriesTimeline } from './SeriesTimeline';
import type { SeriesInfo, SeriesDoc, TimelineEntry } from './StatementGroupingDetail';

interface Props {
  series: SeriesInfo;
  documents: SeriesDoc[];
  similarSeries: SeriesInfo[];
  /** Source series timeline entries from parent detail. */
  sourceTimeline?: TimelineEntry[];
  onComplete: () => void;
  onCancel: () => void;
}

interface TargetDetail {
  series: SeriesInfo;
  documents: SeriesDoc[];
  timeline: TimelineEntry[];
}

export function MergeSeriesFlow({ series, documents, similarSeries, sourceTimeline, onComplete, onCancel }: Props) {
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

  // Build source timeline from docs if parent didn't pass it
  const effectiveSourceTimeline: TimelineEntry[] = useMemo(() => {
    if (sourceTimeline && sourceTimeline.length > 0) return sourceTimeline;
    // Fallback: construct from documents
    const sorted = [...documents].sort((a, b) => (a.statement_date || '').localeCompare(b.statement_date || ''));
    return sorted.map(d => ({
      document_id: d.document_id,
      title: d.title,
      statement_date: d.statement_date,
      period_label: d.period_label,
      account_hint: d.account_hint,
      gap_before_days: null,
      document_summary: d.document_summary,
    }));
  }, [sourceTimeline, documents]);

  // Account data for source timeline
  const sourceAccounts = useMemo(() =>
    Array.from(new Set(documents.map(d => d.account_hint).filter(Boolean))) as string[],
    [documents],
  );
  const sourceColorMap = useMemo(() => {
    const COLORS = ['var(--series-a)', 'var(--series-b)', 'var(--series-c)', 'var(--series-d)'];
    return Object.fromEntries(sourceAccounts.map((a, i) => [a, COLORS[i % COLORS.length]]));
  }, [sourceAccounts]);

  // Account data for target timeline
  const targetAccounts = useMemo(() => {
    if (!targetDetail) return [];
    return Array.from(new Set(targetDetail.documents.map(d => d.account_hint).filter(Boolean))) as string[];
  }, [targetDetail]);
  const targetColorMap = useMemo(() => {
    const COLORS = ['var(--series-b)', 'var(--series-a)', 'var(--series-c)', 'var(--series-d)'];
    return Object.fromEntries(targetAccounts.map((a, i) => [a, COLORS[i % COLORS.length]]));
  }, [targetAccounts]);

  // Guard checks
  const computeGuards = () => {
    if (!targetDetail) return { warnings: [], confidence: 0, duplicatePeriods: new Set<string>() };
    const warnings: string[] = [];
    let confidence = 80;

    // Check for duplicate periods
    const sourcePeriods = new Set(documents.map(d => d.period_label).filter(Boolean));
    const targetPeriods = new Set(targetDetail.documents.map(d => d.period_label).filter(Boolean));
    const overlap = [...sourcePeriods].filter(p => targetPeriods.has(p));
    const duplicatePeriods = new Set(overlap as string[]);
    if (overlap.length > 0) {
      warnings.push(`⚠️ Duplicate periods detected: ${overlap.join(', ')}. These may be separate accounts.`);
      confidence -= 30;
    }

    // Check for different account numbers
    const srcAccounts = new Set(documents.map(d => d.account_hint).filter(Boolean));
    const tgtAccounts = new Set(targetDetail.documents.map(d => d.account_hint).filter(Boolean));
    const allAccounts = new Set([...srcAccounts, ...tgtAccounts]);
    if (allAccounts.size > 1) {
      warnings.push(`⚠️ Different account numbers detected: ${[...allAccounts].join(', ')}`);
      confidence -= 20;
    }

    // Same correspondent boost
    if (series.correspondent_name === targetDetail.series.correspondent_name) {
      confidence += 10;
    }

    confidence = Math.max(0, Math.min(100, confidence));
    return { warnings, confidence, duplicatePeriods };
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
  const mergedTimeline: TimelineEntry[] = useMemo(() => {
    if (!targetDetail) return [];
    const allDocs = [...effectiveSourceTimeline, ...targetDetail.timeline];
    return allDocs.sort((a, b) => (a.statement_date || '').localeCompare(b.statement_date || ''));
  }, [effectiveSourceTimeline, targetDetail]);

  // Combined accounts for merged timeline
  const mergedAccounts = useMemo(() =>
    Array.from(new Set([...sourceAccounts, ...targetAccounts])),
    [sourceAccounts, targetAccounts],
  );
  const mergedColorMap = useMemo(() => {
    const COLORS = ['var(--series-a)', 'var(--series-b)', 'var(--series-c)', 'var(--series-d)'];
    return Object.fromEntries(mergedAccounts.map((a, i) => [a, COLORS[i % COLORS.length]]));
  }, [mergedAccounts]);

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
            {/* Confidence score — prominent */}
            {guards && (
              <div className={`sg-merge-confidence ${guards.confidence >= 60 ? 'good' : 'poor'}`}>
                🎯 Merge Confidence: {guards.confidence}%
                <span style={{ fontWeight: 400, fontSize: 12, marginLeft: 8 }}>
                  {guards.confidence >= 80 ? '— Likely the same account'
                    : guards.confidence >= 60 ? '— Possible match, review recommended'
                    : '— Low confidence, check warnings below'}
                </span>
              </div>
            )}

            {/* Side-by-side timelines */}
            <div className="st-merge-timelines">
              <div className="st-merge-timeline-panel">
                <div className="st-merge-timeline-label" style={{ color: 'var(--series-a)' }}>
                  Source: {series.name} ({documents.length} docs)
                </div>
                <SeriesTimeline
                  entries={effectiveSourceTimeline}
                  accounts={sourceAccounts}
                  accountColorMap={sourceColorMap}
                  compact
                  duplicatePeriods={guards?.duplicatePeriods}
                />
              </div>

              <div className="st-merge-timeline-panel">
                <div className="st-merge-timeline-label" style={{ color: 'var(--series-b)' }}>
                  Target: {targetDetail.series.name} ({targetDetail.documents.length} docs)
                </div>
                <SeriesTimeline
                  entries={targetDetail.timeline}
                  accounts={targetAccounts}
                  accountColorMap={targetColorMap}
                  compact
                  duplicatePeriods={guards?.duplicatePeriods}
                />
              </div>
            </div>

            {/* Merged timeline preview */}
            <div className="st-merge-merged-preview">
              <div className="st-merge-merged-label">
                Merged Result: {mergedTimeline.length} documents
              </div>
              <SeriesTimeline
                entries={mergedTimeline}
                accounts={mergedAccounts}
                accountColorMap={mergedColorMap}
                compact
                duplicatePeriods={guards?.duplicatePeriods}
              />
            </div>

            {/* Guard warnings */}
            {guards && guards.warnings.length > 0 && (
              <div className="sg-merge-guards">
                {guards.warnings.map((w, i) => (
                  <div key={i} className="sg-merge-warning">{w}</div>
                ))}
              </div>
            )}

            {/* Side-by-side doc lists */}
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
                      <DocumentSummary summary={d.document_summary} />
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
                      <DocumentSummary summary={d.document_summary} />
                    </div>
                  ))}
                  {targetDetail.documents.length > 5 && (
                    <div className="text-muted">+ {targetDetail.documents.length - 5} more</div>
                  )}
                </div>
              </div>
            </div>

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
