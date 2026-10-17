import {
  ArrowRight,
  Eye,
  FileStack,
  GitBranch,
  Link2,
  Plus,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { endpoints } from '../lib/api';
import DocumentViewerModal from './DocumentViewerModal';
import { Button, EmptyState, ErrorState, SkeletonLoader } from './ui';

type RelationshipType = 'follows' | 'supersedes' | 'supports' | 'same_sequence';

interface RelatedDocument {
  id: number;
  title: string;
  created?: string | null;
  correspondent_name?: string | null;
  document_type_name?: string | null;
  thumbnail_url: string;
  preview_url: string;
}

interface DocumentRelationship {
  id: string;
  source_document_id: number;
  target_document_id: number;
  relationship_type: RelationshipType;
  provenance: 'automatic' | 'user' | 'imported';
  confidence?: number | null;
  reason_codes: string[];
  direction: 'incoming' | 'outgoing';
  related_document?: RelatedDocument | null;
}

interface RelationshipCandidate {
  document: RelatedDocument;
  suggested: boolean;
  recommendation: {
    source_document_id: number;
    target_document_id: number;
    relationship_type: RelationshipType;
    confidence: number;
    reason_codes: string[];
    priority_adjustment: number;
    priority_explanation: string;
  };
}

interface CurrentDocument {
  id: number;
  title: string;
  created?: string | null;
  document_type_name?: string | null;
}

const RELATIONSHIP_LABELS: Record<RelationshipType, string> = {
  follows: 'Follows',
  supersedes: 'Supersedes',
  supports: 'Supporting document',
  same_sequence: 'Same recurring series',
};

const RELATIONSHIP_OPTIONS: Array<{ value: RelationshipType; label: string }> = [
  { value: 'same_sequence', label: 'Same recurring series' },
  { value: 'supports', label: 'Supporting document' },
  { value: 'follows', label: 'Later notice / follows' },
  { value: 'supersedes', label: 'Replacement / supersedes' },
];

function formatDate(value?: string | null): string {
  if (!value) return 'Date unavailable';
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function reasonLabel(reason: string): string {
  return reason.replaceAll('_', ' ');
}

export default function DocumentRelationshipsPanel({
  document,
  onToast,
}: {
  document: CurrentDocument;
  onToast: (message: string, tone?: 'success' | 'error') => void;
}) {
  const [relationships, setRelationships] = useState<DocumentRelationship[]>([]);
  const [suggestions, setSuggestions] = useState<RelationshipCandidate[]>([]);
  const [searchResults, setSearchResults] = useState<RelationshipCandidate[]>([]);
  const [query, setQuery] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const [busyKey, setBusyKey] = useState('');
  const [types, setTypes] = useState<Record<number, RelationshipType>>({});
  const [preview, setPreview] = useState<RelatedDocument | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [relationshipResponse, suggestionResponse] = await Promise.all([
        endpoints.intake.documentRelationships(document.id),
        endpoints.intake.relationshipCandidates(document.id),
      ]) as [
        { relationships: DocumentRelationship[] },
        { candidates: RelationshipCandidate[] },
      ];
      setRelationships(relationshipResponse.relationships);
      setSuggestions(suggestionResponse.candidates);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Related documents could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [document.id]);

  useEffect(() => {
    setQuery('');
    setSearchResults([]);
    setAddOpen(false);
    void load();
  }, [load]);

  const lifecycle = useMemo(
    () => relationships.filter((relationship) => (
      relationship.relationship_type === 'follows'
      || relationship.relationship_type === 'supersedes'
    )),
    [relationships],
  );
  const related = useMemo(
    () => relationships.filter((relationship) => (
      relationship.relationship_type === 'supports'
      || relationship.relationship_type === 'same_sequence'
    )),
    [relationships],
  );
  const timeline = useMemo(() => {
    const nodes = [
      {
        id: document.id,
        title: document.title,
        created: document.created,
        document_type_name: document.document_type_name,
        current: true,
        relationship: null as DocumentRelationship | null,
      },
      ...lifecycle.flatMap((relationship) => (
        relationship.related_document
          ? [{
              ...relationship.related_document,
              current: false,
              relationship,
            }]
          : []
      )),
    ];
    return nodes.sort((left, right) => (
      (left.created || '9999').localeCompare(right.created || '9999')
      || left.id - right.id
    ));
  }, [document, lifecycle]);

  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const response = await endpoints.intake.relationshipCandidates(
        document.id,
        query.trim(),
      ) as { candidates: RelationshipCandidate[] };
      setSearchResults(response.candidates);
    } catch (reason) {
      onToast(
        reason instanceof Error ? reason.message : 'Related document search failed.',
        'error',
      );
    } finally {
      setSearching(false);
    }
  };

  const addRelationship = async (candidate: RelationshipCandidate) => {
    const selectedType = types[candidate.document.id]
      || candidate.recommendation.relationship_type;
    const directional = selectedType === 'follows' || selectedType === 'supersedes';
    const sourceDocumentId = directional
      ? candidate.recommendation.source_document_id
      : document.id;
    const targetDocumentId = directional
      ? candidate.recommendation.target_document_id
      : candidate.document.id;
    setBusyKey(`add-${candidate.document.id}`);
    try {
      await endpoints.relationships.create({
        source_document_id: sourceDocumentId,
        target_document_id: targetDocumentId,
        relationship_type: selectedType,
        provenance: 'user',
        confidence: candidate.recommendation.confidence,
        reason_codes: candidate.recommendation.reason_codes,
        priority_adjustment: candidate.recommendation.priority_adjustment,
        priority_explanation: candidate.recommendation.priority_explanation,
      });
      onToast(`${candidate.document.title} linked.`);
      setSearchResults((current) => current.filter(
        (item) => item.document.id !== candidate.document.id,
      ));
      await load();
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Document could not be linked.', 'error');
    } finally {
      setBusyKey('');
    }
  };

  const removeRelationship = async (relationship: DocumentRelationship) => {
    setBusyKey(`remove-${relationship.id}`);
    try {
      await endpoints.relationships.remove(relationship.id);
      setRelationships((current) => current.filter((item) => item.id !== relationship.id));
      onToast('Document link removed.');
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Document link could not be removed.', 'error');
    } finally {
      setBusyKey('');
    }
  };

  const candidateCard = (candidate: RelationshipCandidate, suggested: boolean) => {
    const selectedType = types[candidate.document.id]
      || candidate.recommendation.relationship_type;
    return (
      <article className="intake-related-candidate" key={candidate.document.id}>
        <button
          type="button"
          className="intake-related-thumb"
          aria-label={`Preview ${candidate.document.title}`}
          onClick={() => setPreview(candidate.document)}
        >
          <img src={candidate.document.thumbnail_url} alt="" loading="lazy" />
          <span><Eye size={14} /> Preview</span>
        </button>
        <div className="intake-related-copy">
          <div className="intake-related-title-row">
            <strong>{candidate.document.title}</strong>
            {suggested ? <span className="intake-suggested-badge"><Sparkles size={12} /> Suggested</span> : null}
          </div>
          <span>
            {candidate.document.correspondent_name || 'Correspondent not set'}
            {' · '}
            {candidate.document.document_type_name || 'Type not set'}
          </span>
          <span>{formatDate(candidate.document.created)}</span>
          {candidate.recommendation.reason_codes.length ? (
            <div className="intake-related-reasons">
              {candidate.recommendation.reason_codes.slice(0, 3).map((reason) => (
                <span key={reason}>{reasonLabel(reason)}</span>
              ))}
            </div>
          ) : null}
          <div className="intake-related-candidate-actions">
            <select
              aria-label={`Relationship type for ${candidate.document.title}`}
              value={selectedType}
              onChange={(event) => setTypes((current) => ({
                ...current,
                [candidate.document.id]: event.target.value as RelationshipType,
              }))}
            >
              {RELATIONSHIP_OPTIONS.map((option) => (
                <option value={option.value} key={option.value}>{option.label}</option>
              ))}
            </select>
            <Button
              size="sm"
              variant="primary"
              disabled={Boolean(busyKey)}
              onClick={() => void addRelationship(candidate)}
            >
              <Link2 size={14} />
              {busyKey === `add-${candidate.document.id}` ? 'Linking…' : 'Add link'}
            </Button>
          </div>
        </div>
      </article>
    );
  };

  if (loading) return <div className="intake-relationships-panel"><SkeletonLoader variant="detail-panel" /></div>;
  if (error) {
    return (
      <div className="intake-relationships-panel">
        <ErrorState message={`Related documents unavailable: ${error}`} onRetry={() => void load()} />
      </div>
    );
  }

  return (
    <div className="intake-relationships-panel">
      <div className="intake-relationships-heading">
        <div>
          <h3>Document relationships</h3>
          <p>Track lifecycle documents separately from recurring or supporting records.</p>
        </div>
        <Button size="sm" onClick={() => setAddOpen((open) => !open)}>
          {addOpen ? <X size={15} /> : <Plus size={15} />}
          {addOpen ? 'Close search' : 'Add related'}
        </Button>
      </div>

      {addOpen ? (
        <section className="intake-related-search" aria-label="Add a related document">
          <div className="intake-related-search-row">
            <label>
              <Search size={16} />
              <input
                aria-label="Search Paperless documents"
                value={query}
                placeholder="Search title, correspondent, account, or invoice…"
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    void runSearch();
                  }
                }}
              />
            </label>
            <Button disabled={searching || !query.trim()} onClick={() => void runSearch()}>
              {searching ? 'Searching…' : 'Search'}
            </Button>
          </div>
          {searchResults.length ? (
            <div className="intake-related-candidates">
              {searchResults.map((candidate) => candidateCard(candidate, false))}
            </div>
          ) : query && !searching ? (
            <p className="intake-related-search-hint">No results yet. Search to find any Paperless document.</p>
          ) : (
            <p className="intake-related-search-hint">Confirmed links are written to OWL and projected to Paperless.</p>
          )}
        </section>
      ) : null}

      {lifecycle.length ? (
        <section className="intake-relationship-section">
          <div className="intake-relationship-section-title">
            <GitBranch size={17} />
            <div><h4>Lifecycle</h4><p>Invoice, notice, replacement, and receipt progression.</p></div>
          </div>
          <ol className="intake-document-timeline">
            {timeline.map((node, index) => (
              <li className={node.current ? 'current' : ''} key={node.id}>
                <span className="intake-timeline-marker" aria-hidden="true" />
                <div className="intake-timeline-copy">
                  <time>{formatDate(node.created)}</time>
                  <strong>{node.title}</strong>
                  <span>{node.document_type_name || (node.current ? 'Current document' : 'Linked document')}</span>
                </div>
                {node.current ? (
                  <span className="intake-current-badge">Current</span>
                ) : (
                  <>
                    <span className="intake-relationship-kind">
                      {RELATIONSHIP_LABELS[node.relationship!.relationship_type]}
                    </span>
                    <Button size="sm" variant="ghost" onClick={() => setPreview(node as RelatedDocument)}>
                      <Eye size={14} /> View
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove relationship to ${node.title}`}
                      disabled={Boolean(busyKey)}
                      onClick={() => void removeRelationship(node.relationship!)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </>
                )}
                {index < timeline.length - 1 ? <ArrowRight className="intake-timeline-arrow" size={14} aria-hidden="true" /> : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      <section className="intake-relationship-section">
        <div className="intake-relationship-section-title">
          <FileStack size={17} />
          <div><h4>Related & similar</h4><p>Recurring statements and supporting records that are not one lifecycle.</p></div>
        </div>
        {related.length ? (
          <div className="intake-related-grid">
            {related.map((relationship) => {
              const relatedDocument = relationship.related_document;
              if (!relatedDocument) return null;
              return (
                <article className="intake-related-document" key={relationship.id}>
                  <button
                    type="button"
                    aria-label={`Preview ${relatedDocument.title}`}
                    onClick={() => setPreview(relatedDocument)}
                  >
                    <img src={relatedDocument.thumbnail_url} alt="" loading="lazy" />
                    <span><Eye size={14} /> Open PDF</span>
                  </button>
                  <div>
                    <span className="intake-relationship-kind">
                      {RELATIONSHIP_LABELS[relationship.relationship_type]}
                    </span>
                    <strong>{relatedDocument.title}</strong>
                    <span>{relatedDocument.correspondent_name || 'Correspondent not set'}</span>
                    <span>{formatDate(relatedDocument.created)} · {relatedDocument.document_type_name || 'Type not set'}</span>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={Boolean(busyKey)}
                      onClick={() => void removeRelationship(relationship)}
                    >
                      <Trash2 size={14} /> Remove
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <EmptyState
            title="No related records yet"
            desc="Add recurring statements, supporting documents, or other records worth keeping together."
          />
        )}
      </section>

      {suggestions.length ? (
        <section className="intake-relationship-section intake-suggestions-section">
          <div className="intake-relationship-section-title">
            <Sparkles size={17} />
            <div><h4>Suggested</h4><p>Potential matches from correspondent, type, account, and obligation signals.</p></div>
          </div>
          <div className="intake-related-candidates">
            {suggestions.map((candidate) => candidateCard(candidate, true))}
          </div>
        </section>
      ) : null}

      {!relationships.length && !suggestions.length ? (
        <EmptyState
          title="No document relationships"
          desc="Search Paperless to connect a lifecycle document, recurring statement, or supporting record."
        />
      ) : null}

      {preview ? (
        <DocumentViewerModal
          documentId={preview.id}
          title={preview.title}
          onClose={() => setPreview(null)}
        />
      ) : null}
    </div>
  );
}
