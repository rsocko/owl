/**
 * ManualMatchModal — modal overlay for manually linking an EOB to a Bill.
 *
 * Split layout: source document context (left) + candidate search & results (right).
 * Triggered from the Re-link button in EobMatchDetail or triage queue actions.
 */

import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { Badge, Button, EmptyState, SkeletonLoader, Toast } from './ui';
import DocumentPreview from './DocumentPreview';
import { endpoints, type DocumentSummaryModel } from '../lib/api';
import DocumentSummary from './DocumentSummary';
import { getToastDuration } from '../lib/toast';
import '../styles/manual-match-modal.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface MatchBreakdown {
  date?: number | null;
  provider?: number | null;
  patient?: number | null;
  amount?: number | null;
  procedures?: number | null;
}

interface MatchRecord {
  id: number;
  eob_document_id?: number | null;
  bill_document_id?: number | null;
  score?: number | null;
  confidence?: string | null;
  breakdown?: MatchBreakdown | null;
  status?: string | null;
  linked_in_paperless?: boolean;
  eob_preview_url?: string | null;
  bill_preview_url?: string | null;
  created_at?: string | null;
  eob_summary: DocumentSummaryModel;
  bill_summary: DocumentSummaryModel;
}

interface MatchesResponse {
  matches?: MatchRecord[];
}

interface ManualMatchResponse {
  status: string;
  match: MatchRecord;
}

type ToastState = { message: string; tone?: 'success' | 'error' } | null;

interface SearchFilters {
  query: string;
  claimReference: string;
  amountSignal: string;
  createdAfter: string;
  docType: '' | 'eob' | 'bill';
  provider: string;
}

// ------------------------------------------------------------------
// Props
// ------------------------------------------------------------------

export interface ManualMatchModalProps {
  /** Whether the modal is visible. */
  open: boolean;
  /** Close callback. */
  onClose: () => void;
  /** Source document ID (the EOB or Bill doc being re-linked). */
  sourceDocId?: number | null;
  /** Current match ID being replaced (for display context). */
  matchId?: number | null;
  /** Triage queue item ID — passed through to the manual match endpoint. */
  triageItemId?: string;
  /** Called after a manual match is successfully created. */
  onMatchCreated?: (match: MatchRecord) => void;
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function valueToPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function scoreTone(score?: number | null): 'success' | 'warning' | 'danger' {
  const safeScore = valueToPercent(score);
  if (safeScore >= 85) return 'success';
  if (safeScore >= 70) return 'warning';
  return 'danger';
}

function buildSearchText(match: MatchRecord) {
  return [
    match.id,
    match.eob_document_id,
    match.bill_document_id,
    match.confidence,
    match.status,
    match.created_at,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export default function ManualMatchModal({
  open,
  onClose,
  sourceDocId,
  matchId,
  triageItemId,
  onMatchCreated,
}: ManualMatchModalProps) {
  const [allMatches, setAllMatches] = useState<MatchRecord[]>([]);
  const [results, setResults] = useState<MatchRecord[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [linkingId, setLinkingId] = useState<number | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [filters, setFilters] = useState<SearchFilters>({
    query: sourceDocId ? String(sourceDocId) : '',
    claimReference: sourceDocId ? String(sourceDocId) : '',
    amountSignal: '',
    createdAfter: '',
    docType: '',
    provider: '',
  });

  // ---- Load candidate matches when modal opens ----

  useEffect(() => {
    if (!open) return;

    // Reset state on each open
    setSelectedId(null);
    setError(null);
    setLinkingId(null);
    setToast(null);
    setExpandedPreviews(new Set());
    setFilters({
      query: sourceDocId ? String(sourceDocId) : '',
      claimReference: sourceDocId ? String(sourceDocId) : '',
      amountSignal: '',
      createdAfter: '',
      docType: '',
      provider: '',
    });

    const loadMatches = async () => {
      setLoading(true);
      setError(null);
      try {
        const response = (await endpoints.eob.matches('status=candidate&limit=200')) as MatchesResponse;
        const matches = response?.matches ?? [];
        setAllMatches(matches);

        // Initial filter: if we have a source doc ID, prefer matches involving it
        const docFiltered = sourceDocId
          ? matches.filter(
              (m) =>
                m.eob_document_id === sourceDocId || m.bill_document_id === sourceDocId,
            )
          : matches;

        const initialResults = docFiltered.length > 0 ? docFiltered : matches;
        // Sort by score descending so best match appears first (spread to avoid mutating state)
        const sorted = [...initialResults].sort((a, b) => (valueToPercent(b.score) - valueToPercent(a.score)));
        setResults(sorted);
        setSelectedId(sorted[0]?.id ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load candidate matches.');
      } finally {
        setLoading(false);
      }
    };

    void loadMatches();
  }, [open, sourceDocId]);

  // ---- Toast auto-dismiss ----

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  // ---- Escape key + body scroll lock ----

  useEffect(() => {
    if (!open) return undefined;

    // Prevent background scroll while modal is open
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, onClose]);

  // ---- Preview toggle state ----
  const [expandedPreviews, setExpandedPreviews] = useState<Set<number>>(new Set());

  const togglePreview = useCallback((id: number) => {
    setExpandedPreviews((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // ---- Client-side filtering ----

  const applyFilters = useCallback(
    (nextFilters: SearchFilters) => {
      const minAmountSignal = nextFilters.amountSignal ? Number(nextFilters.amountSignal) : null;
      const createdAfterTime = nextFilters.createdAfter ? new Date(nextFilters.createdAfter).getTime() : null;

      const filtered = allMatches.filter((match) => {
        const searchText = buildSearchText(match);
        const queryNeedle = nextFilters.query.trim().toLowerCase();
        const claimNeedle = nextFilters.claimReference.trim().toLowerCase();
        const providerNeedle = nextFilters.provider.trim().toLowerCase();

        if (queryNeedle && !searchText.includes(queryNeedle)) return false;
        if (
          claimNeedle &&
          !String(match.eob_document_id ?? '').includes(claimNeedle) &&
          !String(match.bill_document_id ?? '').includes(claimNeedle) &&
          !String(match.id).includes(claimNeedle)
        ) {
          return false;
        }
        if (providerNeedle && !searchText.includes(providerNeedle)) return false;
        if (nextFilters.docType === 'eob' && match.eob_document_id == null) return false;
        if (nextFilters.docType === 'bill' && match.bill_document_id == null) return false;
        if (typeof minAmountSignal === 'number' && !Number.isNaN(minAmountSignal) && valueToPercent(match.breakdown?.amount) < minAmountSignal) {
          return false;
        }
        if (typeof createdAfterTime === 'number' && !Number.isNaN(createdAfterTime)) {
          const createdTime = match.created_at ? new Date(match.created_at).getTime() : Number.NaN;
          if (Number.isNaN(createdTime) || createdTime < createdAfterTime) return false;
        }
        return true;
      });

      // Sort by score descending so best match appears first
      filtered.sort((a, b) => (valueToPercent(b.score) - valueToPercent(a.score)));

      setResults(filtered);
      if (!filtered.some((match) => match.id === selectedId)) {
        setSelectedId(filtered[0]?.id ?? null);
      }
    },
    [allMatches, selectedId],
  );

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    applyFilters(filters);
  };

  const handleReset = () => {
    const cleared: SearchFilters = {
      query: sourceDocId ? String(sourceDocId) : '',
      claimReference: sourceDocId ? String(sourceDocId) : '',
      amountSignal: '',
      createdAfter: '',
      docType: '',
      provider: '',
    };
    setFilters(cleared);
    applyFilters(cleared);
  };

  const selectedMatch = useMemo(
    () => results.find((m) => m.id === selectedId) ?? allMatches.find((m) => m.id === selectedId) ?? null,
    [allMatches, results, selectedId],
  );

  // ---- Link match ----

  const linkMatch = async (match: MatchRecord) => {
    if (!sourceDocId) {
      setToast({ message: 'No source document ID — cannot create manual link.', tone: 'error' });
      return;
    }

    // Determine which side is the EOB and which is the Bill
    const eobDocId = match.eob_document_id ?? sourceDocId;
    const billDocId = match.bill_document_id ?? sourceDocId;

    setLinkingId(match.id);
    try {
      const response = (await endpoints.eob.manualMatch({
        eob_doc_id: eobDocId,
        bill_doc_id: billDocId,
        notes: `Manual match via modal — linked from candidate #${match.id}`,
        ...(triageItemId ? { triage_item_id: triageItemId } : {}),
      })) as ManualMatchResponse;
      setToast({ message: `Manual match created (#${response.match?.id ?? match.id}).` });
      onMatchCreated?.(response.match ?? match);
      // Parent's onMatchCreated is expected to close the modal; onClose is a fallback
      if (!onMatchCreated) onClose();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to create manual match.', tone: 'error' });
    } finally {
      setLinkingId(null);
    }
  };

  if (!open) return null;

  return (
    <>
      <div className="mm-overlay" onClick={onClose}>
        <div className="mm-dialog" onClick={(e) => e.stopPropagation()}>

          {/* Header */}
          <div className="mm-header">
            <div className="mm-title">
              🔍 Find Matching Document
              {sourceDocId ? ` — Doc #${sourceDocId}` : ''}
            </div>
            <button className="mm-close" onClick={onClose} aria-label="Close">✕</button>
          </div>

          {/* Body */}
          <div className="mm-body">

            {/* Left: Source context */}
            <div className="mm-context">
              <span className="mm-ctx-badge">📋 Source document</span>
              <div>
                <div className="mm-ctx-label">Document ID</div>
                <div className="mm-ctx-value">{sourceDocId ?? 'Not provided'}</div>
              </div>
              <div>
                <div className="mm-ctx-label">Current match</div>
                <div className="mm-ctx-value">{matchId ? `#${matchId}` : 'None'}</div>
              </div>
              <div>
                <div className="mm-ctx-label">Candidate pool</div>
                <div className="mm-ctx-value">{allMatches.length} matches</div>
              </div>

              {/* Selected candidate summary */}
              {selectedMatch && (
                <div className="mm-ctx-divider">
                  <div className="mm-ctx-label">Selected candidate</div>
                  <div className="mm-ctx-value">
                    <DocumentSummary summary={selectedMatch.eob_summary} />
                    <DocumentSummary summary={selectedMatch.bill_summary} />
                  </div>
                  <Badge tone={scoreTone(selectedMatch.score)}>{valueToPercent(selectedMatch.score)}% match</Badge>
                  <div style={{ marginTop: 8, display: 'grid', gap: 4 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Date</span>
                      <strong>{valueToPercent(selectedMatch.breakdown?.date)}%</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Provider</span>
                      <strong>{valueToPercent(selectedMatch.breakdown?.provider)}%</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Amount</span>
                      <strong>{valueToPercent(selectedMatch.breakdown?.amount)}%</strong>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Right: Search & results */}
            <div className="mm-search">
              {loading ? (
                <SkeletonLoader variant="table" rows={4} />
              ) : error ? (
                <div style={{ padding: 16 }}>
                  <p style={{ color: 'var(--danger)' }}>{error}</p>
                  <Button onClick={() => window.location.reload()}>Retry</Button>
                </div>
              ) : (
                <>
                  {/* Search form */}
                  <form onSubmit={handleSubmit}>
                    <div className="mm-search-bar">
                      <input
                        className="mm-search-input"
                        value={filters.query}
                        onChange={(e) => setFilters((f) => ({ ...f, query: e.target.value }))}
                        placeholder="Search by document ID, match ID, status…"
                      />
                      <Button type="submit" variant="primary">🔍 Search</Button>
                    </div>
                    <div className="mm-filter-row" style={{ marginTop: 8 }}>
                      <div className="form-group">
                        <label htmlFor="mm-doc-type">Document type</label>
                        <select
                          id="mm-doc-type"
                          value={filters.docType}
                          onChange={(e) => setFilters((f) => ({ ...f, docType: e.target.value as SearchFilters['docType'] }))}
                        >
                          <option value="">All types</option>
                          <option value="eob">EOB only</option>
                          <option value="bill">Bill only</option>
                        </select>
                      </div>
                      <div className="form-group">
                        <label htmlFor="mm-provider">Provider / correspondent</label>
                        <input
                          id="mm-provider"
                          value={filters.provider}
                          onChange={(e) => setFilters((f) => ({ ...f, provider: e.target.value }))}
                          placeholder="Filter by provider name"
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor="mm-claim-ref">Claim / document ref</label>
                        <input
                          id="mm-claim-ref"
                          value={filters.claimReference}
                          onChange={(e) => setFilters((f) => ({ ...f, claimReference: e.target.value }))}
                          placeholder="EOB/Bill ID"
                        />
                      </div>
                    </div>
                    <div className="mm-filter-row" style={{ marginTop: 8 }}>
                      <div className="form-group">
                        <label htmlFor="mm-amount">Min amount signal (%)</label>
                        <input
                          id="mm-amount"
                          type="number"
                          min="0"
                          max="100"
                          value={filters.amountSignal}
                          onChange={(e) => setFilters((f) => ({ ...f, amountSignal: e.target.value }))}
                          placeholder="e.g. 60"
                        />
                      </div>
                      <div className="form-group">
                        <label htmlFor="mm-created">Created after</label>
                        <input
                          id="mm-created"
                          type="date"
                          value={filters.createdAfter}
                          onChange={(e) => setFilters((f) => ({ ...f, createdAfter: e.target.value }))}
                        />
                      </div>
                    </div>
                    <div className="btn-group" style={{ marginTop: 8 }}>
                      <Button type="button" onClick={handleReset}>Reset</Button>
                    </div>
                  </form>

                  {/* Results */}
                  <div className="mm-results-header">
                    {results.length} potential match{results.length === 1 ? '' : 'es'} found
                  </div>

                  {results.length === 0 ? (
                    <EmptyState
                      title="No candidate matches found"
                      desc="Try widening the filters or clearing the search text."
                    />
                  ) : (
                    <div className="mm-results-list">
                      {results.map((match, index) => {
                        const isBestMatch = index === 0 && results.length > 1;
                        const isPreviewOpen = expandedPreviews.has(match.id);
                        return (
                        <article
                          key={match.id}
                          className={[
                            'mm-result-card',
                            selectedId === match.id ? 'selected' : '',
                            isBestMatch ? 'best-match' : '',
                          ].filter(Boolean).join(' ')}
                          onClick={() => setSelectedId(match.id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              setSelectedId(match.id);
                            }
                          }}
                          role="button"
                          tabIndex={0}
                          aria-pressed={selectedId === match.id}
                        >
                          <div className="mm-rc-top">
                            <span className="mm-rc-title">
                              <DocumentSummary summary={match.eob_summary} />
                              <DocumentSummary summary={match.bill_summary} />
                            </span>
                            {isBestMatch && <Badge tone="success">⭐ Best match</Badge>}
                            <Badge tone={scoreTone(match.score)}>
                              {valueToPercent(match.score)}% match
                            </Badge>
                          </div>
                          <div className="mm-rc-fields">
                            <div className="mm-rc-field">
                              <div className="mm-rc-field-label">Date</div>
                              <div className="mm-rc-field-value">{valueToPercent(match.breakdown?.date)}%</div>
                            </div>
                            <div className="mm-rc-field">
                              <div className="mm-rc-field-label">Provider</div>
                              <div className="mm-rc-field-value">{valueToPercent(match.breakdown?.provider)}%</div>
                            </div>
                            <div className="mm-rc-field">
                              <div className="mm-rc-field-label">Patient</div>
                              <div className="mm-rc-field-value">{valueToPercent(match.breakdown?.patient)}%</div>
                            </div>
                            <div className="mm-rc-field">
                              <div className="mm-rc-field-label">Amount</div>
                              <div className="mm-rc-field-value">{valueToPercent(match.breakdown?.amount)}%</div>
                            </div>
                            <div className="mm-rc-field">
                              <div className="mm-rc-field-label">Procedures</div>
                              <div className="mm-rc-field-value">{valueToPercent(match.breakdown?.procedures)}%</div>
                            </div>
                            <div className="mm-rc-field">
                              <div className="mm-rc-field-label">Status</div>
                              <div className="mm-rc-field-value">{match.status ?? 'candidate'}</div>
                            </div>
                          </div>
                          <div className="mm-rc-footer">
                            <span>Match #{match.id} · {formatDateTime(match.created_at)}</span>
                            <button
                              className="mm-rc-preview-toggle"
                              onClick={(e) => { e.stopPropagation(); togglePreview(match.id); }}
                              aria-expanded={isPreviewOpen}
                            >
                              {isPreviewOpen ? '▼ Hide preview' : '▶ Preview doc'}
                            </button>
                            <Button
                              variant="success"
                              onClick={() => {
                                void linkMatch(match);
                              }}
                              disabled={linkingId !== null}
                            >
                              {linkingId === match.id ? 'Linking…' : 'Link this match'}
                            </Button>
                          </div>

                          {/* Inline document preview (toggle) */}
                          {isPreviewOpen && (
                            <div className="mm-rc-preview-panel">
                              {match.eob_document_id && (
                                <DocumentPreview
                                  documentId={match.eob_document_id}
                                  paperlessUrl={match.eob_preview_url}
                                  variant="compact"
                                  label="EOB"
                                />
                              )}
                              {match.bill_document_id && (
                                <DocumentPreview
                                  documentId={match.bill_document_id}
                                  paperlessUrl={match.bill_preview_url}
                                  variant="compact"
                                  label="Bill"
                                />
                              )}
                              {!match.eob_document_id && !match.bill_document_id && (
                                <div className="mm-rc-preview-empty">No documents available for preview.</div>
                              )}
                            </div>
                          )}
                        </article>
                        );
                      })}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="mm-footer">
            <div className="mm-footer-info">
              {selectedMatch
                ? `Selected: Match #${selectedMatch.id} (${valueToPercent(selectedMatch.score)}%)`
                : 'Select a candidate to link'}
            </div>
            <div className="btn-group">
              <Button onClick={onClose}>Cancel</Button>
              {selectedMatch && (
                <Button
                  variant="success"
                  onClick={() => void linkMatch(selectedMatch)}
                  disabled={linkingId !== null}
                >
                  {linkingId ? 'Linking…' : `Link Match #${selectedMatch.id}`}
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </>
  );
}
