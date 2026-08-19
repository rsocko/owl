/**
 * SeriesTimeline — Reusable horizontal document timeline with monthly columns.
 *
 * Features:
 *  - Monthly grid columns derived from document date range
 *  - Multi-row when multiple accounts detected
 *  - Gap indicators (dashed red) for >60-day gaps
 *  - Hover tooltip with document metadata
 *  - Click to select/deselect documents
 *  - Compact mode for split/merge previews
 */
import { useMemo, useState } from 'react';
import type { TimelineEntry } from './StatementGroupingDetail';
import DocumentSummary, { documentSummaryLabel } from '../DocumentSummary';
import '../../styles/series-timeline.css';

interface Props {
  entries: TimelineEntry[];
  accounts: string[];
  accountColorMap: Record<string, string>;
  selectedDocIds?: Set<string>;
  onDocClick?: (docId: string) => void;
  compact?: boolean;
  /** Highlight period_labels that overlap with another series (merge view). */
  duplicatePeriods?: Set<string>;
  /** Label to show if no data */
  emptyLabel?: string;
}

// ── Helpers ──

function parseDate(dateStr: string | null | undefined): Date | null {
  if (!dateStr) return null;
  const d = new Date(`${dateStr}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function monthKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function monthLabel(key: string): string {
  const [y, m] = key.split('-');
  const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const mi = parseInt(m, 10) - 1;
  return `${names[mi] ?? '?'} ${y.slice(2)}`;
}

function buildMonthRange(entries: TimelineEntry[]): string[] {
  const dates = entries
    .map(e => parseDate(e.statement_date))
    .filter((d): d is Date => d !== null)
    .sort((a, b) => a.getTime() - b.getTime());

  if (dates.length === 0) return [];

  const start = new Date(dates[0].getFullYear(), dates[0].getMonth(), 1);
  const end = new Date(dates[dates.length - 1].getFullYear(), dates[dates.length - 1].getMonth(), 1);
  const result: string[] = [];
  const cursor = new Date(start);
  const MAX_MONTHS = 120; // 10 years safety cap
  while (cursor <= end && result.length < MAX_MONTHS) {
    result.push(monthKey(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }
  return result;
}

function docMonthKey(entry: TimelineEntry): string | null {
  const d = parseDate(entry.statement_date);
  return d ? monthKey(d) : null;
}

/** Position a doc marker as a percentage within the track based on its month index.
 *  When multiple docs share the same month, offset them so they don't stack. */
function docLeftPercent(mk: string, months: string[], offsetIndex: number, totalInMonth: number): number {
  const idx = months.indexOf(mk);
  if (idx < 0) return 0;
  if (months.length <= 1) return 5;
  const base = 2 + (idx / (months.length - 1)) * 88;
  if (totalInMonth <= 1) return base;
  // Spread docs within a ~4% band centered on the month position
  const spread = Math.min(4, 88 / months.length * 0.6);
  return base - spread / 2 + (offsetIndex / (totalInMonth - 1)) * spread;
}

// ── Component ──

export function SeriesTimeline({
  entries,
  accounts,
  accountColorMap,
  selectedDocIds,
  onDocClick,
  compact = false,
  duplicatePeriods,
  emptyLabel = 'No documents to display',
}: Props) {
  const [hoveredDoc, setHoveredDoc] = useState<string | null>(null);

  const months = useMemo(() => buildMonthRange(entries), [entries]);

  const hasMultiAccounts = accounts.length > 1;

  // Group entries by account (or single group)
  const rowGroups: { label: string; color: string; docs: TimelineEntry[] }[] = useMemo(() => {
    if (!entries.length) return [];
    if (!hasMultiAccounts) {
      return [{
        label: 'All documents',
        color: accountColorMap[accounts[0] ?? ''] || 'var(--series-a)',
        docs: entries,
      }];
    }
    return accounts.map(acct => ({
      label: acct,
      color: accountColorMap[acct] || 'var(--series-a)',
      docs: entries.filter(e => e.account_hint === acct),
    }));
  }, [entries, accounts, hasMultiAccounts, accountColorMap]);

  // Pre-compute per-month doc counts for offset calculation
  const monthDocCounts = useMemo(() => {
    const counts = new Map<string, { ids: string[] }>();
    for (const group of rowGroups) {
      for (const entry of group.docs) {
        const mk = docMonthKey(entry);
        if (!mk) continue;
        const key = `${group.label}:${mk}`;
        const existing = counts.get(key);
        if (existing) existing.ids.push(entry.document_id);
        else counts.set(key, { ids: [entry.document_id] });
      }
    }
    return counts;
  }, [rowGroups]);

  if (entries.length === 0) {
    return <div className="st-empty">{emptyLabel}</div>;
  }

  const renderDocMarker = (entry: TimelineEntry, color: string, groupLabel: string) => {
    const mk = docMonthKey(entry);
    if (!mk) return null;
    const monthKey = `${groupLabel}:${mk}`;
    const monthInfo = monthDocCounts.get(monthKey);
    const offsetIndex = monthInfo ? monthInfo.ids.indexOf(entry.document_id) : 0;
    const totalInMonth = monthInfo ? monthInfo.ids.length : 1;
    const left = docLeftPercent(mk, months, offsetIndex, totalInMonth);
    const isGap = entry.gap_before_days != null && entry.gap_before_days > 60;
    const isSelected = selectedDocIds?.has(entry.document_id);
    const isDuplicate = entry.period_label ? duplicatePeriods?.has(entry.period_label) : false;
    const isHovered = hoveredDoc === entry.document_id;
    const label = entry.period_label?.charAt(0) || entry.statement_date?.slice(5, 7) || '?';

    const classNames = [
      'st-doc',
      isGap ? 'st-gap' : '',
      isSelected ? 'st-selected' : '',
      isDuplicate ? 'st-duplicate' : '',
    ].filter(Boolean).join(' ');

    return (
      <div
        key={entry.document_id}
        className={classNames}
        style={{
          left: `${left}%`,
          background: isGap ? undefined : color,
        }}
        onClick={() => onDocClick?.(entry.document_id)}
        onMouseEnter={() => setHoveredDoc(entry.document_id)}
        onMouseLeave={() => setHoveredDoc(null)}
        aria-label={documentSummaryLabel(entry.document_summary)}
      >
        {label}
        {isHovered && !compact && (
          <div className="st-tooltip">
            <DocumentSummary summary={entry.document_summary} />
            <div className="st-tooltip-meta">
              {entry.period_label && <span> · {entry.period_label}</span>}
              {isGap && <span> · ⚠️ {entry.gap_before_days}d gap</span>}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className={`st-root${compact ? ' st-compact' : ''}`}>
      {/* Legend */}
      {!compact && (
        <div className="st-legend">
          {hasMultiAccounts ? (
            accounts.map(acct => (
              <span key={acct} className="st-legend-item">
                <span className="st-legend-dot" style={{ background: accountColorMap[acct] }} />
                {acct}
              </span>
            ))
          ) : accounts[0] ? (
            <span className="st-legend-item">
              <span className="st-legend-dot" style={{ background: accountColorMap[accounts[0]] }} />
              {accounts[0]}
            </span>
          ) : null}
          <span className="st-legend-item">
            <span className="st-legend-dot st-legend-gap" />
            Large gap (&gt;60 days)
          </span>
        </div>
      )}

      {/* Rows */}
      {rowGroups.map(group => (
        <div key={group.label} className="st-row">
          <div className="st-row-label">
            <span className="st-row-dot" style={{ background: group.color }} />
            {group.label}
          </div>
          <div className="st-track">
            {group.docs.map(entry => renderDocMarker(entry, group.color, group.label))}
          </div>
        </div>
      ))}

      {/* Month axis */}
      {months.length > 0 && (
        <div className="st-month-axis">
          {months.map(m => (
            <span key={m} className="st-month-label">{monthLabel(m)}</span>
          ))}
        </div>
      )}
    </div>
  );
}
