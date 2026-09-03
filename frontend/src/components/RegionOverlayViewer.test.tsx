import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import RegionOverlayViewer, { type PageRegions } from './RegionOverlayViewer';

const baseRegions: PageRegions = {
  page: 1,
  page_count: 2,
  width: 600,
  height: 800,
  words: [
    { text: 'Hello', x0: 10, top: 10, x1: 60, bottom: 30, confidence: 0.95, flagged: false, flag_reasons: [], matched_reasons: [] },
    {
      text: 'World',
      x0: 70,
      top: 10,
      x1: 120,
      bottom: 30,
      confidence: 0.4,
      flagged: true,
      flag_reasons: ['alignment'],
      matched_reasons: [{ code: 'overlay.alignment', message: 'Text misaligned with scanned image', severity: 'warning' }],
    },
  ],
};

function loadImage(container: HTMLElement, size = { width: 600, height: 800 }) {
  const img = container.querySelector('img.region-overlay-image') as HTMLImageElement;
  Object.defineProperty(img, 'clientWidth', { value: size.width, configurable: true });
  Object.defineProperty(img, 'clientHeight', { value: size.height, configurable: true });
  fireEvent.load(img);
}

describe('RegionOverlayViewer', () => {
  it('renders one box per word after the image loads', () => {
    const { container } = render(<RegionOverlayViewer imageUrl="/img" regions={baseRegions} />);
    loadImage(container);
    const boxes = screen.getAllByTestId('word-box');
    expect(boxes).toHaveLength(2);
    expect(boxes[0]).toHaveAttribute('title', 'Hello');
  });

  it('shows OCR text + confidence + matched reasons when a box is clicked', () => {
    const { container } = render(<RegionOverlayViewer imageUrl="/img" regions={baseRegions} />);
    loadImage(container);
    const boxes = screen.getAllByTestId('word-box');
    fireEvent.click(boxes[1]);
    const popover = screen.getByTestId('word-detail-popover');
    expect(popover).toHaveTextContent('World');
    expect(popover).toHaveTextContent('40%');
    expect(popover).toHaveTextContent('alignment');
    expect(popover).toHaveTextContent('Text misaligned with scanned image');
  });

  it('toggles heatmap coloring between passed/flagged classes', () => {
    const { container } = render(<RegionOverlayViewer imageUrl="/img" regions={baseRegions} />);
    loadImage(container);
    const toggle = screen.getByRole('button', { name: /heatmap/i });
    fireEvent.click(toggle);
    const boxes = screen.getAllByTestId('word-box');
    expect(boxes[0].className).toContain('passed');
    expect(boxes[1].className).toContain('flagged');
  });

  it('shows page navigation when page_count > 1 and calls onPageChange', () => {
    const onPageChange = vi.fn();
    const { container } = render(
      <RegionOverlayViewer imageUrl="/img" regions={baseRegions} onPageChange={onPageChange} />,
    );
    loadImage(container);
    fireEvent.click(screen.getByRole('button', { name: /Next/i }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it('supports drawing a new annotation box and submitting label/note', async () => {
    const onCreateAnnotation = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <RegionOverlayViewer imageUrl="/img" regions={baseRegions} onCreateAnnotation={onCreateAnnotation} />,
    );
    loadImage(container);

    fireEvent.click(screen.getByRole('button', { name: /Draw annotation/i }));
    const canvas = container.querySelector('.region-overlay-canvas') as HTMLElement;
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 600, bottom: 800, width: 600, height: 800, x: 0, y: 0, toJSON: () => {},
    });
    fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10 });
    fireEvent.mouseMove(canvas, { clientX: 100, clientY: 60 });
    fireEvent.mouseUp(canvas, { clientX: 100, clientY: 60 });

    expect(screen.getByTestId('annotation-form')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'key_data' } });
    fireEvent.change(screen.getByLabelText(/Note/i), { target: { value: 'important field' } });
    fireEvent.click(screen.getByRole('button', { name: /Save annotation/i }));

    expect(onCreateAnnotation).toHaveBeenCalledWith(
      expect.objectContaining({ label: 'key_data', note: 'important field' }),
    );
  });

  it('renders diff highlight classes and overrides heatmap styling when diffHighlights is set', () => {
    const diffHighlights = new Map<number, 'added' | 'removed' | 'shifted'>([
      [0, 'removed'],
      [1, 'shifted'],
    ]);
    const { container } = render(
      <RegionOverlayViewer imageUrl="/img" regions={baseRegions} diffHighlights={diffHighlights} />,
    );
    loadImage(container);
    const boxes = screen.getAllByTestId('word-box');
    expect(boxes[0].className).toContain('diff-removed');
    expect(boxes[1].className).toContain('diff-shifted');
    expect(boxes[0]).toHaveAttribute('title', 'Hello (removed)');
    expect(boxes[1]).toHaveAttribute('title', 'World (shifted)');
  });

  it('renders a rotated box for a word with a non-zero angle, and no transform otherwise', () => {
    const regionsWithRotatedWord: PageRegions = {
      ...baseRegions,
      words: [
        baseRegions.words[0],
        { ...baseRegions.words[1], angle: 90 },
      ],
    };
    const { container } = render(<RegionOverlayViewer imageUrl="/img" regions={regionsWithRotatedWord} />);
    loadImage(container);
    const boxes = screen.getAllByTestId('word-box');
    expect(boxes[0].style.transform).toBe('');
    expect(boxes[1].style.transform).toBe('rotate(-90deg)');
    expect(boxes[1].style.transformOrigin).toBe('center');
  });

  it('renders existing annotations as boxes and supports deleting them', () => {
    const onDeleteAnnotation = vi.fn();
    const { container } = render(
      <RegionOverlayViewer
        imageUrl="/img"
        regions={baseRegions}
        annotations={[{ id: 7, document_id: 1, page: 1, x0: 5, top: 5, x1: 25, bottom: 25, label: 'wrong', note: 'bad' }]}
        onDeleteAnnotation={onDeleteAnnotation}
      />,
    );
    loadImage(container);
    const annotationBox = screen.getByTestId('annotation-box');
    expect(annotationBox).toHaveAttribute('title', 'wrong: bad');
    fireEvent.click(screen.getByRole('button', { name: /Delete annotation 7/i }));
    expect(onDeleteAnnotation).toHaveBeenCalledWith(7);
  });
});
