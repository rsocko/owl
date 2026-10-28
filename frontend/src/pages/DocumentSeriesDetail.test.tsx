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
  documents: vi.fn(),
  addSeriesMembers: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    documentSources: mocks,
    statements: {
      documentPreview: (id: string) => `/preview/${id}`,
      documentThumb: (id: string) => `/thumb/${id}`,
    },
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
    mocks.documents.mockReset().mockResolvedValue([
      { id: 1, title: 'January checking statement', created: '2026-01-31', document_type: 'Statement', tags: ['Finance'] },
      { id: 2, title: 'February checking statement', created: '2026-02-28', document_type: 'Statement', tags: [] },
      { id: 3, title: 'Savings statement', created: '2026-01-31', document_type: 'Statement', tags: [] },
      { id: 4, title: 'March checking statement', created: '2026-03-31', document_type: 'Statement', tags: ['Finance'], account_identifier_display: 'ending 1234', account_identifier_source: 'extracted' },
    ]);
    mocks.addSeriesMembers.mockReset().mockResolvedValue({});
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

  it('finds and previews unassigned documents by evidence before appending membership', async () => {
    render(
      <MemoryRouter initialEntries={['/coverage/series/series-a']}>
        <Routes><Route path="/coverage/series/:seriesId" element={<DocumentSeriesDetail />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('January checking statement')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Find documents'), { target: { value: '1234' } });
    expect(screen.getByText('March checking statement')).toBeInTheDocument();
    expect(screen.queryByText('Savings statement')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open March checking statement' }));
    expect(screen.getByTitle('Preview of March checking statement')).toHaveAttribute('src', '/preview/4');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTitle('Preview of March checking statement')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select March checking statement' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 selected documents' }));
    await waitFor(() => expect(mocks.addSeriesMembers).toHaveBeenCalledWith('series-a', [4]));
    expect(mocks.updateExpectation).not.toHaveBeenCalled();
  });
});
