import type { DocumentSummaryModel } from '../lib/api';

export function formatDocumentSummaryDate(value?: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString();
}

export function documentSummaryLabel(summary: DocumentSummaryModel): string {
  const identity = summary.title?.trim() || `Document ${summary.document_id}`;
  const context = [
    summary.correspondent,
    summary.document_type,
    formatDocumentSummaryDate(summary.document_date),
    summary.account_identifier_display,
    summary.patient_name,
  ].filter(Boolean);
  return context.length ? `${identity}, ${context.join(', ')}` : identity;
}
