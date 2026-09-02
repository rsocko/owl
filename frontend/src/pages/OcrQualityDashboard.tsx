import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Card, EmptyState, ErrorState, PageHeader, SkeletonLoader, StatCard, StatGrid, type Tone } from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/ocr-quality.css';

/* ── Types (exported for testing) ── */

export type ReviewStatus = 'GOOD' | 'UNCERTAIN' | 'REVIEW_RECOMMENDED' | 'FAILED' | 'unscored';

export type CorpusDistribution = {
  total_documents: number;
  review_status_distribution: Record<string, number>;
  overlay_score_decile_distribution: Record<string, number>;
  machine_score_decile_distribution: Record<string, number>;
  scorer_version_distribution: Record<string, number>;
  oldest_assessed_at?: string | null;
  newest_assessed_at?: string | null;
  redacted?: boolean;
};

/* ── Helpers (exported for testing) ── */

export function statusTone(status: string): Tone {
  switch (status) {
    case 'GOOD': return 'ok';
    case 'UNCERTAIN': return 'info';
    case 'REVIEW_RECOMMENDED': return 'warn';
    case 'FAILED': return 'err';
    default: return 'muted';
  }
}

export function formatDate(value?: string | null) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

const DECILE_ORDER = ['0-9', '10-19', '20-29', '30-39', '40-49', '50-59', '60-69', '70-79', '80-89', '90-99', 'unavailable'];

function DecileHistogram({ title, distribution }: { title: string; distribution: Record<string, number> }) {
  const entries = DECILE_ORDER.filter((key) => distribution[key] != null).map((key) => [key, distribution[key]] as const);
  const max = Math.max(...entries.map(([, count]) => count), 1);
  if (entries.length === 0) {
    return (
      <Card title={title}>
        <div className="text-muted">No scored documents yet.</div>
      </Card>
    );
  }
  return (
    <Card title={title}>
      <div className="ocr-histogram">
        {entries.map(([bucket, count]) => (
          <div key={bucket} className="ocr-histogram-row">
            <span className="ocr-histogram-label">{bucket === 'unavailable' ? 'N/A' : bucket}</span>
            <div className="ocr-histogram-bar-track">
              <div className="ocr-histogram-bar" style={{ width: `${Math.max((count / max) * 100, 2)}%` }} />
            </div>
            <span className="ocr-histogram-count">{count}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function OcrQualityDashboard() {
  const navigate = useNavigate();
  const [distribution, setDistribution] = useState<CorpusDistribution | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    endpoints.ocrQuality
      .distribution()
      .then((data) => setDistribution(data as CorpusDistribution))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load OCR quality distribution.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="ocr-quality-dashboard">
      <PageHeader
        title="OCR Quality"
        desc="Corpus-wide overlay/readability and machine-extraction quality distribution."
        actions={
          <button type="button" className="btn" onClick={() => navigate('/ocr-quality/queue')}>
            Open review queue
          </button>
        }
      />

      {loading && <SkeletonLoader variant="stat-grid" />}
      {!loading && error && <ErrorState message={error} onRetry={load} />}
      {!loading && !error && distribution && distribution.total_documents === 0 && (
        <EmptyState
          icon="🔍"
          title="No OCR quality assessments yet"
          desc="Run the Stage 1 corpus scan (issue #25 CLI) to populate this dashboard."
        />
      )}
      {!loading && !error && distribution && distribution.total_documents > 0 && (
        <>
          <StatGrid>
            <StatCard title="Assessed documents" metric={distribution.total_documents} />
            {Object.entries(distribution.review_status_distribution).map(([status, count]) => (
              <StatCard
                key={status}
                title={status}
                metric={count}
                status={{ label: `${Math.round((count / distribution.total_documents) * 100)}%`, tone: statusTone(status) }}
              />
            ))}
          </StatGrid>

          <div className="ocr-histogram-grid">
            <DecileHistogram title="Overlay/readability score distribution" distribution={distribution.overlay_score_decile_distribution} />
            <DecileHistogram title="Machine-extraction score distribution" distribution={distribution.machine_score_decile_distribution} />
          </div>

          <Card title="Scorer versions & freshness">
            <div className="ocr-freshness">
              <div>
                <span className="text-muted">Assessed:</span>{' '}
                {formatDate(distribution.oldest_assessed_at)} — {formatDate(distribution.newest_assessed_at)}
              </div>
              <div className="ocr-scorer-versions">
                {Object.entries(distribution.scorer_version_distribution).map(([version, count]) => (
                  <Badge key={version} tone={version === 'unscored' ? 'muted' : 'info'}>
                    {version}: {count}
                  </Badge>
                ))}
              </div>
            </div>
          </Card>

          <Card title="Review by status">
            <div className="ocr-status-links">
              {Object.entries(distribution.review_status_distribution).map(([status, count]) => (
                <button
                  key={status}
                  type="button"
                  className="btn ghost"
                  onClick={() => navigate(`/ocr-quality/queue?review_status=${encodeURIComponent(status)}`)}
                >
                  <Badge tone={statusTone(status)}>{status}</Badge> {count} documents
                </button>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
