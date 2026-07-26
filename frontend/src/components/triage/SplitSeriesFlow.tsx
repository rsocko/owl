/**
 * SplitSeriesFlow — Split documents from one series into a new series.
 *
 * Shows selected documents, name/account inputs, preview of both series
 * with side-by-side mini-timelines, and confirm.
 */
import { useMemo, useState } from 'react';
import { Button, Card } from '../ui';
import { endpoints } from '../../lib/api';
import { SeriesTimeline } from './SeriesTimeline';
import type { SeriesInfo, SeriesDoc, TimelineEntry } from './StatementGroupingDetail';

interface Props {
  series: SeriesInfo;
  documents: SeriesDoc[];
  selectedDocIds: Set<string>;
  onToggleDoc: (docId: string) => void;
  onSelectAllByAccount: (account: string) => void;
  accounts: string[];
  accountColorMap: Record<string, string>;
  onComplete: () => void;
  onCancel: () => void;
}

/** Convert SeriesDoc to TimelineEntry for the mini-timelines. */
function docsToTimeline(docs: SeriesDoc[]): TimelineEntry[] {
  const sorted = [...docs].sort((a, b) => (a.statement_date || '').localeCompare(b.statement_date || ''));
  let prevDate: Date | null = null;
  return sorted.map(d => {
    let gapDays: number | null = null;
    if (d.statement_date && prevDate) {
      try {
        const cur = new Date(`${d.statement_date}T00:00:00`);
        gapDays = Math.round((cur.getTime() - prevDate.getTime()) / 86400000);
      } catch { /* ignore */ }
    }
    if (d.statement_date) {
      try { prevDate = new Date(`${d.statement_date}T00:00:00`); } catch { /* ignore */ }
    }
    return {
      document_id: d.document_id,
      title: d.title,
      statement_date: d.statement_date,
      period_label: d.period_label,
      account_hint: d.account_hint,
      gap_before_days: gapDays,
    };
  });
}

export function SplitSeriesFlow({
  series,
  documents,
  selectedDocIds,
  accounts,
  accountColorMap,
  onComplete,
  onCancel,
}: Props) {
  const [newName, setNewName] = useState('');
  const [accountId, setAccountId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedDocs = documents.filter(d => selectedDocIds.has(d.document_id));
  const remainingDocs = documents.filter(d => !selectedDocIds.has(d.document_id));

  // Auto-suggest name from common account hint
  const selectedAccounts = new Set(selectedDocs.map(d => d.account_hint).filter(Boolean));

  // Mini-timeline data
  const remainingTimeline = useMemo(() => docsToTimeline(remainingDocs), [remainingDocs]);
  const selectedTimeline = useMemo(() => docsToTimeline(selectedDocs), [selectedDocs]);

  // Derive account subsets for the mini-timelines
  const remainingAccounts = useMemo(() =>
    Array.from(new Set(remainingDocs.map(d => d.account_hint).filter(Boolean))) as string[],
    [remainingDocs],
  );
  const selectedDocAccounts = useMemo(() =>
    Array.from(new Set(selectedDocs.map(d => d.account_hint).filter(Boolean))) as string[],
    [selectedDocs],
  );

  const handleSplit = async () => {
    if (selectedDocIds.size === 0) {
      setError('Select at least one document to split');
      return;
    }
    if (!newName.trim()) {
      setError('Please enter a name for the new series');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await endpoints.statements.seriesSplit(series.id, {
        document_ids: Array.from(selectedDocIds),
        new_series_name: newName.trim(),
        account_identifier: accountId.trim() || undefined,
      });
      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Split failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="✂️ Split Series">
      <div className="sg-split-root">
        {error && <div className="sg-split-error">{error}</div>}

        {selectedDocIds.size === 0 ? (
          <div className="sg-split-hint">
            <p>Select documents from the list below to move them to a new series.</p>
            {accounts.length > 1 && (
              <p className="text-muted">
                <strong>Tip:</strong> Multiple account numbers detected. Select all documents for one account using the "Select all" buttons.
              </p>
            )}
          </div>
        ) : (
          <>
            {/* Split preview with mini-timelines */}
            <div className="sg-split-preview">
              <div className="sg-split-title">
                ✂️ Split Preview — {selectedDocIds.size} document{selectedDocIds.size > 1 ? 's' : ''} selected for new series
              </div>

              {/* Side-by-side mini-timelines */}
              <div className="st-split-timelines">
                <div className="st-split-timeline-panel">
                  <div className="st-split-timeline-label">
                    <span className="sg-split-dot" style={{ background: 'var(--series-a)' }} />
                    Stays: "{series.name}" ({remainingDocs.length} docs)
                  </div>
                  <SeriesTimeline
                    entries={remainingTimeline}
                    accounts={remainingAccounts}
                    accountColorMap={accountColorMap}
                    compact
                    emptyLabel="No documents remain"
                  />
                </div>
                <div className="st-split-timelines-arrow">→</div>
                <div className="st-split-timeline-panel">
                  <div className="st-split-timeline-label">
                    <span className="sg-split-dot" style={{ background: 'var(--series-b)' }} />
                    New: "{newName || '(enter name)'}" ({selectedDocs.length} docs)
                  </div>
                  <SeriesTimeline
                    entries={selectedTimeline}
                    accounts={selectedDocAccounts}
                    accountColorMap={accountColorMap}
                    compact
                    emptyLabel="No documents selected"
                  />
                </div>
              </div>

              {/* Document lists */}
              <div className="sg-split-columns" style={{ marginTop: 12 }}>
                <div className="sg-split-col">
                  <ul className="sg-split-doc-list">
                    {remainingDocs.slice(0, 5).map(d => (
                      <li key={d.document_id}>
                        {d.title || `Doc ${d.document_id}`}
                        {d.account_hint && (
                          <span className="sg-doc-account" style={{
                            background: `${accountColorMap[d.account_hint] || 'var(--muted)'}22`,
                            color: accountColorMap[d.account_hint] || 'var(--muted)',
                            marginLeft: 4,
                          }}>
                            {d.account_hint}
                          </span>
                        )}
                      </li>
                    ))}
                    {remainingDocs.length > 5 && (
                      <li className="text-muted">+ {remainingDocs.length - 5} more</li>
                    )}
                  </ul>
                </div>
                <div className="sg-split-arrow" />
                <div className="sg-split-col">
                  <ul className="sg-split-doc-list">
                    {selectedDocs.slice(0, 5).map(d => (
                      <li key={d.document_id}>
                        {d.title || `Doc ${d.document_id}`}
                        {d.account_hint && (
                          <span className="sg-doc-account" style={{
                            background: `${accountColorMap[d.account_hint] || 'var(--muted)'}22`,
                            color: accountColorMap[d.account_hint] || 'var(--muted)',
                            marginLeft: 4,
                          }}>
                            {d.account_hint}
                          </span>
                        )}
                      </li>
                    ))}
                    {selectedDocs.length > 5 && (
                      <li className="text-muted">+ {selectedDocs.length - 5} more</li>
                    )}
                  </ul>
                  {/* Warn about different accounts in selection */}
                  {selectedAccounts.size > 1 && (
                    <div className="sg-split-warning">
                      ⚠️ Selected documents reference multiple accounts: {Array.from(selectedAccounts).join(', ')}
                    </div>
                  )}
                </div>
              </div>

              {/* Name + account form */}
              <div className="sg-split-form">
                <label>
                  New series name:
                  <input
                    type="text"
                    className="sg-split-input"
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    placeholder="e.g., Chase Freedom"
                  />
                </label>
                <label>
                  Account ID:
                  <input
                    type="text"
                    className="sg-split-input sg-split-input-short"
                    value={accountId}
                    onChange={e => setAccountId(e.target.value)}
                    placeholder="e.g., ending 8876"
                  />
                </label>
                <Button variant="success" onClick={() => void handleSplit()} disabled={busy || !newName.trim()}>
                  ✂️ Confirm Split
                </Button>
                <Button onClick={onCancel} disabled={busy}>Cancel</Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
