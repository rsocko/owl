import { ChevronLeft, ChevronRight, ExternalLink, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { endpoints } from '../lib/api';
import { formatDate, type SourceDocument } from '../lib/documentSources';
import './source-document-evidence.css';

export function SourceDocumentList({
  documents, selected, onToggle, onPreview,
}: {
  documents: SourceDocument[];
  selected?: number[];
  onToggle?: (id: number) => void;
  onPreview: (document: SourceDocument) => void;
}) {
  return (
    <div className="source-document-list">
      {documents.map((document) => (
        <div className="source-document-row" key={document.id}>
          {onToggle ? <input type="checkbox" aria-label={`Select ${document.title}`}
            checked={selected?.includes(document.id) ?? false}
            onChange={() => onToggle(document.id)} /> : null}
          <button type="button" className="source-document-thumb" aria-label={`Preview ${document.title}`}
            onClick={() => onPreview(document)}>
            <img src={endpoints.statements.documentThumb(String(document.id))} alt="" loading="lazy" />
          </button>
          <div className="source-document-copy">
            <strong>{document.title}</strong>
            <small>{formatDate(document.created)} · {document.document_type ?? 'Unknown document type'}</small>
            {document.tags.length ? <small>{document.tags.join(' · ')}</small> : null}
            <small>{document.account_identifier_display
              ? `Account ${document.account_identifier_display} · ${document.account_identifier_source === 'extracted' ? 'from existing OCR' : 'stored metadata'}`
              : 'Account unresolved'} · Paperless #{document.id}</small>
          </div>
          <button type="button" className="source-document-open" onClick={() => onPreview(document)}
            aria-label={`Open ${document.title}`}>Open</button>
        </div>
      ))}
    </div>
  );
}

export function SourceDocumentInspector({
  document, documents, onChange, onClose,
}: {
  document: SourceDocument;
  documents: SourceDocument[];
  onChange: (document: SourceDocument) => void;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const trigger = window.document.activeElement;
    closeRef.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    window.addEventListener('keydown', keydown);
    return () => {
      window.removeEventListener('keydown', keydown);
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);
  const index = documents.findIndex((item) => item.id === document.id);
  const preview = endpoints.statements.documentPreview(String(document.id));
  return (
    <aside className="source-document-inspector" aria-label="Document preview">
      <header>
        <div><h2>{document.title}</h2><p>{formatDate(document.created)} · {document.document_type ?? 'Unknown document type'}</p></div>
        <button ref={closeRef} type="button" aria-label="Close document preview" onClick={onClose}><X /></button>
      </header>
      <nav aria-label="Preview navigation">
        <button type="button" disabled={index <= 0} onClick={() => onChange(documents[index - 1])}><ChevronLeft /> Previous</button>
        <span>{index + 1} of {documents.length}</span>
        <button type="button" disabled={index < 0 || index >= documents.length - 1} onClick={() => onChange(documents[index + 1])}>Next <ChevronRight /></button>
        <a href={preview} target="_blank" rel="noreferrer"><ExternalLink /> New tab</a>
      </nav>
      <iframe key={document.id} title={`Preview of ${document.title}`} src={preview} />
    </aside>
  );
}
