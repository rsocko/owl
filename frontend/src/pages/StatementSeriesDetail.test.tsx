import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StatementSeriesDetail from './StatementSeriesDetail';

const mocks = vi.hoisted(() => ({
  seriesDetail: vi.fn(),
  providerOverrides: vi.fn(),
  candidateExcludeDocument: vi.fn(),
  candidateRestoreDocument: vi.fn(),
  candidateConfirm: vi.fn(),
  candidateSplit: vi.fn(),
  candidateMerge: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    statements: {
      seriesDetail: mocks.seriesDetail,
      providerOverrides: mocks.providerOverrides,
      candidateExcludeDocument: mocks.candidateExcludeDocument,
      candidateRestoreDocument: mocks.candidateRestoreDocument,
      candidateConfirm: mocks.candidateConfirm,
      candidateSplit: mocks.candidateSplit,
      candidateMerge: mocks.candidateMerge,
      seriesSplit: vi.fn(),
      seriesMerge: vi.fn(),
      setProviderOverride: vi.fn(),
      clearProviderOverride: vi.fn(),
    },
  },
}));

vi.mock('../components/DocumentPreview', () => ({
  default: ({ documentId }: { documentId: number }) => <div>Document {documentId}</div>,
}));

vi.mock('../components/triage/SeriesTimeline', () => ({
  SeriesTimeline: () => <div>Timeline</div>,
}));

const candidateDetail = {
  series: {
    id: 'mutual-one-heat-loan',
    name: 'Heat Loan',
    correspondent_id: 12,
    correspondent_name: 'Mutual One',
    frequency: 'monthly',
    account_identifier: null,
    manually_curated: false,
    document_count: 2,
    detected_document_count: 2,
    excluded_document_count: 0,
    first_seen: '2026-05-29',
    last_seen: '2026-06-26',
    source: 'discovery',
  },
  documents: [
    {
      series_id: 'mutual-one-heat-loan',
      document_id: '9746',
      title: 'Heat Loan Statement May 2026',
      statement_date: '2026-05-29',
      period_label: '2026-05',
      account_hint: null,
    },
    {
      series_id: 'mutual-one-heat-loan',
      document_id: '9748',
      title: 'Heat Loan Statement June 2026',
      statement_date: '2026-06-26',
      period_label: '2026-06',
      account_hint: null,
    },
  ],
  timeline: [],
  similar_series: [
    {
      id: 'mutual-one-heat-loan-legacy-title',
      name: 'Heat Loan Monthly',
      correspondent_id: 12,
      correspondent_name: 'Mutual One',
      frequency: 'monthly',
      account_identifier: null,
      manually_curated: false,
      document_count: 3,
      first_seen: '2026-02-28',
      last_seen: '2026-04-28',
      source: 'discovery',
    },
  ],
  anomaly_indicators: [],
  excluded_documents: [],
  membership_complete: true,
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/statements/mutual-one-heat-loan']}>
      <Routes>
        <Route path="/statements/:seriesId" element={<StatementSeriesDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('StatementSeriesDetail candidate review', () => {
  beforeEach(() => {
    mocks.seriesDetail.mockReset().mockResolvedValue(candidateDetail);
    mocks.providerOverrides.mockReset().mockResolvedValue({});
    mocks.candidateExcludeDocument.mockReset().mockResolvedValue({ status: 'ok' });
    mocks.candidateRestoreDocument.mockReset().mockResolvedValue({ status: 'ok' });
    mocks.candidateConfirm.mockReset().mockResolvedValue({ status: 'ok' });
    mocks.candidateSplit.mockReset().mockResolvedValue({ status: 'ok' });
    mocks.candidateMerge.mockReset().mockResolvedValue({ status: 'ok' });
  });

  it('shows all candidate documents and hides curated-series actions', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Heat Loan' })).toBeInTheDocument();
    expect(screen.getByText('Documents to include (2)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm and create series' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Split candidate' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Split Series/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Merge with Another/ })).not.toBeInTheDocument();
    expect(screen.getByText('Other candidates from this correspondent')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review merge' })).toBeEnabled();
    expect(screen.getAllByRole('button', { name: 'Exclude' })).toHaveLength(2);
  });

  it('excludes a selected candidate document', async () => {
    renderPage();

    const excludeButtons = await screen.findAllByRole('button', { name: 'Exclude' });
    fireEvent.click(excludeButtons[0]);

    await waitFor(() => {
      expect(mocks.candidateExcludeDocument).toHaveBeenCalledWith(
        'mutual-one-heat-loan',
        '9746',
      );
    });
  });

  it('splits selected documents into a second unconfirmed candidate', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Split candidate' }));
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Select Heat Loan Statement May 2026' }),
    );
    fireEvent.change(screen.getByLabelText('New candidate name:'), {
      target: { value: 'Heat Loan Escrow' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create second candidate' }));

    await waitFor(() => {
      expect(mocks.candidateSplit).toHaveBeenCalledWith('mutual-one-heat-loan', {
        document_ids: [9746],
        new_candidate_name: 'Heat Loan Escrow',
      });
    });
  });
});
