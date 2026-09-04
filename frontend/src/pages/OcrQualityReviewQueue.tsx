import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Badge, DataTable, EmptyState, ErrorState, PageHeader, SkeletonLoader, type SortDir, type Tone } from '../components/ui';
import { MetadataTypeahead, type MetadataOption } from '../components/MetadataTypeahead';
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
  has_accepted_ocr_candidate?: boolean;
  accepted_candidate_at?: string | null;
  accepted_candidate_pending_invalidation?: boolean;
};

export type DocumentListResponse = {
  documents: DocumentSummary[];
  total: number;
  limit: number;
  offset: number;
};

const REVIEW_STATUS_OPTIONS = ['GOOD', 'UNCERTAIN', 'REVIEW_RECOMMENDED', 'FAILED'];
const PAGE_SIZE = 25;
const DEFAULT_SORT_DIR: SortDir = 'desc';

export function formatScore(value?: number | null) {
  return value == null ? '—' : value.toFixed(1);
}

function formatResolvedDate(value?: string | null) {
  return value ? value.slice(0, 10) : null;
}

function buildQueryParams(
  filters: Record<string, string>,
  sort: { key: string; dir: SortDir } | null,
  limit: number,
  offset: number,
): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  if (sort) {
    params.set('sort_by', sort.key);
    params.set('sort_dir', sort.dir);
  }
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
  const [documentTypeOptions, setDocumentTypeOptions] = useState<MetadataOption[]>([]);
  const [correspondentOptions, setCorrespondentOptions] = useState<MetadataOption[]>([]);
  const [downstreamOutcomeOptions, setDownstreamOutcomeOptions] = useState<string[]>([]);

  const filters = useMemo(
    () => ({
      review_status: searchParams.get('review_status') ?? '',
      document_type: searchParams.get('document_type') ?? '',
      correspondent: searchParams.get('correspondent') ?? '',
      downstream_outcome: searchParams.get('downstream_outcome') ?? '',
      resolved: searchParams.get('resolved') ?? '',
    }),
    [searchParams],
  );

  const sort = useMemo(() => {
    const key = searchParams.get('sort_by');
    if (!key) return null;
    const dir = searchParams.get('sort_dir') === 'asc' ? 'asc' : DEFAULT_SORT_DIR;
    return { key, dir: dir as SortDir };
  }, [searchParams]);

  // Document type / correspondent ID → name lookups. These reuse the same
  // Paperless-backed endpoints already used elsewhere in the app (document
  // type mapping admin screen, action queue metadata correction) instead of
  // inventing a new resolution mechanism.
  useEffect(() => {
    endpoints.admin
      .documentTypes()
      .then((data) => {
        const types = (data as { types?: Array<{ id: number; name: string }> }).types ?? [];
        setDocumentTypeOptions(types.map((t) => ({ value: String(t.id), label: t.name })));
      })
      .catch(() => setDocumentTypeOptions([]));
    endpoints.actionQueue
      .metadataCorrespondents()
      .then((data) => {
        const correspondents = (data as { correspondents?: Array<{ id: number; name: string }> }).correspondents ?? [];
        setCorrespondentOptions(correspondents.map((c) => ({ value: String(c.id), label: c.name })));
      })
      .catch(() => setCorrespondentOptions([]));
    endpoints.ocrQuality
      .downstreamOutcomes()
      .then((data) => setDownstreamOutcomeOptions((data as { downstream_outcomes?: string[] }).downstream_outcomes ?? []))
      .catch(() => setDownstreamOutcomeOptions([]));
  }, []);

  const documentTypeNameById = useMemo(
    () => new Map(documentTypeOptions.map((o) => [o.value, o.label])),
    [documentTypeOptions],
  );
  const correspondentNameById = useMemo(
    () => new Map(correspondentOptions.map((o) => [o.value, o.label])),
    [correspondentOptions],
  );

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const params = buildQueryParams(filters, sort, PAGE_SIZE, offset);
    endpoints.ocrQuality
      .documents(params)
      .then((data) => setResponse(data as DocumentListResponse))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load the review queue.'))
      .finally(() => setLoading(false));
  }, [filters, sort, offset]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setOffset(0);
  }, [filters, sort]);

  const updateFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next);
  };

  const handleSortChange = (key: string) => {
    const next = new URLSearchParams(searchParams);
    if (sort?.key === key) {
      if (sort.dir === 'desc') {
        next.set('sort_dir', 'asc');
      } else {
        next.delete('sort_by');
        next.delete('sort_dir');
      }
    } else {
      next.set('sort_by', key);
      next.set('sort_dir', 'desc');
    }
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
          <MetadataTypeahead
            ariaLabel="Document type"
            options={documentTypeOptions}
            value={filters.document_type}
            onChange={(value) => updateFilter('document_type', value)}
            placeholder="Search document types…"
          />
        </label>
        <label>
          Correspondent
          <MetadataTypeahead
            ariaLabel="Correspondent"
            options={correspondentOptions}
            value={filters.correspondent}
            onChange={(value) => updateFilter('correspondent', value)}
            placeholder="Search correspondents…"
          />
        </label>
        <label>
          Action Queue outcome
          <select value={filters.downstream_outcome} onChange={(e) => updateFilter('downstream_outcome', e.target.value)}>
            <option value="">All</option>
            {downstreamOutcomeOptions.map((outcome) => (
              <option key={outcome} value={outcome}>{outcome}</option>
            ))}
          </select>
        </label>
        <label>
          OCR resolution
          <select value={filters.resolved} onChange={(e) => updateFilter('resolved', e.target.value)}>
            <option value="">All</option>
            <option value="true">Resolved</option>
            <option value="false">Unresolved</option>
          </select>
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
            sortKey={sort?.key}
            sortDir={sort?.dir}
            onSortChange={handleSortChange}
            columns={[
              {
                key: 'document_id',
                header: 'Document',
                sortable: true,
                render: (row) => <button type="button" className="ocr-link-button" onClick={() => navigate(`/ocr-quality/documents/${row.document_id}`)}>#{row.document_id}</button>,
              },
              {
                key: 'document_type',
                header: 'Type',
                sortable: true,
                render: (row) => (row.document_type == null ? '—' : documentTypeNameById.get(row.document_type) ?? row.document_type),
              },
              {
                key: 'correspondent',
                header: 'Correspondent',
                sortable: true,
                render: (row) => (row.correspondent == null ? '—' : correspondentNameById.get(row.correspondent) ?? row.correspondent),
              },
              { key: 'document_created', header: 'Created', sortable: true, render: (row) => row.document_created ?? '—' },
              {
                key: 'review_status',
                header: 'Status',
                sortable: true,
                render: (row) => (
                  <div className="ocr-status-cell">
                    <Badge tone={statusTone(row.review_status ?? 'unscored') as Tone}>{row.review_status ?? 'unscored'}</Badge>
                  </div>
                ),
              },
              {
                key: 'resolved',
                header: 'OCR resolution',
                render: (row) => (
                  <div
                    className="ocr-status-cell"
                    title={
                      row.has_accepted_ocr_candidate
                        ? row.accepted_candidate_at
                          ? `OCR candidate accepted ${row.accepted_candidate_at}`
                          : 'OCR candidate accepted'
                        : 'No OCR candidate has been applied to Paperless'
                    }
                  >
                    {row.has_accepted_ocr_candidate ? (
                      <>
                        <Badge tone={row.accepted_candidate_pending_invalidation ? 'warning' : 'success'}>
                          Resolved
                        </Badge>
                        {formatResolvedDate(row.accepted_candidate_at) && (
                          <span className="text-muted">{formatResolvedDate(row.accepted_candidate_at)}</span>
                        )}
                      </>
                    ) : (
                      <span className="text-muted">Unresolved</span>
                    )}
                  </div>
                ),
              },
              { key: 'overlay_score', header: 'Overlay', sortable: true, render: (row) => formatScore(row.overlay_score) },
              { key: 'machine_score', header: 'Machine', sortable: true, render: (row) => formatScore(row.machine_score) },
              { key: 'downstream_outcome', header: 'Action outcome', sortable: true, render: (row) => row.downstream_outcome ?? '—' },
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
