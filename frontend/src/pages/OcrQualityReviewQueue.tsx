import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Badge, DataTable, EmptyState, ErrorState, PageHeader, SkeletonLoader, type Tone } from '../components/ui';
import { endpoints } from '../lib/api';
import { statusTone } from './OcrQualityDashboard';
import '../styles/ocr-quality.css';

/* ── Types (exported for testing) ── */

export type DocumentSummary = {
  document_id: number;
  document_type?: string | null;
  correspondent?: string | null;
  document_created?: string | null;
  overlay_score?: number | null;
  machine_score?: number | null;
  review_status?: string | null;
  downstream_outcome?: string | null;
  dominant_classification?: string | null;
  quality_scorer_version?: string | null;
  assessed_at?: string | null;
};

export type DocumentListResponse = {
  documents: DocumentSummary[];
  total: number;
  limit: number;
  offset: number;
};

const REVIEW_STATUS_OPTIONS = ['GOOD', 'UNCERTAIN', 'REVIEW_RECOMMENDED', 'FAILED'];
const PAGE_SIZE = 25;

export function formatScore(value?: number | null) {
  return value == null ? '—' : value.toFixed(1);
}

function buildQueryParams(filters: Record<string, string>, limit: number, offset: number): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return params.toString();
}

export default function OcrQualityReviewQueue() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [response, setResponse] = useState<DocumentListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  const filters = useMemo(
    () => ({
      review_status: searchParams.get('review_status') ?? '',
      document_type: searchParams.get('document_type') ?? '',
      correspondent: searchParams.get('correspondent') ?? '',
      downstream_outcome: searchParams.get('downstream_outcome') ?? '',
    }),
    [searchParams],
  );

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = buildQueryParams(filters, PAGE_SIZE, offset);
    endpoints.ocrQuality
      .documents(params)
      .then((data) => setResponse(data as DocumentListResponse))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load the review queue.'))
      .finally(() => setLoading(false));
  }, [filters, offset]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setOffset(0);
  }, [filters]);

  const updateFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next);
  };

  const documents = response?.documents ?? [];
  const total = response?.total ?? 0;

  return (
    <div className="ocr-quality-queue">
      <PageHeader title="OCR Quality Review Queue" desc="Browse and filter documents by assessment status, type, and correspondent." />

      <div className="ocr-filters">
        <label>
          Review status
          <select value={filters.review_status} onChange={(e) => updateFilter('review_status', e.target.value)}>
            <option value="">All</option>
            {REVIEW_STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </label>
        <label>
          Document type
          <input
            type="text"
            value={filters.document_type}
            onChange={(e) => updateFilter('document_type', e.target.value)}
            placeholder="e.g. 5"
          />
        </label>
        <label>
          Correspondent
          <input
            type="text"
            value={filters.correspondent}
            onChange={(e) => updateFilter('correspondent', e.target.value)}
            placeholder="e.g. 9"
          />
        </label>
        <label>
          Downstream outcome
          <input
            type="text"
            value={filters.downstream_outcome}
            onChange={(e) => updateFilter('downstream_outcome', e.target.value)}
            placeholder="e.g. reviewed"
          />
        </label>
      </div>

      {loading && <SkeletonLoader variant="table" />}
      {!loading && error && <ErrorState message={error} onRetry={load} />}
      {!loading && !error && documents.length === 0 && (
        <EmptyState icon="📭" title="No documents match these filters" />
      )}
      {!loading && !error && documents.length > 0 && (
        <>
          <DataTable<DocumentSummary>
            rows={documents}
            rowKey={(row) => String(row.document_id)}
            columns={[
              {
                key: 'document_id',
                header: 'Document',
                render: (row) => <button type="button" className="ocr-link-button" onClick={() => navigate(`/ocr-quality/documents/${row.document_id}`)}>#{row.document_id}</button>,
              },
              { key: 'document_type', header: 'Type', render: (row) => row.document_type ?? '—' },
              { key: 'correspondent', header: 'Correspondent', render: (row) => row.correspondent ?? '—' },
              { key: 'document_created', header: 'Created', render: (row) => row.document_created ?? '—' },
              {
                key: 'review_status',
                header: 'Status',
                render: (row) => <Badge tone={statusTone(row.review_status ?? 'unscored') as Tone}>{row.review_status ?? 'unscored'}</Badge>,
              },
              { key: 'overlay_score', header: 'Overlay', render: (row) => formatScore(row.overlay_score) },
              { key: 'machine_score', header: 'Machine', render: (row) => formatScore(row.machine_score) },
              { key: 'downstream_outcome', header: 'Downstream', render: (row) => row.downstream_outcome ?? '—' },
            ]}
          />
          <div className="ocr-pagination">
            <button type="button" className="btn ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              ← Previous
            </button>
            <span className="text-muted">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </span>
            <button
              type="button"
              className="btn ghost"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next →
            </button>
          </div>
        </>
      )}
    </div>
  );
}
