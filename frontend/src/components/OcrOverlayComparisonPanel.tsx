/**
 * Side-by-side overlay/box-placement comparison view (connects issue #134's
 * region-inspection viewer with issue #18's candidate comparison).
 *
 * Lets a reviewer pick two sources for the same document — "current
 * document" or any of its candidates — and see each source's word boxes
 * rendered on the page image via two `RegionOverlayViewer` instances, plus
 * an optional "Show differences" overlay highlighting words that are
 * geometrically added/removed/shifted between the two sources (computed
 * server-side by `POST /api/ocr-quality/regions/diff`).
 *
 * Read-only in every sense: no annotation support here (candidates don't
 * support annotations in this slice), and nothing here can change the
 * accept/reject decision flow.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, ErrorState } from './ui';
import RegionOverlayViewer, { type DiffHighlightKind, type PageRegions } from './RegionOverlayViewer';
import { endpoints } from '../lib/api';
import '../styles/region-overlay.css';

export type ComparisonCandidateOption = {
  candidate_id: string;
  engine: string;
  state: string;
};

const CURRENT_SOURCE = 'current';

// Only these states still have a stored PDF artifact on disk (requested/
// running haven't generated one yet, failed never produced one, expired
// have had theirs deleted) — see candidate_service.py's artifact lifecycle.
const STATES_WITH_PDF = new Set(['ready', 'accepted', 'rejected']);

type DiffResponse = {
  removed_from_b: number[];
  added_in_b: number[];
  shifted: { index_a: number; index_b: number }[];
};

function sourceLabel(source: string, candidates: ComparisonCandidateOption[], engineLabels: Record<string, string>): string {
  if (source === CURRENT_SOURCE) return 'Current document';
  const candidate = candidates.find((c) => c.candidate_id === source);
  if (!candidate) return source;
  return `${engineLabels[candidate.engine] ?? candidate.engine} (${candidate.candidate_id.slice(0, 8)})`;
}

function regionsRequest(source: string, documentId: number, page: number) {
  return source === CURRENT_SOURCE
    ? endpoints.ocrQuality.regions(documentId, page)
    : endpoints.ocrQuality.candidates.regions(source, page);
}

function pageImageUrlFor(source: string, documentId: number, page: number): string {
  return source === CURRENT_SOURCE
    ? endpoints.ocrQuality.pageImageUrl(documentId, page)
    : endpoints.ocrQuality.candidates.pageImageUrl(source, page);
}

const ENGINE_LABELS: Record<string, string> = {
  'ocrmypdf-tesseract-5': 'OCRmyPDF / Tesseract 5',
  'azure-prebuilt-read': 'Azure Document Intelligence (prebuilt-read)',
};

function SourceSelect({
  label,
  value,
  onChange,
  candidates,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  candidates: ComparisonCandidateOption[];
}) {
  const selectId = `ocr-overlay-source-${label.replace(/\s+/g, '-').toLowerCase()}`;
  return (
    <div className="ocr-overlay-source-select">
      <label htmlFor={selectId}>{label}</label>
      <select id={selectId} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value={CURRENT_SOURCE}>Current document</option>
        {candidates.map((c) => (
          <option key={c.candidate_id} value={c.candidate_id}>
            {sourceLabel(c.candidate_id, candidates, ENGINE_LABELS)}
          </option>
        ))}
      </select>
    </div>
  );
}

export default function OcrOverlayComparisonPanel({ documentId }: { documentId: number }) {
  const [candidates, setCandidates] = useState<ComparisonCandidateOption[]>([]);
  const [sourceA, setSourceA] = useState<string>(CURRENT_SOURCE);
  const [sourceB, setSourceB] = useState<string>(CURRENT_SOURCE);
  const [page, setPage] = useState(1);
  const [regionsA, setRegionsA] = useState<PageRegions | null>(null);
  const [regionsB, setRegionsB] = useState<PageRegions | null>(null);
  const [errorA, setErrorA] = useState<string | null>(null);
  const [errorB, setErrorB] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  // Fetch the candidate list ourselves (mirrors OcrCandidatesPanel's own
  // fetch) so this panel can be mounted independently of that component.
  useEffect(() => {
    let cancelled = false;
    endpoints.ocrQuality.candidates
      .list({ document_id: documentId })
      .then((data) => {
        if (cancelled) return;
        const candidateList = (data as { candidates?: ComparisonCandidateOption[] }).candidates ?? [];
        const withPdf = candidateList.filter((c) => STATES_WITH_PDF.has(c.state));
        setCandidates(withPdf);
      })
      .catch(() => {
        if (!cancelled) setCandidates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  // Keep the "B" side pointed at a real candidate once one becomes
  // available, so the panel isn't stuck comparing "current vs current".
  useEffect(() => {
    if (sourceB === CURRENT_SOURCE && candidates.length > 0) {
      setSourceB(candidates[0].candidate_id);
    }
  }, [candidates, sourceB]);

  useEffect(() => {
    let cancelled = false;
    setErrorA(null);
    regionsRequest(sourceA, documentId, page)
      .then((data) => {
        if (!cancelled) setRegionsA(data as PageRegions);
      })
      .catch((err) => {
        if (!cancelled) {
          setRegionsA(null);
          setErrorA(err instanceof Error ? err.message : 'Failed to load regions for side A.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sourceA, documentId, page]);

  useEffect(() => {
    let cancelled = false;
    setErrorB(null);
    regionsRequest(sourceB, documentId, page)
      .then((data) => {
        if (!cancelled) setRegionsB(data as PageRegions);
      })
      .catch((err) => {
        if (!cancelled) {
          setRegionsB(null);
          setErrorB(err instanceof Error ? err.message : 'Failed to load regions for side B.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sourceB, documentId, page]);

  const runDiff = useCallback(() => {
    if (!regionsA || !regionsB) return;
    setDiffLoading(true);
    setDiffError(null);
    endpoints.ocrQuality
      .regionsDiff({
        words_a: regionsA.words,
        words_b: regionsB.words,
        page_width: Math.max(regionsA.width, regionsB.width),
        page_height: Math.max(regionsA.height, regionsB.height),
      })
      .then((data) => setDiff(data as DiffResponse))
      .catch((err) => {
        setDiff(null);
        setDiffError(err instanceof Error ? err.message : 'Failed to compute differences.');
      })
      .finally(() => setDiffLoading(false));
  }, [regionsA, regionsB]);

  useEffect(() => {
    if (!showDiff) {
      setDiff(null);
      return;
    }
    runDiff();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showDiff, regionsA, regionsB]);

  const diffHighlightsA = useMemo<Map<number, DiffHighlightKind> | undefined>(() => {
    if (!diff) return undefined;
    const map = new Map<number, DiffHighlightKind>();
    diff.removed_from_b.forEach((i) => map.set(i, 'removed'));
    diff.shifted.forEach((pair) => map.set(pair.index_a, 'shifted'));
    return map;
  }, [diff]);

  const diffHighlightsB = useMemo<Map<number, DiffHighlightKind> | undefined>(() => {
    if (!diff) return undefined;
    const map = new Map<number, DiffHighlightKind>();
    diff.added_in_b.forEach((i) => map.set(i, 'added'));
    diff.shifted.forEach((pair) => map.set(pair.index_b, 'shifted'));
    return map;
  }, [diff]);

  return (
    <div className="ocr-overlay-comparison-panel">
      {candidates.length === 0 && (
        <div className="ocr-stub-note">
          No candidates with a stored PDF yet — generate candidates above, then come back here to
          compare their word-box placement against the current document.
        </div>
      )}
      <div className="ocr-overlay-comparison-controls">
        <SourceSelect label="Side A" value={sourceA} onChange={setSourceA} candidates={candidates} />
        <SourceSelect label="Side B" value={sourceB} onChange={setSourceB} candidates={candidates} />
        <Button
          variant={showDiff ? 'primary' : 'ghost'}
          size="sm"
          onClick={() => setShowDiff((v) => !v)}
          disabled={!regionsA || !regionsB}
          aria-pressed={showDiff}
        >
          {diffLoading ? 'Computing differences…' : `Show differences ${showDiff ? 'on' : 'off'}`}
        </Button>
      </div>

      {diffError && <ErrorState message={diffError} />}

      <div className="ocr-overlay-comparison-grid">
        <div>
          <div className="ocr-overlay-comparison-heading">{sourceLabel(sourceA, candidates, ENGINE_LABELS)}</div>
          {errorA && <ErrorState message={errorA} />}
          {!errorA && (
            <RegionOverlayViewer
              imageUrl={pageImageUrlFor(sourceA, documentId, page)}
              regions={regionsA}
              onPageChange={setPage}
              diffHighlights={showDiff ? diffHighlightsA : undefined}
            />
          )}
        </div>
        <div>
          <div className="ocr-overlay-comparison-heading">{sourceLabel(sourceB, candidates, ENGINE_LABELS)}</div>
          {errorB && <ErrorState message={errorB} />}
          {!errorB && (
            <RegionOverlayViewer
              imageUrl={pageImageUrlFor(sourceB, documentId, page)}
              regions={regionsB}
              onPageChange={setPage}
              diffHighlights={showDiff ? diffHighlightsB : undefined}
            />
          )}
        </div>
      </div>
    </div>
  );
}
