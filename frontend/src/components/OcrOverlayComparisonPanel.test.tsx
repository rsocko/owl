import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrOverlayComparisonPanel from './OcrOverlayComparisonPanel';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  regions: vi.fn(),
  candidateRegions: vi.fn(),
  pageImageUrl: vi.fn(),
  candidatePageImageUrl: vi.fn(),
  regionsDiff: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      candidates: {
        list: mocks.list,
        regions: mocks.candidateRegions,
        pageImageUrl: mocks.candidatePageImageUrl,
      },
      regions: mocks.regions,
      pageImageUrl: mocks.pageImageUrl,
      regionsDiff: mocks.regionsDiff,
    },
  },
}));

const candidate = {
  candidate_id: 'cand-123456',
  document_id: 501,
  state: 'ready',
  engine: 'ocrmypdf-tesseract-5',
};

const currentRegions = {
  page: 1,
  page_count: 1,
  width: 600,
  height: 800,
  words: [{ text: 'Hello', x0: 10, top: 10, x1: 60, bottom: 30, confidence: 0.9, flagged: false, flag_reasons: [], matched_reasons: [] }],
};

const candidateRegions = {
  page: 1,
  page_count: 1,
  width: 600,
  height: 800,
  words: [{ text: 'World', x0: 70, top: 10, x1: 120, bottom: 30, confidence: 0.9, flagged: false, flag_reasons: [], matched_reasons: [] }],
};

function loadAllImages(container: HTMLElement) {
  container.querySelectorAll('img.region-overlay-image').forEach((img) => {
    Object.defineProperty(img, 'clientWidth', { value: 600, configurable: true });
    Object.defineProperty(img, 'clientHeight', { value: 800, configurable: true });
    fireEvent.load(img);
  });
}

describe('OcrOverlayComparisonPanel', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockReset());
    mocks.list.mockResolvedValue({ candidates: [candidate] });
    mocks.regions.mockResolvedValue(currentRegions);
    mocks.candidateRegions.mockResolvedValue(candidateRegions);
    mocks.pageImageUrl.mockReturnValue('/img/current');
    mocks.candidatePageImageUrl.mockReturnValue('/img/candidate');
  });

  it('fetches candidates scoped to the document and only offers those with a stored PDF', async () => {
    mocks.list.mockResolvedValue({
      candidates: [
        candidate,
        { candidate_id: 'cand-requested', document_id: 501, state: 'requested', engine: 'azure-prebuilt-layout' },
      ],
    });
    render(<OcrOverlayComparisonPanel documentId={501} />);
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith({ document_id: 501 }));
    await waitFor(() => expect(screen.getAllByRole('combobox')).toHaveLength(2));
    const options = screen.getAllByRole('combobox')[1].querySelectorAll('option');
    expect(options).toHaveLength(2); // "Current document" + the ready candidate only
  });

  it('defaults side A to current document and side B to the first available candidate', async () => {
    const { container } = render(<OcrOverlayComparisonPanel documentId={501} />);
    await waitFor(() => expect(mocks.regions).toHaveBeenCalledWith(501, 1));
    await waitFor(() => expect(mocks.candidateRegions).toHaveBeenCalledWith('cand-123456', 1));
    loadAllImages(container);
    expect(screen.getAllByTestId('word-box')).toHaveLength(2);
  });

  it('computes and applies a diff overlay when "Show differences" is toggled on', async () => {
    mocks.regionsDiff.mockResolvedValue({
      removed_from_b: [0],
      added_in_b: [0],
      shifted: [],
    });
    const { container } = render(<OcrOverlayComparisonPanel documentId={501} />);
    await waitFor(() => expect(mocks.candidateRegions).toHaveBeenCalled());
    loadAllImages(container);

    fireEvent.click(screen.getByRole('button', { name: /Show differences/i }));

    await waitFor(() =>
      expect(mocks.regionsDiff).toHaveBeenCalledWith({
        words_a: currentRegions.words,
        words_b: candidateRegions.words,
        page_width: 600,
        page_height: 800,
      }),
    );

    await waitFor(() => {
      const boxes = screen.getAllByTestId('word-box');
      expect(boxes[0].className).toContain('diff-removed');
      expect(boxes[1].className).toContain('diff-added');
    });

    expect(screen.getByRole('note', { name: /Difference highlight legend/i })).toBeInTheDocument();
  });

  it('does not show the diff legend when "Show differences" is off', async () => {
    const { container } = render(<OcrOverlayComparisonPanel documentId={501} />);
    await waitFor(() => expect(mocks.candidateRegions).toHaveBeenCalled());
    loadAllImages(container);

    expect(screen.queryByRole('note', { name: /Difference highlight legend/i })).not.toBeInTheDocument();
  });

  it('shows an empty-state note when the document has no candidates with a stored PDF yet', async () => {
    mocks.list.mockResolvedValue({ candidates: [] });
    render(<OcrOverlayComparisonPanel documentId={501} />);
    await waitFor(() => expect(screen.getByText(/No candidates with a stored PDF yet/i)).toBeInTheDocument());
  });
});
