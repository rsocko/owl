import type { DocumentSummaryModel } from '../lib/api';
import {
  documentSummaryLabel,
  formatDocumentSummaryDate,
} from './documentSummaryFormat';
import './document-summary.css';

export interface DocumentSummaryProps {
  summary: DocumentSummaryModel;
  density?: 'compact' | 'review';
  className?: string;
}

export default function DocumentSummary({
  summary,
  density = 'compact',
  className = '',
}: DocumentSummaryProps) {
  const title = summary.title?.trim() || `Document ${summary.document_id}`;
  const date = formatDocumentSummaryDate(summary.document_date);
  const metadata = [
    summary.correspondent,
    summary.document_type,
    date && `${summary.date_label || 'Date'} ${date}`,
  ].filter((value): value is string => Boolean(value));

  return (
    <div
      className={`document-summary document-summary-${density} ${className}`.trim()}
      aria-label={documentSummaryLabel(summary)}
    >
      <div className="document-summary-title">{title}</div>
      <div className="document-summary-meta">
        <span>#{summary.document_id}</span>
        {metadata.map((value) => <span key={value}>{value}</span>)}
      </div>
      {summary.account_identifier_display && (
        <div className="document-summary-sensitive">
          Account {summary.account_identifier_display}
        </div>
      )}
      {summary.patient_name && (
        <div className="document-summary-sensitive">Patient {summary.patient_name}</div>
      )}
      {summary.tags && summary.tags.length > 0 && (
        <div className="document-summary-tags" aria-label="Document tags">
          {summary.tags.map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      )}
    </div>
  );
}
