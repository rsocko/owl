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
  const [primaryDocId, setPrimaryDocId] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);

  const loadPair = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await endpoints.duplicates.get(pairId) as DuplicatePairDetail;
      setPair(data);
      // Default primary to doc_a
      if (data.primary_doc_id) {
        setPrimaryDocId(data.primary_doc_id);
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
    try {
      await endpoints.duplicates.resolve(pair.id, {
        resolution,
        primary_doc_id: resolution !== 'not_duplicate' ? (primaryDocId ?? undefined) : undefined,
      });
      setResolved(true);
      onResolved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Resolution failed.');
    } finally {
      setBusy(null);
    }
  };

  // Loading state
  if (loading) return <SkeletonLoader variant="card" rows={6} />;
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

      {/* Primary document selector + actions (only when pending) */}
      {!isResolved && (
        <Card title="Resolution">
          <div>
            <div style={{ fontWeight: 600, marginBottom: '0.5rem', fontSize: '0.85rem' }}>
              Select primary document (to keep):
            </div>
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
            <strong>Not duplicate</strong> — Related but different documents. Keeps both, no merge.
          </div>

          <div className="duplicate-actions">
            <Button
              variant="danger"
              onClick={() => void handleResolve('true_duplicate')}
              disabled={busy !== null || !primaryDocId}
            >
              {busy === 'true_duplicate' ? 'Merging…' : '🔗 True Duplicate'}
            </Button>
            <Button
              variant="warning"
              onClick={() => void handleResolve('superseded')}
              disabled={busy !== null || !primaryDocId}
            >
              {busy === 'superseded' ? 'Resolving…' : '📋 Superseded'}
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
          </div>
        </Card>
      )}
    </div>
  );
}
