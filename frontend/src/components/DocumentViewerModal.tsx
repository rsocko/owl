import { useCallback, useEffect } from 'react';
import { endpoints } from '../lib/api';
import '../styles/document-preview.css';

interface DocumentViewerModalProps {
  documentId: number;
  title?: string;
  paperlessUrl?: string | null;
  onClose: () => void;
}

/**
 * Full-screen modal overlay with embedded PDF preview.
 * Falls back to a "View in new tab" link if the iframe fails.
 */
export default function DocumentViewerModal({
  documentId,
  title,
  paperlessUrl,
  onClose,
}: DocumentViewerModalProps) {
  const previewSrc = endpoints.documents.previewUrl(documentId);
  const downloadUrl = endpoints.documents.downloadUrl(documentId);

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
    // Prevent background scrolling while modal is open
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
      <div className="doc-viewer-container">
        <div className="doc-viewer-header">
          <div className="doc-viewer-title">{title ?? `Document #${documentId}`}</div>
          <div className="doc-viewer-header-actions">
            {paperlessUrl && (
              <a href={paperlessUrl} target="_blank" rel="noreferrer">
                📄 View in Paperless
              </a>
            )}
            <a href={downloadUrl} download>
              ⬇ Download
            </a>
            <a href={previewSrc} target="_blank" rel="noreferrer">
              ↗ New Tab
            </a>
          </div>
          <button className="doc-viewer-close-btn" onClick={onClose} title="Close (Esc)">
            ✕
          </button>
        </div>
        <iframe className="doc-viewer-iframe" src={previewSrc} title={title ?? 'Document preview'} />
      </div>
    </div>
  );
}
