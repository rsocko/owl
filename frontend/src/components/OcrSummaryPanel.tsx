import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  ExternalLink,
  FileSearch,
  RefreshCw,
} from 'lucide-react';
import { useMemo } from 'react';
import {
  isOcrSummaryStale,
  useOcrSummary,
  type OcrAssessment,
  type OcrCandidateSummary,
  type OcrSummaryState,
} from '../hooks/useOcrSummary';
import { Button, EmptyState, ErrorState, SkeletonLoader } from './ui';
import '../styles/ocr-summary.css';

const ACTIVE_CANDIDATE_STATES = new Set(['requested', 'running']);
const ACCEPTED_CANDIDATE_STATES = new Set(['accepted', 'accepted_pending_invalidation']);

function dateValue(value?: string | null): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

function formatDateTime(value?: string | null): string {
  const parsed = dateValue(value);
  if (parsed == null) return 'Not yet assessed';
  return new Date(parsed).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatScore(value?: number | null): string {
  return value == null ? '—' : Math.round(value).toString();
}

function statusLabel(value?: string | null): string {
  if (!value) return 'Not assessed';
  return value.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusTone(value?: string | null): string {
  switch (value) {
    case 'GOOD': return 'good';
    case 'UNCERTAIN': return 'uncertain';
    case 'REVIEW_RECOMMENDED': return 'review';
    case 'FAILED': return 'failed';
    default: return 'unknown';
  }
}

function candidateStateLabel(state: string): string {
  return state.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function CandidateState({
  assessment,
  candidates,
}: {
  assessment: OcrAssessment;
  candidates: OcrCandidateSummary[];
}) {
  const pendingInvalidation = assessment.accepted_candidate_pending_invalidation
    || candidates.some((candidate) => candidate.state === 'accepted_pending_invalidation');
  const accepted = assessment.has_accepted_ocr_candidate
    || candidates.some((candidate) => ACCEPTED_CANDIDATE_STATES.has(candidate.state));
  const active = candidates.find((candidate) => ACTIVE_CANDIDATE_STATES.has(candidate.state));
  const ready = candidates.find((candidate) => candidate.state === 'ready');
  const failed = candidates.find((candidate) => candidate.state === 'failed');

  let icon = <FileSearch size={18} />;
  let label = 'Current OCR only';
  let detail = 'No replacement OCR candidate is active.';
  let tone = 'current';

  if (pendingInvalidation) {
    icon = <AlertTriangle size={18} />;
    label = 'Accepted · updates pending';
    detail = 'The accepted OCR is applied, but downstream analysis still needs invalidation.';
    tone = 'pending';
  } else if (accepted) {
    icon = <CheckCircle2 size={18} />;
    label = 'Resolved with accepted OCR';
    detail = assessment.accepted_candidate_at
      ? `Accepted ${formatDateTime(assessment.accepted_candidate_at)}.`
      : 'A replacement OCR candidate has been accepted.';
    tone = 'accepted';
  } else if (active) {
    icon = <RefreshCw className="ocr-summary-spin" size={18} />;
    label = 'Candidate in progress';
    detail = `${active.engine} · ${candidateStateLabel(active.state)}`;
    tone = 'active';
  } else if (ready) {
    icon = <Clock3 size={18} />;
    label = 'Candidate ready to compare';
    detail = `${ready.engine} is ready for review.`;
    tone = 'ready';
  } else if (failed) {
    icon = <AlertTriangle size={18} />;
    label = 'Candidate generation failed';
    detail = `${failed.engine} · inspect OCR for failure details.`;
    tone = 'failed';
  } else if (candidates.length > 0) {
    const latest = candidates[0];
    label = 'Current OCR retained';
    detail = `Latest candidate: ${candidateStateLabel(latest.state)}.`;
  }

  return (
    <div className={`ocr-summary-candidate state-${tone}`}>
      {icon}
      <div><strong>{label}</strong><span>{detail}</span></div>
    </div>
  );
}

export default function OcrSummaryPanel({
  documentId,
  state,
  inspectHref,
  onInspect,
}: {
  documentId: number;
  state?: OcrSummaryState;
  inspectHref?: string;
  onInspect?: () => void;
}) {
  const internalState = useOcrSummary(state ? null : documentId);
  const summary = state ?? internalState;
  const assessment = summary.assessment;
  const stale = isOcrSummaryStale(assessment);
  const reasons = useMemo(() => (assessment?.reasons ?? []).slice(0, 3), [assessment?.reasons]);
  const href = inspectHref ?? `#/ocr-quality/documents/${documentId}`;

  if (summary.loading) return <SkeletonLoader variant="detail-panel" />;

  if (summary.assessmentError) {
    return <ErrorState message={`OCR assessment unavailable: ${summary.assessmentError}`} onRetry={summary.reload} />;
  }

  return (
    <section className="ocr-summary" aria-label="OCR summary">
      <div className="ocr-summary-heading">
        <div>
          <h3>OCR assessment</h3>
          <p>Current quality, candidate state, and searchable text.</p>
        </div>
        {onInspect ? (
          <Button variant="primary" size="sm" onClick={onInspect}>
            <FileSearch size={16} /> Inspect OCR
          </Button>
        ) : (
          <a className="btn primary" href={href}>
            <FileSearch size={16} /> Inspect OCR
          </a>
        )}
      </div>

      {!assessment ? (
        <EmptyState
          title="No OCR assessment yet"
          desc="Searchable text may be available, but the multidimensional OCR scorer has not assessed this document."
        />
      ) : (
        <>
          <div className={`ocr-summary-status tone-${statusTone(assessment.review_status)}`}>
            <div>
              <strong>{statusLabel(assessment.review_status)}</strong>
              <span>Overall status</span>
            </div>
            <p>
              {assessment.review_status === 'GOOD' && 'OCR appears readable for people and reliable for machine extraction.'}
              {assessment.review_status === 'UNCERTAIN' && 'The available signals disagree or are incomplete; inspect before relying on OCR.'}
              {assessment.review_status === 'REVIEW_RECOMMENDED' && 'The assessment found quality concerns that merit human review.'}
              {assessment.review_status === 'FAILED' && 'The OCR assessment could not produce a reliable result.'}
              {!assessment.review_status && 'No overall status was recorded.'}
            </p>
          </div>

          {(stale || assessment.accepted_candidate_pending_invalidation) ? (
            <div className="ocr-summary-callout" role="alert">
              <AlertTriangle size={17} />
              <div>
                <strong>
                  {assessment.accepted_candidate_pending_invalidation
                    ? 'Accepted OCR is still propagating'
                    : 'Assessment predates the accepted OCR'}
                </strong>
                <span>
                  {assessment.accepted_candidate_pending_invalidation
                    ? 'Downstream analysis has not finished invalidating. Do not treat this assessment as current.'
                    : 'Scores and reasons describe the pre-acceptance version. Re-run deep analysis for current scores.'}
                </span>
              </div>
            </div>
          ) : null}

          <div className="ocr-summary-scores">
            <div><strong>{formatScore(assessment.overlay_score)}</strong><span>Overlay / readability</span></div>
            <div><strong>{formatScore(assessment.machine_score)}</strong><span>Machine extraction</span></div>
          </div>

          <dl className="ocr-summary-meta">
            <div><dt>Assessed</dt><dd>{formatDateTime(assessment.assessed_at)}</dd></div>
            <div><dt>Scorer</dt><dd>{assessment.quality_scorer_version || 'Not recorded'}</dd></div>
            <div>
              <dt>Profile</dt>
              <dd>
                {assessment.document_profile
                  ? [
                    `${assessment.document_profile.page_count} page${assessment.document_profile.page_count === 1 ? '' : 's'}`,
                    assessment.document_profile.dominant_classification,
                    assessment.document_profile.content_shape,
                    assessment.document_profile.language_hint,
                  ].filter(Boolean).join(' · ')
                  : 'No page profile available'}
              </dd>
            </div>
          </dl>

          <CandidateState assessment={assessment} candidates={summary.candidates} />
          {summary.candidateError ? (
            <p className="ocr-summary-inline-error">Candidate state unavailable: {summary.candidateError}</p>
          ) : null}

          <div className="ocr-summary-reasons">
            <div><strong>Top reasons</strong><span>{assessment.reasons.length > 3 ? `Showing 3 of ${assessment.reasons.length}` : `${assessment.reasons.length} recorded`}</span></div>
            {reasons.length ? (
              <ul>
                {reasons.map((reason) => (
                  <li key={`${reason.code}-${reason.message}`} className={`severity-${reason.severity}`}>
                    <span>{reason.severity}</span>
                    <p>{reason.message}</p>
                  </li>
                ))}
              </ul>
            ) : <p className="ocr-summary-muted">No explainability reasons were recorded.</p>}
          </div>
        </>
      )}

      <div className="ocr-summary-text">
        <div>
          <strong>Searchable text preview</strong>
          <span>{summary.text ? `${summary.text.length.toLocaleString()} characters` : 'No text available'}</span>
        </div>
        {summary.textError ? (
          <ErrorState message={`Searchable text unavailable: ${summary.textError}`} onRetry={summary.reload} />
        ) : (
          <textarea readOnly value={summary.text || 'No searchable OCR text is available for this document.'} />
        )}
      </div>

      <p className="ocr-summary-scope-note">
        OCR decisions update document text only. They do not file this document or complete any action.
      </p>

      <a className="ocr-summary-detail-link" href={href}>
        Open scores, page inspection, and candidate comparison <ExternalLink size={14} />
      </a>
    </section>
  );
}
