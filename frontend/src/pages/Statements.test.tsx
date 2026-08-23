import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import Statements from './Statements';

const { missingMock, providersMock } = vi.hoisted(() => ({
  missingMock: vi.fn(),
  providersMock: vi.fn(),
}));

vi.mock('../hooks/useStreamingAction', () => ({
  useStreamingAction: () => [{ running: false, error: null, progress: null }, vi.fn(), vi.fn()],
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    statements: {
      missing: missingMock,
      providers: providersMock,
      discoveryStreamUrl: '/api/statements/discovery',
      recommendationsStreamUrl: vi.fn().mockReturnValue('/api/statements/recommendations'),
    },
  },
}));

beforeAll(() => {
  if (!globalThis.ResizeObserver) {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
});

beforeEach(() => {
  missingMock.mockReset().mockResolvedValue([
    {
      id: 'beta-series',
      correspondent: 'Beta Bank',
      expected_period: '2026-07',
      frequency: 'monthly',
      last_received_date: '2026-06-10',
      days_overdue: 50,
    },
    {
      id: 'alpha-series',
      correspondent: 'Alpha Credit',
      expected_period: '2026-08',
      frequency: 'monthly',
      last_received_date: '2026-07-10',
      days_overdue: 12,
    },
  ]);
  providersMock.mockReset().mockResolvedValue({
    analyzed_documents: 5,
    run_at: '2026-08-20T12:00:00Z',
    providers: [
      {
        provider_key: 'beta',
        provider_name: 'Beta Bank',
        document_count: 2,
        normalized_title: 'statement',
        title_consistency: 1,
        frequency: 'monthly',
        pattern_type: 'fixed_day',
        confidence: 0.9,
        anchor_day: 15,
        variance_days: 1,
        grace_period_days: 3,
        sample_document_ids: [1, 2],
        first_seen: '2026-01-15',
        last_seen: '2026-07-15',
      },
      {
        provider_key: 'alpha',
        provider_name: 'Alpha Credit',
        document_count: 3,
        normalized_title: 'invoice',
        title_consistency: 1,
        frequency: 'quarterly',
        pattern_type: 'variable',
        confidence: 0.55,
        anchor_day: 10,
        variance_days: 4,
        grace_period_days: 5,
        sample_document_ids: [3, 4, 5],
        first_seen: '2025-10-10',
        last_seen: '2026-07-10',
      },
    ],
  });
});

describe('Statements', () => {
  it('sorts and filters discovered provider columns', async () => {
    render(<MemoryRouter><Statements /></MemoryRouter>);

    const providerSort = await screen.findByRole('button', { name: 'Sort by Provider' });
    fireEvent.click(providerSort);

    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('Alpha Credit')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Filter by Frequency' }));
    fireEvent.click(await screen.findByRole('button', { name: 'quarterly' }));

    expect(screen.getByText('Alpha Credit')).toBeTruthy();
    expect(screen.queryByText('Beta Bank')).toBeNull();
  });

  it('sorts and filters missing statement columns', async () => {
    render(<MemoryRouter><Statements /></MemoryRouter>);

    await screen.findByText('Beta Bank');
    fireEvent.click(screen.getByRole('button', { name: 'Missing statements (2)' }));

    const overdueSort = await screen.findByRole('button', { name: 'Sort by Days overdue' });
    fireEvent.click(overdueSort);

    const rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('50 days')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Filter by Priority' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Watch' }));

    await waitFor(() => {
      expect(screen.getByText('Alpha Credit')).toBeTruthy();
      expect(screen.queryByText('Beta Bank')).toBeNull();
    });
  });
});
