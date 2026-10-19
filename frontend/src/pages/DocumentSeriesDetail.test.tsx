import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DocumentSeriesDetail from './DocumentSeriesDetail';

const mocks = vi.hoisted(() => ({
  series: vi.fn(),
  expectations: vi.fn(),
  updateSeries: vi.fn(),
  createSeries: vi.fn(),
  updateExpectation: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    documentSources: mocks,
  },
}));

describe('DocumentSeriesDetail', () => {
  beforeEach(() => {
    mocks.series.mockReset().mockResolvedValue([
      { id: 'series-a', source_correspondent_id: 42, name: 'Checking', kind: 'statement', document_ids: [1, 2], membership_revision: 3, created_at: '2026-01-01', updated_at: '2026-01-01' },
      { id: 'series-b', source_correspondent_id: 42, name: 'Savings', kind: 'statement', document_ids: [3], membership_revision: 1, created_at: '2026-01-01', updated_at: '2026-01-01' },
    ]);
    mocks.expectations.mockReset().mockResolvedValue([
      { id: 'policy', series_id: 'series-a', source_correspondent_id: 42, name: 'Monthly checking', kind: 'statement', expectation_mode: 'recurring', status: 'confirmed', importance: 'important', monitoring_mode: 'presence', grace_period_days: 3, evidence: { source: 'user', reason_codes: [], sample_size: 1 } },
    ]);
    mocks.updateSeries.mockReset().mockResolvedValue({});
    mocks.createSeries.mockReset().mockResolvedValue({});
    mocks.updateExpectation.mockReset();
  });

  it('removes membership without rewriting expectation policy', async () => {
    render(
      <MemoryRouter initialEntries={['/coverage/series/series-a']}>
        <Routes><Route path="/coverage/series/:seriesId" element={<DocumentSeriesDetail />} /></Routes>
      </MemoryRouter>,
    );
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove' }))[0]);
    await waitFor(() => expect(mocks.updateSeries).toHaveBeenCalledWith('series-a', { document_ids: [2] }));
    expect(mocks.updateExpectation).not.toHaveBeenCalled();
    expect(screen.getByText(/Membership and policy are separate/)).toBeInTheDocument();
  });
});
