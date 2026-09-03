/**
 * OCR candidate generation, comparison, and accept/reject UI (issue #18, slice 1).
 *
 * Everything here is read-only-to-Paperless: generation stages candidates in
 * OWL's own tables, and accepting/rejecting a candidate only records a
 * decision in those same tables. Nothing on this page ever changes the live
 * Paperless document — applying an accepted candidate as a new Paperless
 * version, version preservation, and rollback are a later slice gated on
 * issue #114.
 */
import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card, EmptyState, type Tone } from './ui';
import { endpoints } from '../lib/api';
import { formatScore } from '../pages/OcrQualityReviewQueue';

type CandidateSummary = {
  candidate_id: string;
  document_id: number;
  state: string;
  engine: string;
  model_version?: string | null;
  overlay_score?: number | null;
  machine_score?: number | null;
  content_score?: number | null;
  page_count: number;
  requested_at?: string | null;
  completed_at?: string | null;
  decision?: string | null;
  expires_at?: string | null;
};

type ComparisonSummary = {
  comparison_id: string;
  blocking_findings: string[];
  text_diff_summary: Record<string, unknown>;
  overlay_score_delta?: number | null;
  machine_score_delta?: number | null;
  content_score_delta?: number | null;
  performed_at?: string | null;
};

type CandidateDetail = CandidateSummary & {
  comparison: ComparisonSummary | null;
  failure_reason?: string | null;
  decision_reason?: string | null;
  decided_at?: string | null;
  actor?: string | null;
};

const ENGINE_LABELS: Record<string, string> = {
  'ocrmypdf-tesseract-5': 'OCRmyPDF / Tesseract 5',
  'azure-prebuilt-layout': 'Azure Document Intelligence (prebuilt-layout)',
};

const ACTIVE_STATES = new Set(['requested', 'running']);

function stateTone(state: string): Tone {
  switch (state) {
    case 'ready': return 'info';
    case 'accepted': return 'ok';
    case 'rejected': return 'muted';
    case 'expired': return 'muted';
    case 'failed': return 'err';
    case 'running': return 'warn';
    default: return 'muted';
  }
}

// Thresholds for non-authoritative "suggested read" hints (issue #18 slice 1
// clarity follow-up). These never gate or authorize acceptance — they only
// help a reviewer scan quickly when the two engines' scores diverge. Loosely
// mirrors comparison.py's blocking machine-regression tolerance (5 points)
// but intentionally smaller since these are hints, not gates.
const CONTENT_IMPROVEMENT_HINT_THRESHOLD = 3;
const OVERLAY_DECLINE_HINT_THRESHOLD = -3;

type SuggestedReadBadge = { tone: Tone; label: string };

type SuggestedReadInput = {
  blocking_findings: string[];
  overlay_score_delta?: number | null;
  content_score_delta?: number | null;
};

function suggestedReadBadges(input: SuggestedReadInput | null): SuggestedReadBadge[] {
  if (!input) return [];
  const badges: SuggestedReadBadge[] = [];
  const { blocking_findings, overlay_score_delta, content_score_delta } = input;

  if (blocking_findings.length > 0) {
    badges.push({ tone: 'err', label: '⚠ Needs careful review' });
  }
  if (content_score_delta != null && content_score_delta >= CONTENT_IMPROVEMENT_HINT_THRESHOLD && blocking_findings.length === 0) {
    badges.push({ tone: 'ok', label: 'Content accuracy looks improved' });
  }
  if (overlay_score_delta != null && overlay_score_delta <= OVERLAY_DECLINE_HINT_THRESHOLD) {
    badges.push({ tone: 'warn', label: 'Box/highlight placement may be less precise' });
  }
  if (badges.length === 0) {
    if (overlay_score_delta == null && content_score_delta == null) {
      badges.push({ tone: 'muted', label: 'No comparison signal available' });
    } else {
      badges.push({ tone: 'muted', label: 'No strong signal either way' });
    }
  }
  return badges;
}

function scoreDelta(current?: number | null, candidate?: number | null): number | null {
  if (current == null || candidate == null) return null;
  return candidate - current;
}

// Floating-point tolerance for "is this candidate's score the max" comparisons
// below — scores are rounded server-side but comparing floats directly risks
// missing true ties.
const RELATIVE_SCORE_TIE_EPSILON = 0.01;

// Candidate-vs-candidate relative badges (as distinct from the vs-current
// "suggested read" badges above). These are purely factual/descriptive
// ("this one is highest") — never a "best pick" or ranked #1/#2 label — so a
// reviewer choosing between multiple ready candidates isn't left with two
// identically-worded vs-current badges and no way to tell them apart, while
// still stopping short of anything that reads as the system recommending an
// engine or authorizing acceptance.
function relativeBadges(
  candidate: CandidateSummary,
  readyCandidates: CandidateSummary[],
): SuggestedReadBadge[] {
  if (readyCandidates.length < 2) return [];
  const badges: SuggestedReadBadge[] = [];

  const overlayScores = readyCandidates.map((c) => c.overlay_score).filter((v): v is number => v != null);
  if (overlayScores.length >= 2 && candidate.overlay_score != null) {
    const maxOverlay = Math.max(...overlayScores);
    if (maxOverlay - candidate.overlay_score <= RELATIVE_SCORE_TIE_EPSILON) {
      badges.push({ tone: 'info', label: 'Highest overlay score of ready candidates' });
    }
  }

  const contentScores = readyCandidates.map((c) => c.content_score).filter((v): v is number => v != null);
  if (contentScores.length >= 2 && candidate.content_score != null) {
    const maxContent = Math.max(...contentScores);
    if (maxContent - candidate.content_score <= RELATIVE_SCORE_TIE_EPSILON) {
      badges.push({ tone: 'info', label: 'Highest content accuracy of ready candidates' });
    }
  }

  return badges;
}

// Inverse of scoreDelta: the comparison endpoint only stores candidate score
// + delta, so the "current" baseline for the side-by-side line is derived
// rather than fetched again.
function currentScoreFromDelta(candidateScore?: number | null, delta?: number | null): number | null {
  if (candidateScore == null || delta == null) return null;
  return candidateScore - delta;
}

function SuggestedReadBadges({ badges }: { badges: SuggestedReadBadge[] }) {
  if (badges.length === 0) return <span className="text-muted">—</span>;
  return (
    <div className="ocr-suggested-read-row">
      {badges.map((b) => (
        <Badge key={b.label} tone={b.tone}>{b.label}</Badge>
      ))}
    </div>
  );
}

function formatScoreDelta(delta?: number | null) {
  if (delta == null) return '—';
  const sign = delta > 0 ? '+' : '';
  return `${sign}${delta}`;
}

export default function OcrCandidatesPanel({
  documentId,
  hasStage2Analysis,
  currentOverlayScore,
  currentMachineScore,
  currentContentScore,
}: {
  documentId: number;
  hasStage2Analysis?: boolean;
  currentOverlayScore?: number | null;
  currentMachineScore?: number | null;
  currentContentScore?: number | null;
}) {
  const [candidates, setCandidates] = useState<CandidateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [engines, setEngines] = useState<string[]>(['ocrmypdf-tesseract-5']);
  const [requesting, setRequesting] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [text, setText] = useState<{ current_text: string | null; candidate_text: string | null } | null>(null);
  const [deciding, setDeciding] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // `background` is true for the 3s status polling below — it refreshes rows
  // in place without dropping back to the full "Loading candidates…" state,
  // so the table doesn't flash while a generation is in progress.
  const loadCandidates = useCallback((background = false) => {
    if (!background) setLoading(true);
    setError(null);
    endpoints.ocrQuality.candidates
      .list({ document_id: documentId })
      .then((data) => setCandidates((data as { candidates: CandidateSummary[] }).candidates))
      .catch((err) => {
        if (!background) setError(err instanceof Error ? err.message : 'Failed to load candidates.');
      })
      .finally(() => {
        if (!background) setLoading(false);
      });
  }, [documentId]);

  useEffect(() => {
    loadCandidates();
  }, [loadCandidates]);

  // Lightweight polling while any candidate is still generating, so the
  // reviewer doesn't have to manually refresh to see it flip to READY/FAILED.
  useEffect(() => {
    if (!candidates.some((c) => ACTIVE_STATES.has(c.state))) return;
    const timer = setInterval(() => loadCandidates(true), 3000);
    return () => clearInterval(timer);
  }, [candidates, loadCandidates]);

  const loadDetail = useCallback((candidateId: string) => {
    setSelectedId(candidateId);
    setDetail(null);
    setText(null);
    setActionError(null);
    endpoints.ocrQuality.candidates
      .get(candidateId)
      .then((data) => setDetail(data as CandidateDetail))
      .catch((err) => setActionError(err instanceof Error ? err.message : 'Failed to load candidate.'));
    endpoints.ocrQuality.candidates
      .text(candidateId)
      .then((data) => setText(data as { current_text: string | null; candidate_text: string | null }))
      .catch(() => setText(null));
  }, []);

  const toggleEngine = (engine: string) => {
    setEngines((prev) => (prev.includes(engine) ? prev.filter((e) => e !== engine) : [...prev, engine]));
  };

  const requestGeneration = () => {
    if (engines.length === 0) return;
    setRequesting(true);
    setActionError(null);
    endpoints.ocrQuality.candidates
      .request({ document_ids: [documentId], engines })
      .then(() => loadCandidates())
      .catch((err) => setActionError(err instanceof Error ? err.message : 'Failed to request candidate generation.'))
      .finally(() => setRequesting(false));
  };

  const decide = (decision: 'accepted' | 'rejected') => {
    if (!selectedId) return;
    setDeciding(true);
    setActionError(null);
    endpoints.ocrQuality.candidates
      .decide(selectedId, { decision })
      .then(() => {
        loadCandidates();
        loadDetail(selectedId);
      })
      .catch((err) => setActionError(err instanceof Error ? err.message : `Failed to ${decision} candidate.`))
      .finally(() => setDeciding(false));
  };

  const cancel = (candidateId: string) => {
    setCancelling(true);
    setActionError(null);
    endpoints.ocrQuality.candidates
      .cancel(candidateId)
      .then(() => {
        loadCandidates();
        if (selectedId === candidateId) loadDetail(candidateId);
      })
      .catch((err) => setActionError(err instanceof Error ? err.message : 'Failed to cancel candidate.'))
      .finally(() => setCancelling(false));
  };

  return (
    <Card title="OCR candidates">
      <div className="ocr-stub-note">
        Generating and comparing candidates never changes the live Paperless document. Accepting a
        candidate only records a decision in OWL's own records — applying it as the new Paperless
        version is a later step, not yet built (tracked in issues #18/#114).
      </div>

      <div className="ocr-candidate-generate" style={{ marginTop: 12, marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
          {Object.entries(ENGINE_LABELS).map(([value, label]) => (
            <label key={value} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <input
                type="checkbox"
                checked={engines.includes(value)}
                onChange={() => toggleEngine(value)}
              />
              {label}
            </label>
          ))}
          <Button onClick={requestGeneration} disabled={requesting || engines.length === 0}>
            {requesting ? 'Requesting…' : 'Generate candidates'}
          </Button>
        </div>
      </div>

      {actionError && <div className="ocr-unavailable-note">{actionError}</div>}

      {loading && <div className="text-muted">Loading candidates…</div>}
      {!loading && error && <div className="ocr-unavailable-note">{error}</div>}
      {!loading && !error && candidates.length === 0 && (
        <EmptyState icon="🧾" title="No candidates yet" desc="Generate a candidate above to compare it against the current document." />
      )}
      {!loading && !error && candidates.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Engine</th>
              <th>State</th>
              <th>Overlay</th>
              <th>Machine</th>
              <th>Content</th>
              <th>Pages</th>
              <th>Decision</th>
              <th>Suggested read</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(currentOverlayScore != null || currentMachineScore != null) && (
              <tr className="ocr-candidate-current-row">
                <td>Current (Paperless)</td>
                <td><Badge tone="muted">live</Badge></td>
                <td>{currentOverlayScore == null ? '—' : currentOverlayScore}</td>
                <td>{currentMachineScore == null ? '—' : currentMachineScore}</td>
                <td>{currentContentScore == null ? '—' : currentContentScore}</td>
                <td>—</td>
                <td>—</td>
                <td className="text-muted">Reference baseline — not a candidate</td>
                <td />
              </tr>
            )}
            {candidates.map((c) => {
              const readyCandidates = candidates.filter((cand) => cand.state === 'ready');
              const rowBadges = [
                ...suggestedReadBadges({
                  blocking_findings: [],
                  overlay_score_delta: scoreDelta(currentOverlayScore, c.overlay_score),
                  content_score_delta: scoreDelta(currentContentScore, c.content_score),
                }),
                ...(c.state === 'ready' ? relativeBadges(c, readyCandidates) : []),
              ];
              return (
                <tr key={c.candidate_id} className={selectedId === c.candidate_id ? 'is-selected' : ''}>
                  <td>{ENGINE_LABELS[c.engine] ?? c.engine}</td>
                  <td><Badge tone={stateTone(c.state)}>{c.state}</Badge></td>
                  <td>{c.overlay_score == null ? '—' : c.overlay_score}</td>
                  <td>{c.machine_score == null ? '—' : c.machine_score}</td>
                  <td>{c.content_score == null ? '—' : c.content_score}</td>
                  <td>{c.page_count || '—'}</td>
                  <td>{c.decision ?? '—'}</td>
                  <td>{c.state === 'ready' || c.decision ? <SuggestedReadBadges badges={rowBadges} /> : <span className="text-muted">—</span>}</td>
                  <td style={{ display: 'flex', gap: 6 }}>
                    <Button variant="ghost" size="sm" onClick={() => loadDetail(c.candidate_id)}>
                      View
                    </Button>
                    {ACTIVE_STATES.has(c.state) && (
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => cancel(c.candidate_id)}
                        disabled={cancelling}
                      >
                        Cancel
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {selectedId && detail && (
        <div className="ocr-candidate-detail" style={{ marginTop: 20 }}>
          <h3>Candidate {detail.candidate_id.slice(0, 8)}</h3>
          {detail.failure_reason && <div className="ocr-unavailable-note">Generation failed: {detail.failure_reason}</div>}

          {detail.comparison && (
            <div className="ocr-comparison-summary" style={{ marginBottom: 12 }}>
              {hasStage2Analysis === false && (
                <div className="ocr-unavailable-note">
                  <Badge tone="info">Stage 2 not run</Badge> This document hasn't had Stage 2
                  analysis, so the overlay comparison below may be incomplete or unavailable.
                </div>
              )}
              <div className="ocr-score-compare-row">
                <span>
                  Current: overlay {formatScore(currentScoreFromDelta(detail.overlay_score, detail.comparison.overlay_score_delta))} · machine{' '}
                  {formatScore(currentScoreFromDelta(detail.machine_score, detail.comparison.machine_score_delta))} · content{' '}
                  {formatScore(currentScoreFromDelta(detail.content_score, detail.comparison.content_score_delta))}
                </span>
                <span>
                  Candidate: overlay {formatScore(detail.overlay_score)} · machine {formatScore(detail.machine_score)} · content{' '}
                  {formatScore(detail.content_score)}
                </span>
              </div>
              <div>
                Overlay Δ: <strong>{formatScoreDelta(detail.comparison.overlay_score_delta)}</strong>{' '}
                &nbsp;Machine Δ: <strong>{formatScoreDelta(detail.comparison.machine_score_delta)}</strong>{' '}
                &nbsp;Content Δ: <strong>{formatScoreDelta(detail.comparison.content_score_delta)}</strong>
              </div>
              {detail.comparison.blocking_findings.length > 0 ? (
                <div className="ocr-blocking-findings">
                  {detail.comparison.blocking_findings.map((f) => (
                    <Badge key={f} tone="warn">{f}</Badge>
                  ))}
                </div>
              ) : (
                <div className="text-muted">No blocking findings.</div>
              )}
              <div className="ocr-suggested-read-row" style={{ marginTop: 6 }}>
                <SuggestedReadBadges
                  badges={[
                    ...suggestedReadBadges(detail.comparison),
                    ...(detail.state === 'ready'
                      ? relativeBadges(detail, candidates.filter((c) => c.state === 'ready'))
                      : []),
                  ]}
                />
              </div>
              <div className="ocr-comparison-note text-muted">
                These are suggested reads based on score deltas, comparison findings, and how this
                candidate's scores compare to other ready candidates for this document — not a
                recommendation. A higher score is informational only — it does not authorize
                acceptance. A human reviewer makes the final call for each candidate.
              </div>
            </div>
          )}

          {text && (
            <div className="ocr-text-diff" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <h4>Current (live Paperless)</h4>
                <pre className="ocr-text-pane">{text.current_text || '(no text)'}</pre>
              </div>
              <div>
                <h4>Candidate ({ENGINE_LABELS[detail.engine] ?? detail.engine})</h4>
                <pre className="ocr-text-pane">{text.candidate_text || '(no text)'}</pre>
              </div>
            </div>
          )}

          {detail.state === 'ready' && (
            <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
              <Button variant="success" onClick={() => decide('accepted')} disabled={deciding}>
                Accept
              </Button>
              <Button variant="danger" onClick={() => decide('rejected')} disabled={deciding}>
                Reject
              </Button>
            </div>
          )}
          {ACTIVE_STATES.has(detail.state) && (
            <div style={{ marginTop: 12 }}>
              <Button variant="danger" onClick={() => cancel(detail.candidate_id)} disabled={cancelling}>
                {cancelling ? 'Cancelling…' : 'Cancel'}
              </Button>
            </div>
          )}
          {detail.decision && (
            <div className="text-muted" style={{ marginTop: 8 }}>
              Decision recorded: <strong>{detail.decision}</strong> by {detail.actor ?? 'unknown'} — this has
              not modified the live Paperless document.
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
