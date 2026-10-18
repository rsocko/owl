import { ArrowLeft, RefreshCw, Settings2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { endpoints } from '../lib/api';
import AnnotationListPanel from './AnnotationListPanel';
import OcrCandidatesPanel from './OcrCandidatesPanel';
import OcrOverlayComparisonPanel from './OcrOverlayComparisonPanel';
import { OCR_ENGINE_LABELS, type CandidateDetail, type CandidateSummary } from './ocrCandidateTypes';
import RegionOverlayViewer, {
  type Annotation,
  type DrawnBox,
  type PageRegions,
} from './RegionOverlayViewer';
import { Badge, Button, EmptyState, ErrorState, SkeletonLoader, Toast } from './ui';
import '../styles/ocr-focus-inspector.css';

type InspectorView = 'visual' | 'text' | 'findings';

type Reason = {
  code: string;
  message: string;
  severity: 'info' | 'warning' | 'blocking';
};

type DocumentDetail = {
  document_id: number;
  document_type?: string | null;
  correspondent?: string | null;
  document_created?: string | null;
  overlay_score?: number | null;
  machine_score?: number | null;
  review_status?: string | null;
  quality_scorer_version?: string | null;
  assessed_at?: string | null;
  reasons: Reason[];
  document_profile?: {
    page_count: number;
    dominant_classification?: string | null;
    content_shape?: string | null;
    pages?: Array<{
      page_number: number;
      classification: string;
      word_count: number;
      rotation: number;
    }>;
  } | null;
  has_stage2_analysis?: boolean;
  has_accepted_ocr_candidate?: boolean;
  accepted_candidate_at?: string | null;
  accepted_candidate_pending_invalidation?: boolean;
};

type CorrectableField = {
  field_name: string;
  value: string | number | boolean | null;
  has_value?: boolean;
};

const ACTOR_STORAGE_KEY = 'owl_ocr_actor_name';
const ACTIVE_STATES = new Set(['requested', 'running']);
const ACCEPTED_STATES = new Set(['accepted', 'accepted_pending_invalidation']);

function score(value?: number | null) {
  return value == null ? '—' : Math.round(value).toString();
}

function delta(value?: number | null) {
  if (value == null) return '—';
  return `${value > 0 ? '+' : ''}${value}`;
}

function fieldLabel(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function OcrFocusInspector({
  documentId,
  contextLabel = 'OCR quality review',
  onReturn,
}: {
  documentId: number;
  contextLabel?: string;
  onReturn?: () => void;
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [regions, setRegions] = useState<PageRegions | null>(null);
  const [regionsError, setRegionsError] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [candidates, setCandidates] = useState<CandidateSummary[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<CandidateDetail | null>(null);
  const [candidateText, setCandidateText] = useState<{ current_text: string | null; candidate_text: string | null } | null>(null);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [view, setView] = useState<InspectorView>('visual');
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actorName, setActorName] = useState(() => window.localStorage.getItem(ACTOR_STORAGE_KEY) ?? '');
  const [correctableFields, setCorrectableFields] = useState<CorrectableField[]>([]);
  const [pendingCorrection, setPendingCorrection] = useState<DrawnBox | null>(null);
  const [correctionField, setCorrectionField] = useState('');
  const [correctionValue, setCorrectionValue] = useState('');
  const [toast, setToast] = useState<string | null>(null);
  const [candidateRefreshKey, setCandidateRefreshKey] = useState(0);

  const loadDetail = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDetail(await endpoints.ocrQuality.documentDetail(documentId) as DocumentDetail);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load OCR assessment.');
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  useEffect(() => {
    let active = true;
    setRegionsError(null);
    void endpoints.ocrQuality.regions(documentId, page)
      .then((response) => {
        if (active) setRegions(response as PageRegions);
      })
      .catch((loadError) => {
        if (active) {
          setRegions(null);
          setRegionsError(loadError instanceof Error ? loadError.message : 'Failed to load page regions.');
        }
      });
    return () => {
      active = false;
    };
  }, [documentId, page]);

  const loadAnnotations = useCallback(() => {
    void endpoints.ocrQuality.annotations.list(documentId)
      .then((response) => setAnnotations((response as { annotations: Annotation[] }).annotations))
      .catch(() => setAnnotations([]));
  }, [documentId]);

  useEffect(() => {
    loadAnnotations();
    void endpoints.metadata.get(documentId)
      .then((response) => setCorrectableFields((response as { extracted_fields?: CorrectableField[] }).extracted_fields ?? []))
      .catch(() => setCorrectableFields([]));
  }, [documentId, loadAnnotations]);

  useEffect(() => {
    if (!selectedCandidateId) {
      setCandidate(null);
      setCandidateText(null);
      return;
    }
    let active = true;
    setCandidateError(null);
    void Promise.all([
      endpoints.ocrQuality.candidates.get(selectedCandidateId),
      endpoints.ocrQuality.candidates.text(selectedCandidateId).catch(() => null),
    ]).then(([candidateResponse, textResponse]) => {
      if (!active) return;
      setCandidate(candidateResponse as CandidateDetail);
      setCandidateText(textResponse as { current_text: string | null; candidate_text: string | null } | null);
    }).catch((loadError) => {
      if (active) setCandidateError(loadError instanceof Error ? loadError.message : 'Failed to load candidate evidence.');
    });
    return () => {
      active = false;
    };
  }, [selectedCandidateId]);

  useEffect(() => {
    window.localStorage.setItem(ACTOR_STORAGE_KEY, actorName);
  }, [actorName]);

  const refreshSelectedCandidate = useCallback(async () => {
    if (!selectedCandidateId) return;
    setCandidate(await endpoints.ocrQuality.candidates.get(selectedCandidateId) as CandidateDetail);
  }, [selectedCandidateId]);

  const runAction = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setActionError(null);
    try {
      await action();
      await Promise.all([loadDetail(), refreshSelectedCandidate()]);
      setCandidateRefreshKey((current) => current + 1);
    } catch (actionFailure) {
      setActionError(actionFailure instanceof Error ? actionFailure.message : `Failed to ${key}.`);
    } finally {
      setBusy(null);
    }
  };

  const actor = actorName.trim();
  const blocked = Boolean(candidate?.comparison?.blocking_findings.length);
  const freshness = detail?.accepted_candidate_pending_invalidation
    ? 'Downstream invalidation pending'
    : detail?.has_accepted_ocr_candidate
      ? 'Scores describe the pre-acceptance version'
      : detail?.assessed_at
        ? `Assessed ${new Date(detail.assessed_at).toLocaleString()}`
        : 'Not yet assessed';

  const suggestedRead = useMemo(() => {
    if (!candidate?.comparison) return 'No comparison signal is available.';
    if (candidate.comparison.blocking_findings.length) return 'Resolve the blocking findings before accepting this candidate.';
    if ((candidate.comparison.content_score_delta ?? 0) >= 3) return 'Content accuracy looks improved; verify the visual placement before deciding.';
    if ((candidate.comparison.overlay_score_delta ?? 0) <= -3) return 'Text may improve, but box placement appears less precise.';
    return 'No strong signal either way; compare both sources before deciding.';
  }, [candidate]);

  if (loading) return <SkeletonLoader variant="detail-panel" />;
  if (error) return <ErrorState message={error} onRetry={() => void loadDetail()} />;
  if (!detail) return <EmptyState title="No OCR assessment" desc="This document has not been assessed yet." />;

  return (
    <section className="ocr-focus" aria-label="OCR focus inspector">
      <header className="ocr-focus-header">
        <div>
          <span>{contextLabel} · Paperless #{documentId}</span>
          <h2>OCR focus inspector</h2>
          <p>{detail.correspondent || 'Unknown correspondent'} · {detail.document_type || 'Document type not set'} · {detail.document_created || 'Date not set'}</p>
        </div>
        <Button variant="ghost" onClick={onReturn}><ArrowLeft size={16} /> Return to review</Button>
      </header>

      <div className="ocr-focus-layout">
        <aside className="ocr-focus-candidates" aria-label="OCR candidates">
          <OcrCandidatesPanel
            compact
            documentId={documentId}
            hasStage2Analysis={detail.has_stage2_analysis}
            currentOverlayScore={detail.overlay_score}
            currentMachineScore={detail.machine_score}
            selectedCandidateId={selectedCandidateId}
            onSelectCandidate={setSelectedCandidateId}
            onCandidatesChange={setCandidates}
            refreshKey={candidateRefreshKey}
          />
        </aside>

        <main className="ocr-focus-canvas">
          <nav className="ocr-focus-view-tabs" aria-label="Inspector view">
            {(['visual', 'text', 'findings'] as const).map((value) => (
              <button key={value} className={view === value ? 'active' : ''} onClick={() => setView(value)}>
                {value === 'visual' ? 'Visual' : value === 'text' ? 'Text diff' : 'Page findings'}
              </button>
            ))}
          </nav>

          {candidateError && <ErrorState message={candidateError} />}
          {view === 'visual' && selectedCandidateId ? (
            <OcrOverlayComparisonPanel documentId={documentId} candidateId={selectedCandidateId} lockedSources />
          ) : null}
          {view === 'visual' && !selectedCandidateId ? (
            <>
              {regionsError ? <ErrorState message={regionsError} /> : (
                <RegionOverlayViewer
                  imageUrl={endpoints.ocrQuality.pageImageUrl(documentId, page)}
                  regions={regions}
                  annotations={annotations.filter((annotation) => annotation.page === page)}
                  onPageChange={setPage}
                  zoom={zoom}
                  onZoomChange={setZoom}
                  onCreateAnnotation={async (box) => {
                    await endpoints.ocrQuality.annotations.create(documentId, { page, ...box });
                    loadAnnotations();
                  }}
                  onDeleteAnnotation={async (annotationId) => {
                    await endpoints.ocrQuality.annotations.remove(documentId, annotationId);
                    loadAnnotations();
                  }}
                  onCorrectRegion={setPendingCorrection}
                />
              )}
              {pendingCorrection && (
                <div className="ocr-focus-correction">
                  <strong>Correct metadata from selected region</strong>
                  <select value={correctionField} onChange={(event) => {
                    setCorrectionField(event.target.value);
                    const field = correctableFields.find((item) => item.field_name === event.target.value);
                    setCorrectionValue(field?.value == null ? '' : String(field.value));
                  }}>
                    <option value="">Select a field…</option>
                    {correctableFields.map((field) => <option key={field.field_name} value={field.field_name}>{fieldLabel(field.field_name)}</option>)}
                  </select>
                  <input aria-label="Corrected value" value={correctionValue} onChange={(event) => setCorrectionValue(event.target.value)} />
                  <Button size="sm" disabled={!correctionField || !correctionValue.trim()} onClick={() => void runAction('submit correction', async () => {
                    await endpoints.metadata.correct(documentId, {
                      field_name: correctionField,
                      corrected_value: correctionValue.trim(),
                      source_region: { page, ...pendingCorrection },
                      notes: 'Submitted from OCR focus inspector',
                    });
                    setPendingCorrection(null);
                    setCorrectionField('');
                    setCorrectionValue('');
                    setToast('Metadata correction submitted.');
                  })}>Submit correction</Button>
                  <Button size="sm" variant="ghost" onClick={() => setPendingCorrection(null)}>Cancel</Button>
                </div>
              )}
              <details className="ocr-focus-disclosure">
                <summary>Annotations</summary>
                <AnnotationListPanel annotations={annotations} onDelete={async (id) => {
                  await endpoints.ocrQuality.annotations.remove(documentId, id);
                  loadAnnotations();
                }} onUpdate={async (id, updates) => {
                  await endpoints.ocrQuality.annotations.update(documentId, id, updates);
                  loadAnnotations();
                }} onSelectPage={setPage} />
              </details>
            </>
          ) : null}
          {view === 'text' && (
            selectedCandidateId ? (
              <div className="ocr-focus-text-grid">
                <section><h3>Current Paperless OCR</h3><pre>{candidateText?.current_text || '(no text)'}</pre></section>
                <section><h3>{candidate ? OCR_ENGINE_LABELS[candidate.engine] ?? candidate.engine : 'Candidate OCR'}</h3><pre>{candidateText?.candidate_text || '(no text)'}</pre></section>
              </div>
            ) : <EmptyState title="Select a candidate" desc="Text diff compares the current searchable text with one candidate." />
          )}
          {view === 'findings' && (
            <div className="ocr-focus-findings">
              <section>
                <h3>Assessment reasons</h3>
                {detail.reasons.length ? detail.reasons.map((reason) => (
                  <article key={`${reason.code}-${reason.message}`}><Badge tone={reason.severity === 'blocking' ? 'err' : reason.severity === 'warning' ? 'warn' : 'info'}>{reason.severity}</Badge><p>{reason.message}</p><code>{reason.code}</code></article>
                )) : <p>No assessment reasons were recorded.</p>}
              </section>
              <details className="ocr-focus-disclosure">
                <summary>Full page profile</summary>
                {(detail.document_profile?.pages ?? []).map((profile) => <p key={profile.page_number}>Page {profile.page_number}: {profile.classification} · {profile.word_count} words · {profile.rotation}°</p>)}
              </details>
            </div>
          )}
        </main>

        <aside className="ocr-focus-evidence" aria-label="OCR evidence and decision">
          <div className="ocr-focus-status">
            <Badge tone={detail.review_status === 'GOOD' ? 'ok' : detail.review_status === 'FAILED' ? 'err' : 'warn'}>{detail.review_status || 'Not assessed'}</Badge>
            <strong>{freshness}</strong>
            <span>Scorer {detail.quality_scorer_version || 'not recorded'}</span>
          </div>

          <section>
            <h3>Score matrix</h3>
            <table>
              <thead><tr><th>Signal</th><th>Current</th><th>Candidate</th><th>Δ</th></tr></thead>
              <tbody>
                <tr><td>Overlay</td><td>{score(detail.overlay_score)}</td><td>{score(candidate?.overlay_score)}</td><td>{delta(candidate?.comparison?.overlay_score_delta)}</td></tr>
                <tr><td>Machine</td><td>{score(detail.machine_score)}</td><td>{score(candidate?.machine_score)}</td><td>{delta(candidate?.comparison?.machine_score_delta)}</td></tr>
                <tr><td>Content</td><td>—</td><td>{score(candidate?.content_score)}</td><td>{delta(candidate?.comparison?.content_score_delta)}</td></tr>
              </tbody>
            </table>
            <p className="ocr-focus-suggested">{suggestedRead}</p>
          </section>

          <section>
            <h3>Decision evidence</h3>
            {candidate ? (
              <>
                <dl>
                  <div><dt>Source</dt><dd>{OCR_ENGINE_LABELS[candidate.engine] ?? candidate.engine}</dd></div>
                  <div><dt>Version</dt><dd>{candidate.model_version || 'Not recorded'}</dd></div>
                  <div><dt>State</dt><dd>{candidate.state}</dd></div>
                </dl>
                {candidate.comparison?.blocking_findings.length ? (
                  <div className="ocr-focus-blockers" role="alert"><strong>Acceptance blocked</strong>{candidate.comparison.blocking_findings.map((finding) => <span key={finding}>{finding}</span>)}</div>
                ) : <p>No blocking findings.</p>}
                {candidate.failure_reason ? <ErrorState message={candidate.failure_reason} /> : null}
              </>
            ) : <p>Select a candidate to compare provenance, scores, and findings.</p>}
          </section>

          <details className="ocr-focus-settings">
            <summary><Settings2 size={15} /> Decision details</summary>
            <label>Actor<input value={actorName} onChange={(event) => setActorName(event.target.value)} placeholder="e.g. jsmith" /></label>
            <p>Stored on this device and recorded with accept, reject, retry, and rollback actions.</p>
          </details>
          <details className="ocr-focus-settings">
            <summary>Candidate history</summary>
            {candidates.map((item) => <p key={item.candidate_id}>{OCR_ENGINE_LABELS[item.engine] ?? item.engine} · {item.state} · {item.candidate_id.slice(0, 8)}</p>)}
          </details>
        </aside>
      </div>

      <footer className="ocr-focus-actions">
        <p>OCR decisions do not file this document, complete an action, or refresh downstream analysis automatically.</p>
        <div>
          <Button variant="ghost" onClick={onReturn}>Return to review</Button>
          {!candidates.some((item) => ACTIVE_STATES.has(item.state) || item.state === 'ready') && <Button disabled={busy !== null} onClick={() => void runAction('generate candidate', () => endpoints.ocrQuality.candidates.request({ document_ids: [documentId], engines: ['ocrmypdf-tesseract-5'] }))}>Generate candidate</Button>}
          <Button disabled={busy !== null} onClick={() => void runAction('re-run Stage 2', () => endpoints.ocrQuality.forceStage2(documentId))}>{busy === 're-run Stage 2' ? <RefreshCw className="ocr-summary-spin" size={15} /> : null} Re-run Stage 2</Button>
          {candidate && ACTIVE_STATES.has(candidate.state) && <Button variant="danger" disabled={busy !== null} onClick={() => void runAction('cancel candidate', () => endpoints.ocrQuality.candidates.cancel(candidate.candidate_id))}>Cancel</Button>}
          {candidate?.state === 'ready' && <>
            <Button variant="danger" disabled={busy !== null || !actor} onClick={() => void runAction('reject candidate', () => endpoints.ocrQuality.candidates.decide(candidate.candidate_id, { decision: 'rejected', actor }))}>Reject</Button>
            <Button variant="success" disabled={busy !== null || !actor || blocked} onClick={() => void runAction('accept candidate', () => endpoints.ocrQuality.candidates.decide(candidate.candidate_id, { decision: 'accepted', actor }))}>Accept</Button>
          </>}
          {candidate?.state === 'accepted_pending_invalidation' && <Button disabled={busy !== null || !actor} onClick={() => void runAction('retry invalidation', () => endpoints.ocrQuality.candidates.retryInvalidation(candidate.candidate_id, { actor }))}>Retry invalidation</Button>}
          {candidate && ACCEPTED_STATES.has(candidate.state) && <Button variant="danger" disabled={busy !== null || !actor} onClick={() => {
            if (window.confirm('Roll back to the Paperless version from before this OCR candidate was applied?')) {
              void runAction('roll back', () => endpoints.ocrQuality.rollback(documentId, { target_candidate_id: candidate.candidate_id, actor }));
            }
          }}>Roll back</Button>}
        </div>
        {actionError && <span role="alert">{actionError}</span>}
      </footer>
      {toast && <Toast message={toast} tone="success" onDismiss={() => setToast(null)} />}
    </section>
  );
}
