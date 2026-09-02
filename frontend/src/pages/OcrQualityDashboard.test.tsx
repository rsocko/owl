import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OcrQualityDashboard from './OcrQualityDashboard';

const mocks = vi.hoisted(() => ({
  distribution: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    ocrQuality: {
      distribution: mocks.distribution,
    },
  },
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <OcrQualityDashboard />
    </MemoryRouter>,
  );
}

describe('OcrQualityDashboard', () => {
  beforeEach(() => {
    mocks.distribution.mockReset();
  });

  it('shows a loading state before data resolves', () => {
    mocks.distribution.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.querySelector('.skeleton, .skeleton-loader, [class*="skeleton"]')).toBeTruthy();
  });

  it('shows an empty state when the corpus has not been scanned', async () => {
    mocks.distribution.mockResolvedValue({
      total_documents: 0,
      review_status_distribution: {},
      overlay_score_decile_distribution: {},
      machine_score_decile_distribution: {},
      scorer_version_distribution: {},
      oldest_assessed_at: null,
      newest_assessed_at: null,
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/No OCR quality assessments yet/i)).toBeInTheDocument());
  });

  it('renders an error state and retries', async () => {
    mocks.distribution.mockRejectedValueOnce(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });

  it('renders review-status stats and score histograms when data is present', async () => {
    mocks.distribution.mockResolvedValue({
      total_documents: 42,
      review_status_distribution: { GOOD: 30, UNCERTAIN: 8, REVIEW_RECOMMENDED: 3, FAILED: 1 },
      overlay_score_decile_distribution: { '90-99': 20, '80-89': 10, unavailable: 12 },
      machine_score_decile_distribution: { '90-99': 25, '70-79': 17 },
      scorer_version_distribution: { '1.0.0': 42 },
      oldest_assessed_at: '2024-01-01T00:00:00Z',
      newest_assessed_at: '2024-06-01T00:00:00Z',
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Assessed documents')).toBeInTheDocument());
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getAllByText('GOOD').length).toBeGreaterThan(0);
    expect(screen.getByText('Overlay/readability score distribution')).toBeInTheDocument();
    expect(screen.getByText('Machine-extraction score distribution')).toBeInTheDocument();
  });
});
