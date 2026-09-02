import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Badge, Breadcrumb, Button, Card, EmptyState, ErrorState, PageHeader, SkeletonLoader, type Tone } from '../components/ui';
import DocumentPreview from '../components/DocumentPreview';
import OcrCandidatesPanel from '../components/OcrCandidatesPanel';
import OcrOverlayComparisonPanel from '../components/OcrOverlayComparisonPanel';
import RegionOverlayViewer, { type Annotation, type DrawnBox, type PageRegions } from '../components/RegionOverlayViewer';
import AnnotationListPanel from '../components/AnnotationListPanel';
import { endpoints } from '../lib/api';
import { statusTone, formatDate } from './OcrQualityDashboard';
import { formatScore } from './OcrQualityReviewQueue';
import '../styles/ocr-quality.css';

/* ── Types (exported for testing) ── */

export type Reason = {
  code: string;
  message: string;
  severity: 'info' | 'warning' | 'blocking';
  component: string;
  weight?: number | null;
  value?: number | null;
};

export type PageProfile = {
  page_number: number;
  classification: string;
  text_coverage?: number | null;
  image_coverage?: number | null;
  char_count: number;
  word_count: number;
  rotation: number;
  error?: string | null;
};

export type DocumentProfile = {
  page_count: number;
  pages?: PageProfile[];
  dominant_classification?: string | null;
  content_shape?: string;
  language_hint?: string | null;
  producer?: string | null;
  is_short_document?: boolean;
  has_pdf_geometry?: boolean;
};

export type DocumentDetail = {
  document_id: number;
  document_type?: string | null;
  correspondent?: string | null;
  document_created?: string | null;
  overlay_score?: number | null;
  machine_score?: number | null;
  review_status?: string | null;
  downstream_outcome?: string | null;
  quality_scorer_version?: string | null;
  assessed_at?: string | null;
  preliminary_score?: number | null;
  legacy_action_queue_score?: number | null;
  reasons: Reason[];
  document_profile?: DocumentProfile | null;
};

const SEVERITY_TONE: Record<string, Tone> = { info: 'info', warning: 'warn', blocking: 'err' };

function ReasonList({ reasons }: { reasons: Reason[] }) {
  if (reasons.length === 0) {
    return <div className="text-muted">No explainability reasons recorded.</div>;
  }
  return (
    <ul className="ocr-reason-list">
      {reasons.map((reason, i) => (
        <li key={`${reason.code}-${i}`} className="ocr-reason-item">
          <Badge tone={SEVERITY_TONE[reason.severity] ?? 'muted'}>{reason.severity}</Badge>
          <span className="ocr-reason-code">{reason.code}</span>
          <span className="ocr-reason-message">{reason.message}</span>
        </li>
      ))}
    </ul>
  );
}

function PageProfileTable({ pages }: { pages: PageProfile[] }) {
  if (pages.length === 0) {
    return <div className="text-muted">No per-page profile signal is available for this document.</div>;
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Page</th>
          <th>Classification</th>
          <th>Text coverage</th>
          <th>Image coverage</th>
          <th>Words</th>
          <th>Rotation</th>
        </tr>
      </thead>
      <tbody>
        {pages.map((page) => (
          <tr key={page.page_number}>
            <td>{page.page_number}</td>
            <td>{page.classification}{page.error ? ` (${page.error})` : ''}</td>
            <td>{page.text_coverage == null ? '—' : `${Math.round(page.text_coverage * 100)}%`}</td>
            <td>{page.image_coverage == null ? '—' : `${Math.round(page.image_coverage * 100)}%`}</td>
            <td>{page.word_count}</td>
            <td>{page.rotation}°</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function OcrQualityDocumentDetail() {
  const { documentId } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [paperlessUrl, setPaperlessUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Region-level inspection (issue #134) — fetched on-demand only when this
  // detail page is open, never pre-computed for the whole corpus.
  const [inspectionPage, setInspectionPage] = useState(1);
  const [regions, setRegions] = useState<PageRegions | null>(null);
  const [regionsError, setRegionsError] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);

  // Force Stage-2 analysis (one-off per-document trigger, distinct from the
  // corpus-wide random stratified sample).
  const [forcingStage2, setForcingStage2] = useState(false);
  const [stage2Error, setStage2Error] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!documentId) return;
    setLoading(true);
    setError(null);
    setNotFound(false);
    Promise.all([
      endpoints.ocrQuality.documentDetail(documentId),
      endpoints.statements.paperlessUrl().catch(() => ({ paperless_url: null })),
    ])
      .then(([detailData, paperlessData]) => {
        setDetail(detailData as DocumentDetail);
        setPaperlessUrl((paperlessData as { paperless_url?: string | null })?.paperless_url ?? null);
      })
      .catch((err) => {
        if (err?.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load document assessment.');
        }
      })
      .finally(() => setLoading(false));
  }, [documentId]);

  useEffect(() => {
    load();
  }, [load]);

  // Load word-region geometry for the current inspection page on demand.
  useEffect(() => {
    if (!documentId) return;
    let cancelled = false;
    setRegionsError(null);
    endpoints.ocrQuality
      .regions(documentId, inspectionPage)
      .then((data) => {
        if (!cancelled) setRegions(data as PageRegions);
      })
      .catch((err) => {
        if (!cancelled) {
          setRegions(null);
          setRegionsError(err instanceof Error ? err.message : 'Failed to load region geometry.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, inspectionPage]);

  // Load saved annotations for the whole document (all pages), so the list
  // panel can show every annotation while the overlay filters to the
  // currently displayed page.
  const loadAnnotations = useCallback(() => {
    if (!documentId) return;
    endpoints.ocrQuality.annotations
      .list(documentId)
      .then((data) => setAnnotations((data as { annotations: Annotation[] }).annotations))
      .catch(() => setAnnotations([]));
  }, [documentId]);

  useEffect(() => {
    loadAnnotations();
  }, [loadAnnotations]);

  const handleCreateAnnotation = useCallback(
    async (box: DrawnBox & { label: string; note: string | null }) => {
      if (!documentId) return;
      const created = (await endpoints.ocrQuality.annotations.create(documentId, {
        page: inspectionPage,
        ...box,
      })) as Annotation;
      setAnnotations((prev) => [...prev, created]);
    },
    [documentId, inspectionPage],
  );

  const handleUpdateAnnotation = useCallback(
    async (annotationId: number, updates: Partial<Pick<Annotation, 'label' | 'note'>>) => {
      if (!documentId) return;
      const updated = (await endpoints.ocrQuality.annotations.update(documentId, annotationId, updates)) as Annotation;
      setAnnotations((prev) => prev.map((a) => (a.id === annotationId ? updated : a)));
    },
    [documentId],
  );

  const handleDeleteAnnotation = useCallback(
    async (annotationId: number) => {
      if (!documentId) return;
      await endpoints.ocrQuality.annotations.remove(documentId, annotationId);
      setAnnotations((prev) => prev.filter((a) => a.id !== annotationId));
    },
    [documentId],
  );

  const pageAnnotations = annotations.filter((a) => a.page === inspectionPage);

  const handleForceStage2 = useCallback(() => {
    if (!documentId) return;
    setForcingStage2(true);
    setStage2Error(null);
    endpoints.ocrQuality
      .forceStage2(documentId)
      .then((data) => setDetail(data as DocumentDetail))
      .catch((err) => setStage2Error(err instanceof Error ? err.message : 'Failed to force Stage 2 analysis.'))
      .finally(() => setForcingStage2(false));
  }, [documentId]);

  return (
    <div className="ocr-quality-document-detail">
      <Breadcrumb items={[{ label: 'OCR Quality', to: '/ocr-quality' }, { label: 'Review queue', to: '/ocr-quality/queue' }, { label: `Document #${documentId}` }]} />
      <PageHeader
        title={`Document #${documentId}`}
        desc="OCR quality assessment detail"
        actions={
          <Button variant="ghost" size="sm" onClick={() => navigate('/ocr-quality/queue')}>
            ← Back to review queue
          </Button>
        }
      />

      {loading && <SkeletonLoader variant="detail-panel" />}
      {!loading && notFound && (
        <EmptyState icon="🔍" title="No assessment found" desc="This document has not been scanned by the OCR quality inventory yet." />
      )}
      {!loading && !notFound && error && <ErrorState message={error} onRetry={load} />}
      {!loading && !notFound && !error && detail && (
        <>
          <Card
            title="Metadata"
            actions={
              paperlessUrl ? (
                <a href={`${paperlessUrl}/documents/${detail.document_id}/details`} target="_blank" rel="noreferrer" className="btn ghost">
                  Open in Paperless ↗
                </a>
              ) : null
            }
          >
            <div className="ocr-metadata-grid">
              <div><span className="text-muted">Type:</span> {detail.document_type ?? '—'}</div>
              <div><span className="text-muted">Correspondent:</span> {detail.correspondent ?? '—'}</div>
              <div><span className="text-muted">Created:</span> {detail.document_created ?? '—'}</div>
              <div><span className="text-muted">Downstream outcome:</span> {detail.downstream_outcome ?? '—'}</div>
              <div><span className="text-muted">Assessed:</span> {formatDate(detail.assessed_at)}</div>
              <div><span className="text-muted">Scorer version:</span> {detail.quality_scorer_version ?? 'not yet scored'}</div>
            </div>
          </Card>

          <Card title="Document preview">
            <DocumentPreview
              documentId={detail.document_id}
              paperlessUrl={paperlessUrl ? `${paperlessUrl}/documents/${detail.document_id}/details` : null}
              variant="card"
              label="Document"
            />
          </Card>

          <Card title="Region inspection">
            {regionsError && <ErrorState message={regionsError} />}
            {!regionsError && (
              <>
                <RegionOverlayViewer
                  imageUrl={endpoints.ocrQuality.pageImageUrl(detail.document_id, inspectionPage)}
                  regions={regions}
                  annotations={pageAnnotations}
                  onCreateAnnotation={handleCreateAnnotation}
                  onDeleteAnnotation={handleDeleteAnnotation}
                  onPageChange={setInspectionPage}
                />
                <div className="ocr-annotation-panel-heading">Saved annotations</div>
                <AnnotationListPanel
                  annotations={annotations}
                  onUpdate={handleUpdateAnnotation}
                  onDelete={handleDeleteAnnotation}
                  onSelectPage={setInspectionPage}
                />
              </>
            )}
          </Card>

          <Card title="Scores">
            <div className="ocr-score-summary">
              <Badge tone={statusTone(detail.review_status ?? 'unscored') as Tone}>{detail.review_status ?? 'unscored'}</Badge>
              <span>Overlay/readability: <strong>{formatScore(detail.overlay_score)}</strong></span>
              <span>Machine-extraction: <strong>{formatScore(detail.machine_score)}</strong></span>
              {detail.preliminary_score != null && (
                <span className="text-muted">Stage-1 preliminary heuristic: {detail.preliminary_score}</span>
              )}
            </div>
            {detail.overlay_score == null && (
              <div className="ocr-unavailable-note">
                Overlay score is unavailable — this document has not yet been PDF-profiled by the Stage 2 stratified sample.
              </div>
            )}
            <div className="ocr-force-stage2">
              <Button variant="primary" size="sm" onClick={handleForceStage2} disabled={forcingStage2}>
                {forcingStage2 ? 'Analyzing…' : 'Force Stage 2 analysis'}
              </Button>
            </div>
            {stage2Error && <div className="ocr-unavailable-note">{stage2Error}</div>}
          </Card>

          <Card title="Explainable reasons">
            <ReasonList reasons={detail.reasons} />
          </Card>

          <Card title="Page profile">
            {detail.document_profile ? (
              <>
                <div className="ocr-profile-summary">
                  <span>Pages: {detail.document_profile.page_count}</span>
                  <span>Dominant classification: {detail.document_profile.dominant_classification ?? '—'}</span>
                  <span>Content shape: {detail.document_profile.content_shape ?? '—'}</span>
                </div>
                <PageProfileTable pages={detail.document_profile.pages ?? []} />
              </>
            ) : (
              <div className="text-muted">No document profile available.</div>
            )}
          </Card>

          <Card title="Candidate comparison">
            <div className="ocr-stub-note">
              Applying an accepted candidate as the new Paperless version, version preservation, and
              rollback are not yet available (blocked on issue #114). Candidate generation and
              comparison below never modify the live Paperless document.
            </div>
            <OcrOverlayComparisonPanel documentId={detail.document_id} />
          </Card>

          <OcrCandidatesPanel documentId={detail.document_id} />
        </>
      )}
    </div>
  );
}
