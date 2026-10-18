export type CandidateSummary = {
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

export type ComparisonSummary = {
  comparison_id: string;
  blocking_findings: string[];
  text_diff_summary: Record<string, unknown>;
  overlay_score_delta?: number | null;
  machine_score_delta?: number | null;
  content_score_delta?: number | null;
  performed_at?: string | null;
};

export type CandidateDetail = CandidateSummary & {
  comparison: ComparisonSummary | null;
  failure_reason?: string | null;
  decision_reason?: string | null;
  decided_at?: string | null;
  actor?: string | null;
};

export const OCR_ENGINE_LABELS: Record<string, string> = {
  'ocrmypdf-tesseract-5': 'OCRmyPDF / Tesseract 5',
  'azure-prebuilt-layout': 'Azure Document Intelligence (prebuilt-layout)',
};
