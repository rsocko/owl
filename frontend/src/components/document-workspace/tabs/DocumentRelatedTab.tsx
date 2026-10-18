import { ExternalLink } from 'lucide-react';
import type { ReactNode } from 'react';
import { endpoints, type DocumentSummaryModel } from '../../../lib/api';
import { EmptyState } from '../../ui';

export interface RelatedDocument {
  document_id: number;
  role?: string | null;
  title?: string | null;
  correspondent?: string | null;
  thumbnail_url?: string | null;
  preview_url?: string | null;
  document_summary?: DocumentSummaryModel;
}

export default function DocumentRelatedTab({
  documents,
  emptyDescription = 'No related documents are attached to this document.',
  children,
}: {
  documents?: RelatedDocument[];
  emptyDescription?: string;
  children?: ReactNode;
}) {
  if (children) {
    return <div className="document-workspace-related-tab">{children}</div>;
  }

  const relatedDocuments = documents ?? [];
  return (
    <div className="workspace-related document-workspace-related-tab">
      <div className="workspace-panel-heading">
        <div>
          <h3>Related evidence</h3>
          <p>Documents connected to this record.</p>
        </div>
      </div>
      {relatedDocuments.length ? relatedDocuments.map((document) => {
        const summary = document.document_summary;
        return (
          <a
            key={document.document_id}
            href={document.preview_url || endpoints.documents.previewUrl(document.document_id)}
            target="_blank"
            rel="noopener noreferrer"
          >
            <img
              src={document.thumbnail_url || endpoints.documents.thumbnailUrl(document.document_id)}
              alt=""
            />
            <span>
              <strong>{summary?.title || document.title || `Document #${document.document_id}`}</strong>
              <small>
                {[document.role, summary?.correspondent || document.correspondent || 'Unknown correspondent']
                  .filter(Boolean)
                  .join(' · ')}
              </small>
            </span>
            <ExternalLink size={16} />
          </a>
        );
      }) : <EmptyState title="No related evidence" desc={emptyDescription} />}
    </div>
  );
}
