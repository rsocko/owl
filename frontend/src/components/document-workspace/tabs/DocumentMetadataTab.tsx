import type { ReactNode } from 'react';
import type { DocumentSummaryModel } from '../../../lib/api';

export default function DocumentMetadataTab({
  document,
  editHref,
  note,
  children,
}: {
  document?: DocumentSummaryModel;
  editHref?: string;
  note?: ReactNode;
  children?: ReactNode;
}) {
  if (children) {
    return <div className="document-workspace-metadata-tab">{children}</div>;
  }

  return (
    <div className="workspace-metadata document-workspace-metadata-tab">
      <div className="workspace-panel-heading">
        <div>
          <h3>Document metadata</h3>
          <p>Paperless remains authoritative.</p>
        </div>
        {editHref ? <a className="btn" href={editHref}>Edit in Inbox</a> : null}
      </div>
      <dl>
        <div><dt>Title</dt><dd>{document?.title || 'Not set'}</dd></div>
        <div><dt>Correspondent</dt><dd>{document?.correspondent || 'Not set'}</dd></div>
        <div><dt>Document type</dt><dd>{document?.document_type || 'Not set'}</dd></div>
        <div><dt>Document date</dt><dd>{document?.date_label || document?.document_date || 'Not set'}</dd></div>
      </dl>
      {note ? <p className="workspace-scope-note">{note}</p> : null}
    </div>
  );
}
