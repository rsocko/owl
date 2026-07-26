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
  while (cursor <= end) {
    result.push(monthKey(cursor));
    cursor.setMonth(cursor.getMonth() + 1);
  }
  return result;
}

function docMonthKey(entry: TimelineEntry): string | null {
  const d = parseDate(entry.statement_date);
  return d ? monthKey(d) : null;
}

/** Position a doc marker as a percentage within the track based on its month index. */
function docLeftPercent(mk: string, months: string[]): number {
  const idx = months.indexOf(mk);
  if (idx < 0) return 0;
  if (months.length <= 1) return 5;
  return 2 + (idx / (months.length - 1)) * 88;
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

  if (entries.length === 0) {
    return <div className="st-empty">{emptyLabel}</div>;
  }

  const hasMultiAccounts = accounts.length > 1;

  // Group entries by account (or single group)
  const rowGroups: { label: string; color: string; docs: TimelineEntry[] }[] = useMemo(() => {
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

  const renderDocMarker = (entry: TimelineEntry, color: string) => {
    const mk = docMonthKey(entry);
    if (!mk) return null;
    const left = docLeftPercent(mk, months);
    const isGap = entry.gap_before_days != null && entry.gap_before_days > 60;
    const isSelected = selectedDocIds?.has(entry.document_id);
    const isDuplicate = duplicatePeriods?.has(entry.period_label || '');
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
      >
        {label}
        {isHovered && !compact && (
          <div className="st-tooltip">
            <div className="st-tooltip-title">{entry.title || `Document ${entry.document_id}`}</div>
            <div className="st-tooltip-meta">
              {entry.statement_date && <span>{entry.statement_date}</span>}
              {entry.period_label && <span> · {entry.period_label}</span>}
              {entry.account_hint && <span> · {entry.account_hint}</span>}
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
            {group.docs.map(entry => renderDocMarker(entry, group.color))}
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
