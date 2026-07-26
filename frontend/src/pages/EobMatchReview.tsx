import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Badge,
  Button,
  Card,
  ConfidenceBar,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonLoader,
  Toast,
  confidenceTone,
} from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/eob-pages.css';

interface EobMatch {
  id: number;
  run_id?: number | null;
  eob_document_id?: number | null;
  bill_document_id?: number | null;
  score?: number | null;
  confidence?: string | null;
  breakdown?: {
    date?: number | null;
    provider?: number | null;
    patient?: number | null;
    amount?: number | null;
    procedures?: number | null;
  } | null;
  status?: string | null;
  linked_in_paperless?: boolean | null;
  eob_preview_url?: string | null;
  bill_preview_url?: string | null;
  created_at?: string | null;
  confirmed_at?: string | null;
  flag_reason?: string | null;
  notes?: string | null;
}

interface EobMatchesResponse {
  matches?: EobMatch[];
}

interface MatchHistoryEvent {
  id: number;
  event_type: string;
  actor: string;
  detail?: string | null;
  created_at?: string | null;
}

interface MatchHistoryResponse {
  match_id: number;
  events: MatchHistoryEvent[];
}

type MatchStatus = 'confirmed' | 'rejected' | 'candidate';
type ToastState = { message: string; tone: 'success' | 'error' } | null;

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return `${Math.round(value)}%`;
}

function statusTone(status?: string | null): 'success' | 'warning' | 'danger' | 'muted' {
  switch ((status || '').toLowerCase()) {
    case 'confirmed':
      return 'success';
    case 'rejected':
      return 'danger';
    case 'candidate':
      return 'warning';
    default:
      return 'muted';
  }
}

function statusLabel(status?: string | null) {
  switch ((status || '').toLowerCase()) {
    case 'confirmed':
      return 'Confirmed';
    case 'rejected':
      return 'Rejected';
    case 'candidate':
      return 'Pending review';
    default:
      return status || 'Unknown';
  }
}

function factorNarrative(label: string, value?: number | null) {
  if (typeof value !== 'number') return `${label} data is not available from the current API response.`;
  if (value >= 90) return `${label} is a strong match signal.`;
  if (value >= 70) return `${label} is directionally aligned but worth a quick check.`;
  if (value >= 50) return `${label} is weak enough to justify manual review.`;
  return `${label} is a likely mismatch and is driving this review.`;
}

function scoreBadgeTone(score?: number | null): 'success' | 'warning' | 'danger' {
  const tone = confidenceTone(score ?? 0);
  return tone === 'high' ? 'success' : tone === 'medium' ? 'warning' : 'danger';
}

function eventDotTone(eventType: string): 'info' | 'success' | 'danger' | 'muted' {
  switch (eventType) {
    case 'auto_matched': return 'info';
    case 'confirmed': return 'success';
    case 'rejected': return 'danger';
    case 'flagged': return 'muted';
    case 'reset': return 'muted';
    default: return 'muted';
  }
}

function eventLabel(eventType: string): string {
  switch (eventType) {
    case 'auto_matched': return 'Auto-matched';
    case 'flagged': return 'Flagged for review';
    case 'confirmed': return 'Confirmed';
    case 'rejected': return 'Rejected';
    case 'reset': return 'Reset to candidate';
    default: return eventType;
  }
}

export default function EobMatchReview() {
  const navigate = useNavigate();
  const { matchId } = useParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [notes, setNotes] = useState('');
  const [savingStatus, setSavingStatus] = useState<MatchStatus | null>(null);
  const [swappingAltId, setSwappingAltId] = useState<number | null>(null);
  const [match, setMatch] = useState<EobMatch | null>(null);
  const [alternatives, setAlternatives] = useState<EobMatch[]>([]);
  const [historyEvents, setHistoryEvents] = useState<MatchHistoryEvent[]>([]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const numericMatchId = Number(matchId);

  const loadMatch = useCallback(async () => {
    if (!matchId || Number.isNaN(numericMatchId)) {
      setLoading(false);
      setError('The match ID in the URL is invalid.');
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = (await endpoints.eob.matches('limit=250')) as EobMatchesResponse;
      const allMatches = response.matches ?? [];
      const currentMatch = allMatches.find((item) => item.id === numericMatchId) ?? null;
      if (!currentMatch) {
        setError(`Match #${matchId} was not found in the recent EOB queue.`);
        setMatch(null);
        setAlternatives([]);
        return;
      }
      setMatch(currentMatch);
      setAlternatives(
        allMatches
          .filter((item) => item.id !== currentMatch.id && item.run_id === currentMatch.run_id)
          .slice(0, 3),
      );

      // Fetch match history timeline
      try {
        const historyResponse = (await endpoints.eob.matchHistory(matchId)) as MatchHistoryResponse;
        setHistoryEvents(historyResponse.events ?? []);
      } catch {
        setHistoryEvents([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load match details.');
    } finally {
      setLoading(false);
    }
  }, [matchId, numericMatchId]);

  useEffect(() => {
    void loadMatch();
  }, [loadMatch]);

  const factorRows = useMemo(
    () => [
      { key: 'date', label: 'Date of service', pct: Math.round(match?.breakdown?.date ?? 0) },
      { key: 'provider', label: 'Provider', pct: Math.round(match?.breakdown?.provider ?? 0) },
      { key: 'patient', label: 'Patient', pct: Math.round(match?.breakdown?.patient ?? 0) },
      { key: 'amount', label: 'Amount', pct: Math.round(match?.breakdown?.amount ?? 0) },
      { key: 'procedures', label: 'Procedures', pct: Math.round(match?.breakdown?.procedures ?? 0) },
    ],
    [match],
  );

  const weakestFactor = useMemo(() => {
    const withValues = factorRows.filter((row) => typeof row.pct === 'number');
    return withValues.reduce<{ key: string; label: string; pct: number } | null>(
      (lowest, row) => (lowest === null || row.pct < lowest.pct ? row : lowest),
      null,
    );
  }, [factorRows]);

  const amountToneClass = (match?.breakdown?.amount ?? 0) >= 75 ? 'pass' : 'fail';

  const handleUpdate = useCallback(
    async (status: MatchStatus) => {
      if (!matchId) return;
      setSavingStatus(status);
      try {
        await endpoints.eob.updateMatch(matchId, { status, notes: notes.trim() || null });
        setToast({
          message:
            status === 'confirmed'
              ? 'Match confirmed.'
              : status === 'rejected'
                ? 'Match rejected.'
                : 'Match returned to candidate status.',
          tone: 'success',
        });
        setNotes('');
        await loadMatch();
      } catch (err) {
        setToast({
          message: err instanceof Error ? err.message : 'Unable to update match.',
          tone: 'error',
        });
      } finally {
        setSavingStatus(null);
      }
    },
    [loadMatch, matchId, notes],
  );

  const handleSwapAlternative = useCallback(
    async (altId: number) => {
      if (!matchId || swappingAltId !== null) return;
      setSwappingAltId(altId);
      try {
        await endpoints.eob.updateMatch(matchId, { status: 'rejected', notes: `Swapped in favor of Match #${altId}` });
        await endpoints.eob.updateMatch(String(altId), { status: 'confirmed', notes: `Selected as replacement for Match #${matchId}` });
        setToast({ message: `Swapped to Match #${altId}. Redirecting…`, tone: 'success' });
        setTimeout(() => navigate(`/eob/matches/${altId}`), 1200);
      } catch (err) {
        setToast({ message: err instanceof Error ? err.message : 'Unable to swap match.', tone: 'error' });
      } finally {
        setSwappingAltId(null);
      }
    },
    [matchId, swappingAltId, navigate],
  );

  const handleRelink = useCallback(() => {
    if (!match || savingStatus !== null) return;
    const params = new URLSearchParams({ matchId: String(match.id) });
    if (match.eob_document_id != null) {
      params.set('docId', String(match.eob_document_id));
    }
    navigate(`/triage/manual-search?${params.toString()}`);
  }, [match, savingStatus, navigate]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        handleRelink();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleRelink]);

  if (loading) {
    return (
      <>
        <PageHeader title="Match review" desc="Inspect the candidate pair and decide whether it should be confirmed." />
        <SkeletonLoader variant="cards" />
      </>
    );
  }

  if (error) {
    return (
      <>
        <PageHeader title="Match review" desc="Inspect the candidate pair and decide whether it should be confirmed." />
        <ErrorState message={error} onRetry={() => void loadMatch()} />
      </>
    );
  }

  if (!match) {
    return (
      <>
        <PageHeader title="Match review" desc="Inspect the candidate pair and decide whether it should be confirmed." />
        <EmptyState title="No review record found" desc="The requested match is not available in the current EOB match list." />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={
          <div className="eob-header-stack">
            <Link to="/eob" className="eob-link">
              ← Back to dashboard
            </Link>
            <span>
              Match #{match.id}: EOB #{match.eob_document_id ?? '—'} ↔ Bill #{match.bill_document_id ?? '—'}
            </span>
          </div>
        }
        desc={
          <div className="eob-meta-row">
            <Badge tone={statusTone(match.status)}>{statusLabel(match.status)}</Badge>
            <Badge tone={scoreBadgeTone(match.score)}>
              {formatPercent(match.score)} overall · {(match.confidence || 'candidate').toUpperCase()}
            </Badge>
            <span>Created {formatDateTime(match.created_at)}</span>
          </div>
        }
        actions={
          <div className="btn-group">
            <Button
              variant="success"
              onClick={() => void handleUpdate('confirmed')}
              disabled={savingStatus !== null}
            >
              {savingStatus === 'confirmed' ? 'Confirming…' : 'Confirm'}
            </Button>
            <Button
              variant="danger"
              onClick={() => void handleUpdate('rejected')}
              disabled={savingStatus !== null}
            >
              {savingStatus === 'rejected' ? 'Rejecting…' : 'Reject'}
            </Button>
            <Button
              onClick={handleRelink}
              disabled={savingStatus !== null}
              title="Re-link to Different (R)"
            >
              Re-link to Different
            </Button>
            <Button onClick={() => navigate('/eob')} disabled={savingStatus !== null}>
              Skip
            </Button>
          </div>
        }
      />

      <div className="eob-review-shell">
        <Card className="eob-sticky-actions">
          <div className="eob-action-strip">
            <div>
              <div className="eob-action-strip-title">Review action</div>
              <div className="eob-table-secondary">
                Confirm to write back when enabled, reject to keep the documents separate, or skip to return later.
              </div>
            </div>
            <div className="btn-group">
              <Button
                variant="success"
                onClick={() => void handleUpdate('confirmed')}
                disabled={savingStatus !== null}
              >
                Accept match
              </Button>
              <Button
                variant="danger"
                onClick={() => void handleUpdate('rejected')}
                disabled={savingStatus !== null}
              >
                Reject match
              </Button>
              <Button
                onClick={handleRelink}
                disabled={savingStatus !== null}
                title="Re-link to Different (R)"
              >
                Re-link to Different
              </Button>
              <Button onClick={() => void handleUpdate('candidate')} disabled={savingStatus !== null}>
                Reset to pending
              </Button>
            </div>
          </div>
        </Card>

        <div className={`eob-reason-banner ${weakestFactor && weakestFactor.pct < 70 ? 'danger' : ''}`}>
          <strong>⚠️ Why this was flagged:</strong>{' '}
          {match.flag_reason ?? (weakestFactor
            ? `${weakestFactor.label} scored ${weakestFactor.pct}%, so the matcher could not fully auto-confirm this pair.`
            : 'This candidate pair needs manual confirmation.')}
          {!match.flag_reason && (
            <span className="eob-banner-note">
              The current API exposes score factors and document references, but not the extracted field values themselves.
            </span>
          )}
        </div>

        <div className="eob-grid-2">
          <Card title="Match confidence breakdown">
            <div className="eob-card-stack">
              <div className="eob-confidence-overview">
                <div>
                  <div className={`eob-confidence-score ${confidenceTone(match.score ?? 0)}`}>
                    {formatPercent(match.score)}
                  </div>
                  <div className="eob-table-secondary">Weighted 5-factor score</div>
                </div>
                <Badge tone={scoreBadgeTone(match.score)}>{(match.confidence || 'unknown').toUpperCase()}</Badge>
              </div>
              <div className="eob-card-stack">
                {factorRows.map((row) => (
                  <ConfidenceBar key={row.key} label={row.label} pct={row.pct} />
                ))}
              </div>
              <div className="eob-factor-grid">
                {factorRows.map((row) => (
                  <div
                    key={`${row.key}-note`}
                    className={`eob-factor-card ${row.pct < 70 ? 'mismatch' : ''}`}
                  >
                    <div className="eob-field-label">{row.label}</div>
                    <div className="eob-field-value">{formatPercent(row.pct)}</div>
                    <div className="eob-field-note">{factorNarrative(row.label, row.pct)}</div>
                  </div>
                ))}
              </div>
            </div>
          </Card>

          <Card title="Amount validation">
            <div className={`eob-validation ${amountToneClass}`}>
              <div className="eob-validation-icon">{amountToneClass === 'pass' ? '✓' : '⚠'}</div>
              <div className="eob-card-stack">
                <div className="eob-field-value">
                  {amountToneClass === 'pass' ? 'Amount signal is aligned' : 'Amount signal needs review'}
                </div>
                <div className="eob-field-note">
                  Amount similarity scored {formatPercent(match.breakdown?.amount)}. The matcher compares EOB patient responsibility
                  with bill balance/total due.
                </div>
                <div className="eob-meta-row">
                  <Badge tone={amountToneClass === 'pass' ? 'success' : 'danger'}>
                    {amountToneClass === 'pass' ? 'Pass' : 'Mismatch risk'}
                  </Badge>
                  <span>Linked in Paperless: {match.linked_in_paperless ? 'Yes' : 'No'}</span>
                  <span>Confirmed at: {formatDateTime(match.confirmed_at)}</span>
                </div>
              </div>
            </div>
          </Card>
        </div>

        <Card title="Document comparison">
          <div className="eob-compare-grid">
            <div className="eob-compare-col">
              <div className="eob-compare-header">
                <span>EOB document</span>
                <Badge tone="info">#{match.eob_document_id ?? '—'}</Badge>
              </div>
              <div className="eob-compare-row">
                <div className="eob-field-label">Document ID</div>
                <div className="eob-field-value">{match.eob_document_id ?? '—'}</div>
              </div>
              <div className="eob-compare-row">
                <div className="eob-field-label">Preview</div>
                <div className="eob-field-value">
                  {match.eob_preview_url ? (
                    <a className="eob-link" href={match.eob_preview_url} target="_blank" rel="noreferrer">
                      Open EOB preview →
                    </a>
                  ) : (
                    'Preview unavailable'
                  )}
                </div>
              </div>
              <div className={`eob-compare-row ${(match.breakdown?.date ?? 0) < 70 ? 'mismatch' : ''}`}>
                <div className="eob-field-label">Date of service signal</div>
                <div className="eob-field-value">{formatPercent(match.breakdown?.date)}</div>
                <div className="eob-field-note">{factorNarrative('Date of service', match.breakdown?.date)}</div>
              </div>
              <div className={`eob-compare-row ${(match.breakdown?.provider ?? 0) < 70 ? 'mismatch' : ''}`}>
                <div className="eob-field-label">Provider signal</div>
                <div className="eob-field-value">{formatPercent(match.breakdown?.provider)}</div>
                <div className="eob-field-note">{factorNarrative('Provider', match.breakdown?.provider)}</div>
              </div>
              <div className={`eob-compare-row ${(match.breakdown?.patient ?? 0) < 70 ? 'mismatch' : ''}`}>
                <div className="eob-field-label">Patient signal</div>
                <div className="eob-field-value">{formatPercent(match.breakdown?.patient)}</div>
                <div className="eob-field-note">{factorNarrative('Patient', match.breakdown?.patient)}</div>
              </div>
            </div>

            <div className="eob-compare-col">
              <div className="eob-compare-header">
                <span>Candidate bill</span>
                <Badge tone="info">#{match.bill_document_id ?? '—'}</Badge>
              </div>
              <div className="eob-compare-row">
                <div className="eob-field-label">Document ID</div>
                <div className="eob-field-value">{match.bill_document_id ?? '—'}</div>
              </div>
              <div className="eob-compare-row">
                <div className="eob-field-label">Preview</div>
                <div className="eob-field-value">
                  {match.bill_preview_url ? (
                    <a className="eob-link" href={match.bill_preview_url} target="_blank" rel="noreferrer">
                      Open bill preview →
                    </a>
                  ) : (
                    'Preview unavailable'
                  )}
                </div>
              </div>
              <div className={`eob-compare-row ${(match.breakdown?.amount ?? 0) < 70 ? 'mismatch' : ''}`}>
                <div className="eob-field-label">Amount signal</div>
                <div className="eob-field-value">{formatPercent(match.breakdown?.amount)}</div>
                <div className="eob-field-note">{factorNarrative('Amount', match.breakdown?.amount)}</div>
              </div>
              <div className={`eob-compare-row ${(match.breakdown?.procedures ?? 0) < 70 ? 'mismatch' : ''}`}>
                <div className="eob-field-label">Procedure overlap</div>
                <div className="eob-field-value">{formatPercent(match.breakdown?.procedures)}</div>
                <div className="eob-field-note">{factorNarrative('Procedure overlap', match.breakdown?.procedures)}</div>
              </div>
              <div className="eob-compare-row">
                <div className="eob-field-label">Workflow state</div>
                <div className="eob-field-value">{statusLabel(match.status)}</div>
                <div className="eob-field-note">Run #{match.run_id ?? '—'} · Created {formatDateTime(match.created_at)}</div>
              </div>
            </div>
          </div>
        </Card>

        <div className="eob-grid-2">
          <Card title="Alternative candidates">
            {alternatives.length ? (
              <div className="eob-card-stack">
                {alternatives.map((item) => {
                  const altScore = Math.round(item.score ?? 0);
                  const altTone = altScore >= 75 ? 'success' : altScore >= 50 ? 'warning' : 'danger';
                  return (
                    <div key={item.id} className="eob-alt-card">
                      <div className="eob-alt-card-header">
                        <div>
                          <div className="eob-field-value">
                            Match #{item.id}: EOB #{item.eob_document_id ?? '—'} ↔ Bill #{item.bill_document_id ?? '—'}
                          </div>
                          <div className="eob-field-note">
                            Status {statusLabel(item.status)} · Run #{item.run_id ?? '—'}
                          </div>
                        </div>
                        <Badge tone={altTone}>{formatPercent(item.score)}</Badge>
                      </div>
                      {item.breakdown && (
                        <div className="eob-alt-factors">
                          {(['date', 'provider', 'patient', 'amount'] as const).map((factor) => {
                            const val = Math.round(item.breakdown?.[factor] ?? 0);
                            return (
                              <div key={factor} className="eob-alt-factor">
                                <span className="eob-alt-factor-label">{factor}</span>
                                <ConfidenceBar label="" pct={val} />
                              </div>
                            );
                          })}
                        </div>
                      )}
                      <div className="eob-alt-card-actions">
                        <Button
                          size="sm"
                          variant="primary"
                          disabled={swappingAltId !== null}
                          onClick={() => void handleSwapAlternative(item.id)}
                        >
                          {swappingAltId === item.id ? 'Swapping…' : 'Select This Instead'}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => navigate(`/eob/matches/${item.id}`)}>
                          Review →
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <EmptyState
                title="No alternate candidates in this run"
                desc="The current API only exposes recent match rows, so alternative suggestions are limited to same-run records."
              />
            )}
          </Card>

          <Card title="Match history & notes">
            <div className="eob-card-stack">
              <div className="eob-timeline">
                {historyEvents.length > 0 ? (
                  historyEvents.map((event) => (
                    <div key={event.id} className="eob-timeline-item">
                      <div className="eob-timeline-track">
                        <span className={`eob-history-dot ${eventDotTone(event.event_type)}`} />
                        <span className="eob-timeline-line" />
                      </div>
                      <div className="eob-timeline-content">
                        <div className="eob-field-value">{eventLabel(event.event_type)}</div>
                        {event.detail && <div className="eob-field-note">{event.detail}</div>}
                        <div className="eob-field-note">
                          {event.actor === 'user' ? '👤 Reviewer' : '⚙️ System'} · {formatDateTime(event.created_at)}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="eob-history-list">
                    <div className="eob-history-item">
                      <span className="eob-history-dot info" />
                      <div>
                        <div className="eob-field-value">Candidate created</div>
                        <div className="eob-field-note">{formatDateTime(match.created_at)}</div>
                      </div>
                    </div>
                    <div className="eob-history-item">
                      <span className={`eob-history-dot ${match.status === 'confirmed' ? 'success' : match.status === 'rejected' ? 'danger' : 'muted'}`} />
                      <div>
                        <div className="eob-field-value">{statusLabel(match.status)}</div>
                        <div className="eob-field-note">
                          {match.confirmed_at ? formatDateTime(match.confirmed_at) : 'Awaiting reviewer action'}
                        </div>
                      </div>
                    </div>
                      {match.notes && (
                        <div className="eob-field-note" style={{ marginTop: 4, fontStyle: 'italic' }}>
                          &ldquo;{match.notes}&rdquo;
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="eob-note-box">
                <div className="eob-field-label">Reviewer notes</div>
                <textarea
                  className="eob-notes-input"
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  placeholder="Add context about this match decision (e.g., 'Bill includes $6 lab draw fee not on EOB — confirmed correct match')"
                />
                <div className="eob-field-note">
                  Notes are saved with your confirm / reject action.
                </div>
              </div>
            </div>
          </Card>
        </div>
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}
