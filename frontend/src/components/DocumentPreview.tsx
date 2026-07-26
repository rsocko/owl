import { useEffect, useState } from 'react';
import { endpoints } from '../lib/api';
import DocumentViewerModal from './DocumentViewerModal';
import '../styles/document-preview.css';

interface DocumentMetadata {
  id: number;
  title?: string;
  original_file_name?: string;
  page_count?: number | null;
  created?: string | null;
  added?: string | null;
  paperless_url?: string | null;
  thumbnail_url?: string;
  preview_url?: string;
  download_url?: string;
}

interface DocumentPreviewProps {
  /** Paperless document ID */
  documentId: number;
  /** Override Paperless deep link instead of fetching from metadata */
  paperlessUrl?: string | null;
  /** Display variant: 'card' = full metadata card, 'compact' = inline row */
  variant?: 'card' | 'compact';
  /** Label for the document type (e.g. "EOB", "Bill") */
  label?: string;
}

function formatDate(value?: string | null) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString();
}

/**
 * Reusable document preview component with thumbnail, metadata, and action buttons.
 * Supports full card and compact inline variants.
 */
export default function DocumentPreview({
  documentId,
  paperlessUrl: paperlessUrlOverride,
  variant = 'card',
  label,
}: DocumentPreviewProps) {
  const [meta, setMeta] = useState<DocumentMetadata | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [thumbLoaded, setThumbLoaded] = useState(false);
  const [thumbError, setThumbError] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setThumbLoaded(false);
    setThumbError(false);

    endpoints.documents
      .metadata(documentId)
      .then((data) => {
        if (!cancelled) setMeta(data as DocumentMetadata);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load metadata');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [documentId]);

  const thumbnailUrl = endpoints.documents.thumbnailUrl(documentId);
  const downloadUrl = endpoints.documents.downloadUrl(documentId);
  const paperlessUrl = paperlessUrlOverride ?? meta?.paperless_url;
  const filename = meta?.original_file_name ?? meta?.title ?? `Document #${documentId}`;
  const displayLabel = label ? `${label}: ` : '';

  if (variant === 'compact') {
    return (
      <>
        <div className="doc-preview-compact">
          <div
            className="doc-preview-compact-thumb"
            onClick={() => setViewerOpen(true)}
            title="Click to preview"
          >
            {!thumbError ? (
              <img
                src={thumbnailUrl}
                alt={filename}
                onLoad={() => setThumbLoaded(true)}
                onError={() => setThumbError(true)}
                style={{ display: thumbLoaded ? 'block' : 'none' }}
              />
            ) : null}
            {!thumbLoaded && !thumbError && <div className="doc-preview-thumb-loading" />}
            {thumbError && <div className="doc-preview-compact-thumb-placeholder">📄</div>}
          </div>
          <div className="doc-preview-compact-info">
            <div className="doc-preview-compact-filename" title={filename}>
              {displayLabel}{filename}
            </div>
            <div className="doc-preview-compact-links">
              <a href="#" onClick={(e) => { e.preventDefault(); setViewerOpen(true); }}>
                Preview
              </a>
              {paperlessUrl && (
                <a href={paperlessUrl} target="_blank" rel="noreferrer">
                  Paperless
                </a>
              )}
              <a href={downloadUrl} download>
                Download
              </a>
            </div>
          </div>
        </div>
        {viewerOpen && (
          <DocumentViewerModal
            documentId={documentId}
            title={filename}
            paperlessUrl={paperlessUrl}
            onClose={() => setViewerOpen(false)}
          />
        )}
      </>
    );
  }

  // ── Full card variant ──
  if (loading) {
    return (
      <div className="doc-preview-loading">
        <div className="doc-preview-thumb-loading" />
        Loading document metadata…
      </div>
    );
  }

  if (error) {
    return <div className="doc-preview-error">⚠ {error}</div>;
  }

  return (
    <>
      <div className="doc-preview-card">
        <div className="doc-preview-body">
          <div
            className="doc-preview-thumb"
            onClick={() => setViewerOpen(true)}
            title="Click to preview document"
          >
            {!thumbError ? (
              <img
                src={thumbnailUrl}
                alt={filename}
                onLoad={() => setThumbLoaded(true)}
                onError={() => setThumbError(true)}
                style={{ display: thumbLoaded ? 'block' : 'none' }}
              />
            ) : null}
            {!thumbLoaded && !thumbError && <div className="doc-preview-thumb-loading" />}
            {thumbError && <div className="doc-preview-thumb-placeholder">📄</div>}
          </div>
          <div className="doc-preview-meta">
            <div className="doc-preview-filename">{displayLabel}{filename}</div>
            <div className="doc-preview-info">
              {meta?.page_count != null && <span>📄 {meta.page_count} page{meta.page_count !== 1 ? 's' : ''}</span>}
              {meta?.added && <span>📥 Ingested {formatDate(meta.added)}</span>}
              {meta?.created && <span>📅 Created {formatDate(meta.created)}</span>}
            </div>
            <div className="doc-preview-actions">
              <button
                className="doc-action-primary"
                onClick={() => setViewerOpen(true)}
              >
                👁 View {label ?? 'Document'} →
              </button>
              {paperlessUrl && (
                <a href={paperlessUrl} target="_blank" rel="noreferrer">
                  📄 View in Paperless
                </a>
              )}
              <a href={downloadUrl} download>
                ⬇ Download
              </a>
            </div>
          </div>
        </div>
      </div>
      {viewerOpen && (
        <DocumentViewerModal
          documentId={documentId}
          title={filename}
          paperlessUrl={paperlessUrl}
          onClose={() => setViewerOpen(false)}
        />
      )}
    </>
  );
}
