import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Card, ConfidenceBar, SkeletonLoader } from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/duplicate-detail.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface DocMetadata {
  document_id?: number;
  title?: string;
  provider?: string;
  provider_name?: string;
  amount?: number | string;
  date_of_service?: string;
  patient_name?: string;
  invoice_number?: string;
  claim_number?: string;
  doc_type?: string;
}

interface DuplicatePairDetail {
  id: string;
  doc_a_id: number;
  doc_b_id: number;
  similarity_score: number;
  breakdown: Record<string, number> | null;
  status: string;
  primary_doc_id: number | null;
  resolved_at: string | null;
  created_at: string | null;
  doc_a_metadata: DocMetadata | null;
  doc_b_metadata: DocMetadata | null;
  relationship_proposal?: {
    source_document_id: number;
    target_document_id: number;
    relationship_type: string;
    confidence: number;
    reason_codes: string[];
    priority_adjustment: number;
    priority_explanation: string;
    auto_create: boolean;
  } | null;
}

interface RelatedResolutionResponse {
  duplicate: DuplicatePairDetail;
  relationship: {
    relationship_type: string;
    priority_adjustment: number;
    priority_explanation: string;
  };
  projection: { synced: boolean; error: string | null };
}

interface DuplicateDetailProps {
  pairId: string;
  onResolved?: () => void;
}

// ------------------------------------------------------------------
// Signal labels for breakdown display
// ------------------------------------------------------------------

const SIGNAL_LABELS: Record<string, string> = {
  invoice_number: 'Invoice / Claim #',
  amount: 'Amount',
  date_of_service: 'Date of Service',
  provider: 'Provider',
  title: 'Title Similarity',
  content_hash: 'Content Hash',
};

const SIGNAL_WEIGHTS: Record<string, number> = {
  invoice_number: 40,
  amount: 20,
  date_of_service: 15,
  provider: 10,
  title: 10,
  content_hash: 5,
};

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function scoreTone(score: number): 'high' | 'medium' | 'low' {
  if (score >= 0.85) return 'high';
  if (score >= 0.70) return 'medium';
  return 'low';
}

function formatFieldValue(val: unknown): string {
  if (val === null || val === undefined || val === '') return '—';
  if (typeof val === 'number') return val.toLocaleString();
  return String(val);
}

function fieldsMatch(a: unknown, b: unknown): boolean | null {
  if (a === null || a === undefined || a === '' || b === null || b === undefined || b === '') return null;
  return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export default function DuplicateDetail({ pairId, onResolved }: DuplicateDetailProps) {
  const [pair, setPair] = useState<DuplicatePairDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const [primaryDocId, setPrimaryDocId] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);
  const [relationshipType, setRelationshipType] = useState('follows');
  const [relationshipResult, setRelationshipResult] = useState<RelatedResolutionResponse | null>(null);

  const loadPair = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await endpoints.duplicates.get(pairId) as DuplicatePairDetail;
      setPair(data);
      // Default primary to doc_a
      if (data.primary_doc_id) {
        setPrimaryDocId(data.primary_doc_id);
      } else if (data.relationship_proposal) {
        setPrimaryDocId(data.relationship_proposal.source_document_id);
        setRelationshipType(data.relationship_proposal.relationship_type);
      } else {
        setPrimaryDocId(data.doc_a_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load duplicate pair.');
    } finally {
      setLoading(false);
    }
  }, [pairId]);

  useEffect(() => {
    void loadPair();
    setResolved(false);
  }, [loadPair]);

  const handleResolve = async (resolution: string) => {
    if (!pair) return;
    setBusy(resolution);
    setResolveError(null);
    try {
      const result = await endpoints.duplicates.resolve(pair.id, {
        resolution,
        primary_doc_id: resolution !== 'not_duplicate' ? (primaryDocId ?? undefined) : undefined,
        relationship_type: resolution === 'related' ? relationshipType : undefined,
      });
      if (resolution === 'related') {
        const related = result as RelatedResolutionResponse;
        setRelationshipResult(related);
        setPair(related.duplicate);
      } else {
        setPair(result as DuplicatePairDetail);
      }
      setResolved(true);
      onResolved?.();
    } catch (err) {
      setResolveError(err instanceof Error ? err.message : 'Resolution failed.');
    } finally {
      setBusy(null);
    }
  };

  // Loading state
  if (loading) return <SkeletonLoader variant="cards" rows={6} />;
  if (error) return <div className="text-muted">Error: {error}</div>;
  if (!pair) return <div className="text-muted">Duplicate pair not found.</div>;

  const metaA = pair.doc_a_metadata || {};
  const metaB = pair.doc_b_metadata || {};
  const scorePct = Math.round(pair.similarity_score * 100);
  const tone = scoreTone(pair.similarity_score);
  const isResolved = pair.status !== 'pending' || resolved;

  const comparisonFields = [
    { key: 'title', label: 'Title' },
    { key: 'provider', label: 'Provider', altKey: 'provider_name' },
    { key: 'amount', label: 'Amount' },
    { key: 'date_of_service', label: 'Date of Service' },
    { key: 'patient_name', label: 'Patient' },
    { key: 'invoice_number', label: 'Invoice / Claim #', altKey: 'claim_number' },
    { key: 'doc_type', label: 'Document Type' },
  ];

  return (
    <div className="duplicate-detail">
      {/* Overall similarity score */}
      <div className="duplicate-score-banner">
        <div className={`score-value ${tone}`}>{scorePct}%</div>
        <div>
          <div style={{ fontWeight: 600 }}>Similarity Score</div>
          <div className="score-label">
            {scorePct >= 85
              ? 'High likelihood of duplicate'
              : scorePct >= 70
                ? 'Moderate similarity — review carefully'
                : 'Low similarity — likely not duplicates'}
          </div>
        </div>
        <Badge tone={isResolved ? 'muted' : 'warning'}>
          {isResolved ? pair.status.replace('_', ' ') : 'Pending review'}
        </Badge>
      </div>

      {/* Side-by-side comparison */}
      <Card title="Document Comparison">
        <div className="duplicate-comparison">
          {/* Document A */}
          <div className={`duplicate-doc-card${primaryDocId === pair.doc_a_id ? ' primary' : ''}`}>
            <div className="duplicate-doc-card-header">
              <div className="doc-label">
                📄 Document A <Badge tone="muted">#{pair.doc_a_id}</Badge>
              </div>
              {primaryDocId === pair.doc_a_id && <Badge tone="info">Primary</Badge>}
            </div>
            <div className="duplicate-doc-card-body">
              {comparisonFields.map(({ key, label, altKey }) => {
                const val = (metaA as Record<string, unknown>)[key] ?? (altKey ? (metaA as Record<string, unknown>)[altKey] : undefined);
                const otherVal = (metaB as Record<string, unknown>)[key] ?? (altKey ? (metaB as Record<string, unknown>)[altKey] : undefined);
                const match = fieldsMatch(val, otherVal);
                return (
                  <div key={key} className="duplicate-field-row">
                    <span className="duplicate-field-label">{label}</span>
                    <span className={`duplicate-field-value${match === true ? ' match' : match === false ? ' mismatch' : ''}`}>
                      {formatFieldValue(val)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Document B */}
          <div className={`duplicate-doc-card${primaryDocId === pair.doc_b_id ? ' primary' : ''}`}>
            <div className="duplicate-doc-card-header">
              <div className="doc-label">
                📄 Document B <Badge tone="muted">#{pair.doc_b_id}</Badge>
              </div>
              {primaryDocId === pair.doc_b_id && <Badge tone="info">Primary</Badge>}
            </div>
            <div className="duplicate-doc-card-body">
              {comparisonFields.map(({ key, label, altKey }) => {
                const val = (metaB as Record<string, unknown>)[key] ?? (altKey ? (metaB as Record<string, unknown>)[altKey] : undefined);
                const otherVal = (metaA as Record<string, unknown>)[key] ?? (altKey ? (metaA as Record<string, unknown>)[altKey] : undefined);
                const match = fieldsMatch(val, otherVal);
                return (
                  <div key={key} className="duplicate-field-row">
                    <span className="duplicate-field-label">{label}</span>
                    <span className={`duplicate-field-value${match === true ? ' match' : match === false ? ' mismatch' : ''}`}>
                      {formatFieldValue(val)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </Card>

      {/* Similarity breakdown */}
      {pair.breakdown && (
        <Card title="Similarity Breakdown by Signal">
          <div className="duplicate-breakdown">
            {Object.entries(pair.breakdown)
              .sort(([a], [b]) => (SIGNAL_WEIGHTS[b] ?? 0) - (SIGNAL_WEIGHTS[a] ?? 0))
              .map(([signal, score]) => (
                <ConfidenceBar
                  key={signal}
                  label={`${SIGNAL_LABELS[signal] || signal} (${SIGNAL_WEIGHTS[signal] || 0}%)`}
                  pct={Math.round(score * 100)}
                />
              ))}
          </div>
        </Card>
      )}

      {pair.relationship_proposal && !isResolved && (
        <Card title="Related-document proposal">
          <div className="duplicate-resolution-hint">
            OWL proposes <strong>{pair.relationship_proposal.relationship_type}</strong> at{' '}
            <strong>{Math.round(pair.relationship_proposal.confidence * 100)}% confidence</strong>.
            <br />
            {pair.relationship_proposal.priority_explanation}.
            <br />
            Evidence: {pair.relationship_proposal.reason_codes.map((code) => code.replace(/_/g, ' ')).join(', ')}.
          </div>
        </Card>
      )}

      {/* Primary document selector + actions (only when pending) */}
      {!isResolved && (
        <Card title="Resolution">
          <div>
            <div style={{ fontWeight: 600, marginBottom: '0.5rem', fontSize: '0.85rem' }}>
              Select primary or current document:
            </div>
            <label style={{ display: 'grid', gap: '0.35rem', marginTop: '0.75rem', maxWidth: 280 }}>
              <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>If keeping both, link as</span>
              <select
                value={relationshipType}
                onChange={(event) => setRelationshipType(event.target.value)}
              >
                <option value="follows">Follows (later notice)</option>
                <option value="supersedes">Supersedes (replacement)</option>
                <option value="supports">Supports (additional evidence)</option>
                <option value="same_sequence">Same sequence</option>
              </select>
            </label>
            <div className="duplicate-primary-selector">
              <label
                className={`duplicate-primary-option${primaryDocId === pair.doc_a_id ? ' selected' : ''}`}
              >
                <input
                  type="radio"
                  name="primary-doc"
                  checked={primaryDocId === pair.doc_a_id}
                  onChange={() => setPrimaryDocId(pair.doc_a_id)}
                />
                Doc A #{pair.doc_a_id}
                {metaA.title ? ` — ${metaA.title}` : ''}
              </label>
              <label
                className={`duplicate-primary-option${primaryDocId === pair.doc_b_id ? ' selected' : ''}`}
              >
                <input
                  type="radio"
                  name="primary-doc"
                  checked={primaryDocId === pair.doc_b_id}
                  onChange={() => setPrimaryDocId(pair.doc_b_id)}
                />
                Doc B #{pair.doc_b_id}
                {metaB.title ? ` — ${metaB.title}` : ''}
              </label>
            </div>
          </div>

          <div className="duplicate-resolution-hint">
            <strong>True duplicate</strong> — Same content, different scan. Merges and archives the other.
            <br />
            <strong>Superseded</strong> — Updated balance or payment applied. Marks old as superseded.
            <br />
            <strong>Keep both and link</strong> — Preserves both documents and records their typed relationship.
            <br />
            <strong>Not duplicate</strong> — Unrelated documents. Keeps both without a link.
          </div>

          {resolveError && (
            <div style={{ color: 'var(--red-600, #dc2626)', fontSize: '0.85rem', padding: '0.5rem 0' }}>
              ⚠ {resolveError}
            </div>
          )}

          <div className="duplicate-actions">
            <Button
              variant="danger"
              onClick={() => void handleResolve('true_duplicate')}
              disabled={busy !== null || !primaryDocId}
            >
              {busy === 'true_duplicate' ? 'Merging…' : '🔗 True Duplicate'}
            </Button>
            <Button
              variant="danger"
              onClick={() => void handleResolve('superseded')}
              disabled={busy !== null || !primaryDocId}
            >
              {busy === 'superseded' ? 'Resolving…' : '📋 Superseded'}
            </Button>
            <Button
              onClick={() => void handleResolve('related')}
              disabled={busy !== null || !primaryDocId}
            >
              {busy === 'related' ? 'Linking…' : '🔗 Keep Both and Link'}
            </Button>
            <Button
              onClick={() => void handleResolve('not_duplicate')}
              disabled={busy !== null}
            >
              {busy === 'not_duplicate' ? 'Resolving…' : '✅ Not Duplicate'}
            </Button>
          </div>
        </Card>
      )}

      {/* Resolved state */}
      {isResolved && (
        <Card title="Resolution">
          <div style={{ textAlign: 'center', padding: '1rem', color: 'var(--text-muted)' }}>
            ✓ Resolved as <strong>{pair.status.replace(/_/g, ' ')}</strong>
            {pair.primary_doc_id && <> — Primary: Doc #{pair.primary_doc_id}</>}
            {relationshipResult && (
              <>
                <br />
                Linked as <strong>{relationshipResult.relationship.relationship_type}</strong>.
                {relationshipResult.relationship.priority_adjustment > 0 && (
                  <> {relationshipResult.relationship.priority_explanation}.</>
                )}
                {!relationshipResult.projection.synced && (
                  <> OWL saved the link, but Paperless projection needs retry: {relationshipResult.projection.error}.</>
                )}
              </>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
