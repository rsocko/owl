import { useCallback, useEffect } from 'react';
import { endpoints } from '../lib/api';
import '../styles/document-preview.css';

interface DocumentPane {
  documentId: number;
  title?: string;
  paperlessUrl?: string | null;
}

interface SideBySideViewerModalProps {
  left: DocumentPane;
  right: DocumentPane;
  onClose: () => void;
}

/**
 * Full-screen modal with two PDF previews side-by-side, each independently scrollable.
 * Designed for comparing EOB ↔ Bill documents during match review.
 */
export default function SideBySideViewerModal({
  left,
  right,
  onClose,
}: SideBySideViewerModalProps) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown, true);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true);
      document.body.style.overflow = prev;
    };
  }, [handleKeyDown]);

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      e.stopPropagation();
      onClose();
    }
  };

  return (
    <div className="doc-viewer-overlay" onClick={handleOverlayClick}>
      <div className="doc-sbs-container">
        <div className="doc-sbs-header">
          <div className="doc-viewer-title">Compare Documents</div>
          <button className="doc-viewer-close-btn" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>
        <div className="doc-sbs-body">
          <PdfPane {...left} />
          <div className="doc-sbs-divider" />
          <PdfPane {...right} />
        </div>
      </div>
    </div>
  );
}

function PdfPane({ documentId, title, paperlessUrl }: DocumentPane) {
  const previewSrc = endpoints.documents.previewUrl(documentId);
  const downloadUrl = endpoints.documents.downloadUrl(documentId);

  return (
    <div className="doc-sbs-pane">
      <div className="doc-sbs-pane-header">
        <span className="doc-sbs-pane-title">{title ?? `Document #${documentId}`}</span>
        <div className="doc-sbs-pane-actions">
          {paperlessUrl && (
            <a href={paperlessUrl} target="_blank" rel="noreferrer" title="View in Paperless">
              📄
            </a>
          )}
          <a href={downloadUrl} download title="Download">
            ⬇
          </a>
          <a href={previewSrc} target="_blank" rel="noreferrer" title="Open in new tab">
            ↗
          </a>
        </div>
      </div>
      <iframe className="doc-sbs-pane-iframe" src={previewSrc} title={title ?? 'Document preview'} />
    </div>
  );
}
