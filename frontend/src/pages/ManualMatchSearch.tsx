import { type FormEvent, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Badge,
  Breadcrumb,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonLoader,
  Toast,
} from '../components/ui';
import DocumentPreview from '../components/DocumentPreview';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/manual-match-search.css';

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
}

interface MatchesResponse {
  matches?: MatchRecord[];
}

type ToastState = { message: string; tone?: 'success' | 'error' } | null;

interface SearchFilters {
  query: string;
  providerPatient: string;
  claimReference: string;
  amountSignal: string;
  createdAfter: string;
}

function valueToPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function scoreTone(score?: number | null) {
  const safeScore = valueToPercent(score);
  if (safeScore >= 85) return 'success' as const;
  if (safeScore >= 70) return 'warning' as const;
  return 'danger' as const;
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

export default function ManualMatchSearch() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const docId = searchParams.get('docId') ?? '';
  const matchId = searchParams.get('matchId') ?? '';
  // Prefilled context passed from EobUnmatched.tsx / TriageQueue.tsx links —
  // see their "search manually" actions for how these are constructed.
  const providerHint = searchParams.get('provider') ?? '';
  const patientResponsibilityHint = searchParams.get('patientResponsibility') ?? '';
  const dateOfServiceHint = searchParams.get('dateOfService') ?? '';

  const [allMatches, setAllMatches] = useState<MatchRecord[]>([]);
  const [results, setResults] = useState<MatchRecord[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(matchId ? Number(matchId) : null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [linkingId, setLinkingId] = useState<number | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [filters, setFilters] = useState<SearchFilters>({
    query: docId,
    providerPatient: providerHint,
    claimReference: docId,
    // Note: amountSignal/createdAfter filter on match-score percentages and
    // match creation time respectively — the patientResponsibility/
    // dateOfService hints from the linking page are dollar amounts and
    // service dates, not those units, so they're surfaced as read-only
    // context below instead of force-mapped into filters with different
    // semantics (see the "Context from linked EOB" banner).
    amountSignal: '',
    createdAfter: '',
  });

  useEffect(() => {
    const loadMatches = async () => {
      setLoading(true);
      setError(null);
      try {
        const response = (await endpoints.eob.matches('status=candidate&limit=200')) as MatchesResponse;
        const matches = response?.matches ?? [];
        setAllMatches(matches);
        setResults(matches);
        if (!selectedId && matches[0]?.id) {
          setSelectedId(matches[0].id);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load candidate matches.');
      } finally {
        setLoading(false);
      }
    };

    void loadMatches();
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const applyFilters = (nextFilters: SearchFilters) => {
    const minAmountSignal = nextFilters.amountSignal ? Number(nextFilters.amountSignal) : null;
    const createdAfterTime = nextFilters.createdAfter ? new Date(nextFilters.createdAfter).getTime() : null;

    const filtered = allMatches.filter((match) => {
      const searchText = buildSearchText(match);
      const queryNeedle = nextFilters.query.trim().toLowerCase();
      const providerPatientNeedle = nextFilters.providerPatient.trim().toLowerCase();
      const claimNeedle = nextFilters.claimReference.trim().toLowerCase();

      if (queryNeedle && !searchText.includes(queryNeedle)) return false;
      if (providerPatientNeedle && !searchText.includes(providerPatientNeedle)) return false;
      if (
        claimNeedle &&
        !String(match.eob_document_id ?? '').includes(claimNeedle) &&
        !String(match.bill_document_id ?? '').includes(claimNeedle) &&
        !String(match.id).includes(claimNeedle)
      ) {
        return false;
      }
      if (typeof minAmountSignal === 'number' && !Number.isNaN(minAmountSignal) && valueToPercent(match.breakdown?.amount) < minAmountSignal) {
        return false;
      }
      if (typeof createdAfterTime === 'number' && !Number.isNaN(createdAfterTime)) {
        const createdTime = match.created_at ? new Date(match.created_at).getTime() : Number.NaN;
        if (Number.isNaN(createdTime) || createdTime < createdAfterTime) return false;
      }
      if (docId) {
        const normalizedDocId = docId.trim();
        if (
          normalizedDocId &&
          !String(match.eob_document_id ?? '').includes(normalizedDocId) &&
          !String(match.bill_document_id ?? '').includes(normalizedDocId)
        ) {
          return false;
        }
      }
      return true;
    });

    setResults(filtered);
    if (!filtered.some((match) => match.id === selectedId)) {
      setSelectedId(filtered[0]?.id ?? null);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    applyFilters(filters);
  };

  const selectedMatch = useMemo(
    () => results.find((match) => match.id === selectedId) ?? allMatches.find((match) => match.id === selectedId) ?? null,
    [allMatches, results, selectedId],
  );

  const linkMatch = async (match: MatchRecord) => {
    setLinkingId(match.id);
    try {
      await endpoints.eob.updateMatch(String(match.id), { status: 'confirmed' });
      setToast({ message: `Linked candidate match #${match.id}.` });
      navigate('/triage');
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to link match.', tone: 'error' });
    } finally {
      setLinkingId(null);
    }
  };

  return (
    <>
      <Breadcrumb
        items={[
          { label: 'Needs Review', to: '/triage' },
          { label: 'Manual Match Search' },
        ]}
      />
      <PageHeader
        title="Manual Match Search"
        desc="Search the current candidate match set and manually confirm the best EOB ↔ claim pairing."
      />

      {loading ? (
        <SkeletonLoader variant="table" rows={6} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => window.location.reload()} />
      ) : (
        <div className="manual-search-shell">
          <section className="manual-context-column">
            <Card title="Source context">
              <div className="manual-context-list">
                <div className="manual-context-row"><span>Source document</span><strong>{docId || 'Not provided'}</strong></div>
                <div className="manual-context-row"><span>Current match</span><strong>{matchId || 'New manual search'}</strong></div>
                <div className="manual-context-row"><span>Candidate pool</span><strong>{allMatches.length} matches</strong></div>
                <div className="manual-context-note">
                  {/* The backend does not yet expose a dedicated claim-search endpoint with provider/patient/amount/date fields.
                      This page reuses /api/eob/matches and filters the current candidate set client-side until a richer search API exists. */}
                  Current backend search data is limited to match IDs, timestamps, confidence, and factor breakdowns. Provider/patient form inputs are kept for UX parity and future API expansion.
                </div>
              </div>
            </Card>

            {selectedMatch ? (
              <Card title="Selected candidate">
                <div className="manual-selected-summary">
                  <div className="manual-selected-title">EOB #{selectedMatch.eob_document_id ?? '—'} ↔ Bill #{selectedMatch.bill_document_id ?? '—'}</div>
                  <div className="manual-selected-badges">
                    <Badge tone={scoreTone(selectedMatch.score)}>{valueToPercent(selectedMatch.score)}% match</Badge>
                    <Badge tone="muted">{selectedMatch.confidence ?? 'Candidate'}</Badge>
                  </div>
                  <div className="manual-factor-list">
                    <div className="manual-factor-row"><span>Date signal</span><strong>{valueToPercent(selectedMatch.breakdown?.date)}%</strong></div>
                    <div className="manual-factor-row"><span>Provider signal</span><strong>{valueToPercent(selectedMatch.breakdown?.provider)}%</strong></div>
                    <div className="manual-factor-row"><span>Patient signal</span><strong>{valueToPercent(selectedMatch.breakdown?.patient)}%</strong></div>
                    <div className="manual-factor-row"><span>Amount signal</span><strong>{valueToPercent(selectedMatch.breakdown?.amount)}%</strong></div>
                    <div className="manual-factor-row"><span>Created</span><strong>{formatDateTime(selectedMatch.created_at)}</strong></div>
                  </div>
                  <div style={{ display: 'flex', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
                    {selectedMatch.eob_document_id && (
                      <DocumentPreview
                        documentId={selectedMatch.eob_document_id}
                        paperlessUrl={selectedMatch.eob_preview_url}
                        variant="compact"
                        label="EOB"
                      />
                    )}
                    {selectedMatch.bill_document_id && (
                      <DocumentPreview
                        documentId={selectedMatch.bill_document_id}
                        paperlessUrl={selectedMatch.bill_preview_url}
                        variant="compact"
                        label="Bill"
                      />
                    )}
                  </div>
                </div>
              </Card>
            ) : (
              <Card title="Selected candidate">
                <EmptyState title="Pick a result" desc="Select a candidate from the search results to inspect its match signals before linking it." />
              </Card>
            )}
          </section>

          <section className="manual-results-column">
            {(providerHint || patientResponsibilityHint || dateOfServiceHint) && (
              <div className="manual-phase-banner">
                <Badge tone="info">Context</Badge>
                <span>
                  Linked from an unmatched EOB
                  {providerHint ? ` — provider: ${providerHint}` : ''}
                  {patientResponsibilityHint ? `, patient responsibility: ${patientResponsibilityHint}` : ''}
                  {dateOfServiceHint ? `, date of service: ${dateOfServiceHint}` : ''}. The provider hint has been
                  applied to the search below — use it alongside the other filters to narrow results.
                </span>
              </div>
            )}

            <div className="manual-phase-banner">
              <Badge tone="warning">MVP</Badge>
              <span>Searching the existing candidate match dataset until a dedicated claim search endpoint is added.</span>
            </div>

            <Card title="Search candidates">
              <form onSubmit={handleSubmit}>
                <div className="form-group">
                  <label htmlFor="manual-query">Search text</label>
                  <input
                    id="manual-query"
                    value={filters.query}
                    onChange={(event) => setFilters((current) => ({ ...current, query: event.target.value }))}
                    placeholder="Document ID, match ID, status, or confidence"
                  />
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="manual-provider-patient">Provider / patient hint</label>
                    <input
                      id="manual-provider-patient"
                      value={filters.providerPatient}
                      onChange={(event) => setFilters((current) => ({ ...current, providerPatient: event.target.value }))}
                      placeholder="Reserved for future richer match metadata"
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="manual-claim-reference">Claim / document reference</label>
                    <input
                      id="manual-claim-reference"
                      value={filters.claimReference}
                      onChange={(event) => setFilters((current) => ({ ...current, claimReference: event.target.value }))}
                      placeholder="EOB ID, bill ID, or match ID"
                    />
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="manual-amount-signal">Minimum amount signal (%)</label>
                    <input
                      id="manual-amount-signal"
                      type="number"
                      min="0"
                      max="100"
                      value={filters.amountSignal}
                      onChange={(event) => setFilters((current) => ({ ...current, amountSignal: event.target.value }))}
                      placeholder="e.g. 60"
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="manual-created-after">Created after</label>
                    <input
                      id="manual-created-after"
                      type="date"
                      value={filters.createdAfter}
                      onChange={(event) => setFilters((current) => ({ ...current, createdAfter: event.target.value }))}
                    />
                  </div>
                </div>
                <div className="btn-group">
                  <Button type="submit" variant="primary">Search</Button>
                  <Button
                    type="button"
                    onClick={() => {
                      const cleared = {
                        query: docId,
                        providerPatient: '',
                        claimReference: docId,
                        amountSignal: '',
                        createdAfter: '',
                      };
                      setFilters(cleared);
                      applyFilters(cleared);
                    }}
                  >
                    Reset
                  </Button>
                </div>
              </form>
            </Card>

            <div className="manual-results-header">{results.length} potential match{results.length === 1 ? '' : 'es'} found</div>

            {results.length === 0 ? (
              <EmptyState
                title="No candidate matches found"
                desc="Try widening the filters. If you're searching from an unmatched document, the backend may need a dedicated manual-link API to surface new candidates."
              />
            ) : (
              <div className="manual-results-list">
                {results.map((match) => (
                  <article
                    key={match.id}
                    className={selectedId === match.id ? 'manual-result-card selected' : 'manual-result-card'}
                    onClick={() => setSelectedId(match.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        setSelectedId(match.id);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    aria-pressed={selectedId === match.id}
                  >
                    <div className="manual-result-top">
                      <div>
                        <div className="manual-result-title">EOB #{match.eob_document_id ?? '—'} ↔ Bill #{match.bill_document_id ?? '—'}</div>
                        <div className="text-muted">Match #{match.id} · {formatDateTime(match.created_at)}</div>
                      </div>
                      <Badge tone={scoreTone(match.score)}>{valueToPercent(match.score)}% match</Badge>
                    </div>
                    <div className="manual-result-grid">
                      <div className="manual-result-field"><span>Date</span><strong>{valueToPercent(match.breakdown?.date)}%</strong></div>
                      <div className="manual-result-field"><span>Provider</span><strong>{valueToPercent(match.breakdown?.provider)}%</strong></div>
                      <div className="manual-result-field"><span>Patient</span><strong>{valueToPercent(match.breakdown?.patient)}%</strong></div>
                      <div className="manual-result-field"><span>Amount</span><strong>{valueToPercent(match.breakdown?.amount)}%</strong></div>
                      <div className="manual-result-field"><span>Procedures</span><strong>{valueToPercent(match.breakdown?.procedures)}%</strong></div>
                      <div className="manual-result-field"><span>Status</span><strong>{match.status ?? 'candidate'}</strong></div>
                    </div>
                    <div className="manual-result-footer">
                      <div className="btn-group">
                        <Button variant="success" onClick={() => void linkMatch(match)} disabled={linkingId !== null}>
                          Link this match
                        </Button>
                        {match.bill_preview_url ? (
                          <a href={match.bill_preview_url} target="_blank" rel="noreferrer">Bill →</a>
                        ) : null}
                        {match.eob_preview_url ? (
                          <a href={match.eob_preview_url} target="_blank" rel="noreferrer">EOB →</a>
                        ) : null}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </>
  );
}
