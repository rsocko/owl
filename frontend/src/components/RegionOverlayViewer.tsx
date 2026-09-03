import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button } from './ui';
import '../styles/region-overlay.css';

/* ── Types (exported for testing/reuse) ── */

export type RegionWord = {
  text: string;
  x0: number;
  top: number;
  x1: number;
  bottom: number;
  confidence?: number | null;
  /** Rotation of the word's glyphs in degrees (0 = normal upright text). */
  angle?: number;
  flagged: boolean;
  flag_reasons: string[];
  matched_reasons: { code: string; message: string; severity: string }[];
};

export type PageRegions = {
  page: number;
  page_count: number;
  width: number;
  height: number;
  error?: string | null;
  words: RegionWord[];
};

export type Annotation = {
  id: number;
  document_id: number;
  page: number;
  x0: number;
  top: number;
  x1: number;
  bottom: number;
  label: string;
  note?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type DrawnBox = { x0: number; top: number; x1: number; bottom: number };

export type DiffHighlightKind = 'added' | 'removed' | 'shifted';

export const ANNOTATION_LABELS = ['wrong', 'key_data', 'table_region', 'other'] as const;

interface RegionOverlayViewerProps {
  /** Page image URL (already resolved by the caller, e.g. endpoints.ocrQuality.pageImageUrl). */
  imageUrl: string;
  /** Word-level region geometry + flags for the currently displayed page, or null while loading. */
  regions: PageRegions | null;
  /** Annotations that belong to the currently displayed page (already filtered by the caller). */
  annotations?: Annotation[];
  /** Called with a freshly drawn box + label/note once the reviewer submits the inline form. */
  onCreateAnnotation?: (box: DrawnBox & { label: string; note: string | null }) => void | Promise<void>;
  /** Called when the reviewer asks to delete an existing annotation from the overlay. */
  onDeleteAnnotation?: (annotationId: number) => void | Promise<void>;
  /** Optional page navigation controls (rendered only when page_count > 1). */
  onPageChange?: (page: number) => void;
  /**
   * Optional box-diff result (issue #134 x #18 candidate overlay
   * comparison), keyed by this instance's word index in `regions.words`.
   * When present, overrides the normal neutral/passed/flagged styling with
   * a distinct diff style for the flagged indices — takes precedence over
   * heatmap mode, which is otherwise mutually exclusive UI state.
   */
  diffHighlights?: Map<number, DiffHighlightKind>;
}

/**
 * Renders a document page image with an overlaid layer of word-level boxes
 * (issue #134, Part 1) plus an ad hoc "draw a box and flag it" annotation
 * tool (Part 2). Deliberately takes a single image/regions/annotations set
 * as generic props (not hard-wired to "the current document") so a future
 * side-by-side comparison view could reuse it with two instances — see
 * `OcrOverlayComparisonPanel.tsx` (issue #134 x #18), which does exactly
 * that and drives the optional `diffHighlights` prop.
 */
export default function RegionOverlayViewer({
  imageUrl,
  regions,
  annotations = [],
  onCreateAnnotation,
  onDeleteAnnotation,
  onPageChange,
  diffHighlights,
}: RegionOverlayViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [imgSize, setImgSize] = useState<{ width: number; height: number } | null>(null);
  const [heatmap, setHeatmap] = useState(false);
  const [selectedWord, setSelectedWord] = useState<RegionWord | null>(null);
  const [drawing, setDrawing] = useState<{ startX: number; startY: number; x: number; y: number } | null>(null);
  const [pendingBox, setPendingBox] = useState<DrawnBox | null>(null);
  const [label, setLabel] = useState<string>(ANNOTATION_LABELS[0]);
  const [note, setNote] = useState('');
  const [drawMode, setDrawMode] = useState(false);

  const handleImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImgSize({ width: img.clientWidth, height: img.clientHeight });
  }, []);

  // Scale factors: convert PDF-point coordinates (from /regions) into the
  // image's currently *displayed* pixel size, independent of the DPI used
  // to render the PNG on the backend.
  const scale = useMemo(() => {
    if (!imgSize || !regions || regions.width <= 0 || regions.height <= 0) return null;
    return { sx: imgSize.width / regions.width, sy: imgSize.height / regions.height };
  }, [imgSize, regions]);

  const toPixelRect = useCallback(
    (box: { x0: number; top: number; x1: number; bottom: number }) => {
      if (!scale) return null;
      return {
        left: box.x0 * scale.sx,
        top: box.top * scale.sy,
        width: (box.x1 - box.x0) * scale.sx,
        height: (box.bottom - box.top) * scale.sy,
      };
    },
    [scale],
  );

  const toPointBox = useCallback(
    (pixelBox: { x0: number; top: number; x1: number; bottom: number }): DrawnBox | null => {
      if (!scale) return null;
      return {
        x0: pixelBox.x0 / scale.sx,
        top: pixelBox.top / scale.sy,
        x1: pixelBox.x1 / scale.sx,
        bottom: pixelBox.bottom / scale.sy,
      };
    },
    [scale],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!drawMode || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      setDrawing({ startX: x, startY: y, x, y });
      setPendingBox(null);
    },
    [drawMode],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!drawing || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      setDrawing((prev) => (prev ? { ...prev, x, y } : prev));
    },
    [drawing],
  );

  const handleMouseUp = useCallback(() => {
    if (!drawing) return;
    const pixelBox = {
      x0: Math.min(drawing.startX, drawing.x),
      top: Math.min(drawing.startY, drawing.y),
      x1: Math.max(drawing.startX, drawing.x),
      bottom: Math.max(drawing.startY, drawing.y),
    };
    setDrawing(null);
    if (pixelBox.x1 - pixelBox.x0 < 4 || pixelBox.bottom - pixelBox.top < 4) return; // ignore accidental clicks
    const pointBox = toPointBox(pixelBox);
    if (pointBox) setPendingBox(pointBox);
  }, [drawing, toPointBox]);

  const submitPendingBox = useCallback(async () => {
    if (!pendingBox || !onCreateAnnotation) return;
    await onCreateAnnotation({ ...pendingBox, label, note: note.trim() ? note.trim() : null });
    setPendingBox(null);
    setNote('');
    setLabel(ANNOTATION_LABELS[0]);
  }, [pendingBox, onCreateAnnotation, label, note]);

  const cancelPendingBox = useCallback(() => {
    setPendingBox(null);
    setNote('');
    setLabel(ANNOTATION_LABELS[0]);
  }, []);

  useEffect(() => {
    // Reset transient interaction state whenever the page image changes.
    setSelectedWord(null);
    setDrawing(null);
    setPendingBox(null);
  }, [imageUrl]);

  const drawingPixelBox = drawing
    ? {
        left: Math.min(drawing.startX, drawing.x),
        top: Math.min(drawing.startY, drawing.y),
        width: Math.abs(drawing.x - drawing.startX),
        height: Math.abs(drawing.y - drawing.startY),
      }
    : null;

  return (
    <div className="region-overlay-viewer">
      <div className="region-overlay-toolbar">
        <Button
          variant={heatmap ? 'primary' : 'ghost'}
          size="sm"
          onClick={() => setHeatmap((v) => !v)}
          aria-pressed={heatmap}
        >
          🌡 Heatmap {heatmap ? 'on' : 'off'}
        </Button>
        {onCreateAnnotation && (
          <Button
            variant={drawMode ? 'primary' : 'ghost'}
            size="sm"
            onClick={() => setDrawMode((v) => !v)}
            aria-pressed={drawMode}
          >
            ✏ {drawMode ? 'Drawing…' : 'Draw annotation'}
          </Button>
        )}
        {regions && regions.page_count > 1 && onPageChange && (
          <div className="region-overlay-page-nav">
            <Button
              size="sm"
              variant="ghost"
              disabled={regions.page <= 1}
              onClick={() => onPageChange(regions.page - 1)}
            >
              ← Prev
            </Button>
            <span>
              Page {regions.page} / {regions.page_count}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={regions.page >= regions.page_count}
              onClick={() => onPageChange(regions.page + 1)}
            >
              Next →
            </Button>
          </div>
        )}
      </div>

      <div
        ref={containerRef}
        className={`region-overlay-canvas ${drawMode ? 'draw-mode' : ''}`}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
      >
        {/* eslint-disable-next-line jsx-a11y/alt-text */}
        <img src={imageUrl} onLoad={handleImageLoad} className="region-overlay-image" alt="Document page" />

        {scale &&
          regions?.words.map((word, i) => {
            const rect = toPixelRect(word);
            if (!rect) return null;
            const diffKind = diffHighlights?.get(i);
            const boxClass = diffKind
              ? `diff-${diffKind}`
              : heatmap
                ? word.flagged
                  ? 'flagged'
                  : 'passed'
                : 'neutral';
            return (
              <div
                key={i}
                className={`region-overlay-box ${boxClass}`}
                style={{
                  left: rect.left,
                  top: rect.top,
                  width: rect.width,
                  height: rect.height,
                }}
                title={
                  diffKind
                    ? `${word.text} (${diffKind})`
                    : word.angle
                      ? `${word.text} (rotated ${Math.round(word.angle)}°)`
                      : word.text
                }
                onClick={() => setSelectedWord(word)}
                data-testid="word-box"
              />
            );
          })}

        {scale &&
          annotations.map((ann) => {
            const rect = toPixelRect(ann);
            if (!rect) return null;
            return (
              <div
                key={ann.id}
                className="region-overlay-annotation-box"
                style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
                title={`${ann.label}${ann.note ? `: ${ann.note}` : ''}`}
                data-testid="annotation-box"
              >
                {onDeleteAnnotation && (
                  <button
                    type="button"
                    className="region-overlay-annotation-delete"
                    aria-label={`Delete annotation ${ann.id}`}
                    onClick={() => onDeleteAnnotation(ann.id)}
                  >
                    ×
                  </button>
                )}
              </div>
            );
          })}

        {drawingPixelBox && (
          <div
            className="region-overlay-box drawing-preview"
            style={drawingPixelBox}
            data-testid="drawing-preview"
          />
        )}
      </div>

      {selectedWord && (
        <div className="region-overlay-detail-popover" role="dialog" data-testid="word-detail-popover">
          <div className="region-overlay-detail-header">
            <strong>{selectedWord.text}</strong>
            <button type="button" onClick={() => setSelectedWord(null)} aria-label="Close">
              ×
            </button>
          </div>
          <div>
            Confidence:{' '}
            {selectedWord.confidence == null ? '—' : `${Math.round(selectedWord.confidence * 100)}%`}
          </div>
          {selectedWord.flag_reasons.length > 0 && (
            <div className="region-overlay-detail-flags">
              {selectedWord.flag_reasons.map((flag) => (
                <Badge key={flag} tone="warn">
                  {flag}
                </Badge>
              ))}
            </div>
          )}
          {selectedWord.matched_reasons.length > 0 && (
            <ul className="region-overlay-detail-reasons">
              {selectedWord.matched_reasons.map((reason, i) => (
                <li key={i}>{reason.message}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {pendingBox && (
        <div className="region-overlay-annotation-form" data-testid="annotation-form">
          <label htmlFor="annotation-label">Label</label>
          <select id="annotation-label" value={label} onChange={(e) => setLabel(e.target.value)}>
            {ANNOTATION_LABELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
          <label htmlFor="annotation-note">Note (optional)</label>
          <textarea
            id="annotation-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
          />
          <div className="region-overlay-annotation-form-actions">
            <Button size="sm" onClick={submitPendingBox}>
              Save annotation
            </Button>
            <Button size="sm" variant="ghost" onClick={cancelPendingBox}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
