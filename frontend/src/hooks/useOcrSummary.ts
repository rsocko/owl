import { useCallback, useEffect, useState } from 'react';
import { endpoints } from '../lib/api';

export type OcrAssessmentStatus = 'GOOD' | 'UNCERTAIN' | 'REVIEW_RECOMMENDED' | 'FAILED';

export interface OcrReason {
  code: string;
  message: string;
  severity: 'info' | 'warning' | 'blocking';
  component: string;
}

export interface OcrPageProfile {
  page_number: number;
  classification: string;
  word_count: number;
  error?: string | null;
}

export interface OcrDocumentProfile {
  page_count: number;
  pages?: OcrPageProfile[];
  dominant_classification?: string | null;
  content_shape?: string | null;
  language_hint?: string | null;
}

export interface OcrAssessment {
  document_id: number;
  overlay_score?: number | null;
  machine_score?: number | null;
  review_status?: OcrAssessmentStatus | string | null;
  quality_scorer_version?: string | null;
  assessed_at?: string | null;
  reasons: OcrReason[];
  document_profile?: OcrDocumentProfile | null;
  has_stage2_analysis?: boolean;
  has_accepted_ocr_candidate?: boolean;
  accepted_candidate_at?: string | null;
  accepted_candidate_pending_invalidation?: boolean;
}

export interface OcrCandidateSummary {
  candidate_id: string;
  state: string;
  engine: string;
  requested_at?: string | null;
  completed_at?: string | null;
}

export interface OcrSummaryState {
  assessment: OcrAssessment | null;
  candidates: OcrCandidateSummary[];
  text: string;
  loading: boolean;
  assessmentError: string | null;
  candidateError: string | null;
  textError: string | null;
  reload: () => void;
}

const ATTENTION_STATUSES = new Set(['UNCERTAIN', 'REVIEW_RECOMMENDED', 'FAILED']);

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function isNotFound(error: unknown): boolean {
  return error instanceof Error && 'status' in error && (error as { status?: number }).status === 404;
}

async function loadOrCreateAssessment(documentId: number): Promise<unknown> {
  try {
    return await endpoints.ocrQuality.documentDetail(documentId);
  } catch (error) {
    if (!isNotFound(error)) throw error;
    return endpoints.ocrQuality.assessDocument(documentId);
  }
}

function dateValue(value?: string | null): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

export function isOcrSummaryStale(assessment: OcrAssessment | null): boolean {
  if (!assessment?.has_accepted_ocr_candidate) return false;
  const assessedAt = dateValue(assessment.assessed_at);
  const acceptedAt = dateValue(assessment.accepted_candidate_at);
  return assessedAt != null && acceptedAt != null && assessedAt < acceptedAt;
}

export function ocrNeedsAttention(assessment: OcrAssessment | null): boolean {
  return Boolean(
    assessment?.accepted_candidate_pending_invalidation
    || isOcrSummaryStale(assessment)
    || ATTENTION_STATUSES.has(assessment?.review_status || ''),
  );
}

export function useOcrSummary(documentId: number | null): OcrSummaryState {
  const [assessment, setAssessment] = useState<OcrAssessment | null>(null);
  const [candidates, setCandidates] = useState<OcrCandidateSummary[]>([]);
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(Boolean(documentId));
  const [assessmentError, setAssessmentError] = useState<string | null>(null);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  const reload = useCallback(() => setRevision((current) => current + 1), []);

  useEffect(() => {
    if (documentId == null) {
      setAssessment(null);
      setCandidates([]);
      setText('');
      setLoading(false);
      setAssessmentError(null);
      setCandidateError(null);
      setTextError(null);
      return undefined;
    }

    let active = true;
    setAssessment(null);
    setCandidates([]);
    setText('');
    setLoading(true);
    setAssessmentError(null);
    setCandidateError(null);
    setTextError(null);

    void Promise.allSettled([
      loadOrCreateAssessment(documentId),
      endpoints.ocrQuality.candidates.list({ document_id: documentId }),
      endpoints.intake.documentOcr(documentId),
    ]).then(([assessmentResult, candidatesResult, textResult]) => {
      if (!active) return;

      if (assessmentResult.status === 'fulfilled') {
        setAssessment(assessmentResult.value as OcrAssessment);
      } else if (!isNotFound(assessmentResult.reason)) {
        setAssessmentError(errorMessage(assessmentResult.reason, 'Failed to load OCR assessment.'));
      }

      if (candidatesResult.status === 'fulfilled') {
        setCandidates(
          ((candidatesResult.value as { candidates?: OcrCandidateSummary[] }).candidates ?? []),
        );
      } else {
        setCandidateError(errorMessage(candidatesResult.reason, 'Failed to load OCR candidates.'));
      }

      if (textResult.status === 'fulfilled') {
        setText((textResult.value as { text?: string | null }).text ?? '');
      } else {
        setTextError(errorMessage(textResult.reason, 'Failed to load searchable OCR text.'));
      }
    }).finally(() => {
      if (active) setLoading(false);
    });

    return () => {
      active = false;
    };
  }, [documentId, revision]);

  return {
    assessment,
    candidates,
    text,
    loading,
    assessmentError,
    candidateError,
    textError,
    reload,
  };
}
