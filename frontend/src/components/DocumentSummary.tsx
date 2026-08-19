import type { DocumentSummaryModel } from '../lib/api';
import './document-summary.css';

export interface DocumentSummaryProps {
  summary: DocumentSummaryModel;
  density?: 'compact' | 'review';
  className?: string;
}

function formatDate(value?: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(`${value.length === 10 ? `${value}T00:00:00` : value}`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString();
}

export function documentSummaryLabel(summary: DocumentSummaryModel): string {
  const identity = summary.title?.trim() || `Document ${summary.document_id}`;
  const context = [
    summary.correspondent,
    summary.document_type,
    formatDate(summary.document_date),
    summary.account_identifier_display,
  ].filter(Boolean);
  return context.length ? `${identity}, ${context.join(', ')}` : identity;
}

export default function DocumentSummary({
  summary,
  density = 'compact',
  className = '',
}: DocumentSummaryProps) {
  const title = summary.title?.trim() || `Document ${summary.document_id}`;
  const date = formatDate(summary.document_date);
  const metadata = [
    summary.correspondent,
    summary.document_type,
    date,
  ].filter((value): value is string => Boolean(value));

  return (
    <div
      className={`document-summary document-summary-${density} ${className}`.trim()}
      aria-label={documentSummaryLabel(summary)}
    >
      <div className="document-summary-title">{title}</div>
      <div className="document-summary-meta">
        <span>#{summary.document_id}</span>
        {metadata.map((value, index) => <span key={`${value}-${index}`}>{value}</span>)}
      </div>
      {summary.account_identifier_display && (
        <div className="document-summary-account">Account {summary.account_identifier_display}</div>
      )}
      {summary.tags && summary.tags.length > 0 && (
        <div className="document-summary-tags" aria-label="Document tags">
          {summary.tags.map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      )}
    </div>
  );
}
