import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import BenchmarkHistory from './BenchmarkHistory';

/* ── Mock the api module ── */

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    eob: {
      benchmarkHistory: vi.fn().mockResolvedValue({
        runs: [
          {
            id: 1,
            started_at: '2026-07-20T10:00:00Z',
            finished_at: '2026-07-20T10:05:00Z',
            documents_tested: 5,
            models_tested: 2,
            trigger: 'manual',
            status: 'completed',
            models: [
              { model: 'phi3:mini', success_rate: 0.8, avg_confidence: 0.75, avg_time_seconds: 1.23, estimated_cost_usd: 0.001 },
              { model: 'gpt-4o-mini', success_rate: 0.6, avg_confidence: 0.55, avg_time_seconds: 2.1, estimated_cost_usd: 0.005 },
            ],
          },
        ],
      }),
      benchmarkTrends: vi.fn().mockResolvedValue({
        models: ['phi3:mini', 'gpt-4o-mini'],
        runs: [
          {
            run_id: 1,
            date: '2026-07-20T10:00:00Z',
            trigger: 'manual',
            models: {
              'phi3:mini': { success_rate: 0.8, avg_confidence: 0.75, avg_time_seconds: 1.23, estimated_cost_usd: 0.001 },
              'gpt-4o-mini': { success_rate: 0.6, avg_confidence: 0.55, avg_time_seconds: 2.1, estimated_cost_usd: 0.005 },
            },
          },
          {
            run_id: 2,
            date: '2026-07-21T10:00:00Z',
            trigger: 'scheduled',
            models: {
              'phi3:mini': { success_rate: 0.85, avg_confidence: 0.78, avg_time_seconds: 1.1, estimated_cost_usd: 0.001 },
              'gpt-4o-mini': { success_rate: 0.65, avg_confidence: 0.6, avg_time_seconds: 2.0, estimated_cost_usd: 0.005 },
            },
          },
        ],
      }),
      benchmark: vi.fn().mockResolvedValue({ status: 'ok' }),
    },
  },
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/eob/benchmarks']}>
      <BenchmarkHistory />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('BenchmarkHistory', () => {
  it('renders the run history table with data', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Benchmark History')).toBeTruthy();
    });
    // Should show model names in the table row
    expect(screen.getAllByText(/phi3:mini/).length).toBeGreaterThanOrEqual(1);
  });

  it('shows regression indicator for low success rate', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/Regression/)).toBeTruthy();
    });
  });

  it('expands run detail on click', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Benchmark History')).toBeTruthy();
    });
    // Find the table row (first tr in tbody)
    const rows = screen.getAllByRole('button');
    const tableRow = rows.find((r) => r.tagName === 'TR');
    if (tableRow) fireEvent.click(tableRow);
    await waitFor(() => {
      expect(screen.getByText('80.0%')).toBeTruthy();
    });
  });

  it('renders Run Benchmark button', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/Run Benchmark/)).toBeTruthy();
    });
  });

  it('renders trend charts when multiple runs exist', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Model Trends')).toBeTruthy();
    });
  });
});
