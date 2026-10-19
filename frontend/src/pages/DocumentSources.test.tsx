import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endpoints } from '../lib/api';
import type {
  DocumentExpectation,
  DocumentSeries,
  DocumentSource,
  ExternalSignal,
} from '../lib/documentSources';
import DocumentSources from './DocumentSources';

vi.mock('../lib/api', () => ({
  endpoints: {
    documentSources: {
      list: vi.fn(),
      series: vi.fn(),
      expectations: vi.fn(),
      externalSignals: vi.fn(),
      sync: vi.fn(),
      setDisposition: vi.fn(),
      setExternalSignalRelationship: vi.fn(),
    },
    statements: {
      documentPreview: (id: string) => `/preview/${id}`,
      documentThumb: (id: string) => `/thumb/${id}`,
    },
  },
}));

const sourceOne: DocumentSource = {
  correspondent_id: 41,
  current_name: 'Harbor Bank',
  review_state: 'needs_review',
  disposition: null,
  lifecycle: 'active',
  reopen_reasons: ['repeatable_pattern_detected'],
  aliases: ['Harbor Financial'],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-10-18T00:00:00Z',
  last_evidence_at: '2026-10-18T00:00:00Z',
};

const sourceTwo: DocumentSource = {
  correspondent_id: 88,
  current_name: 'Pine Mutual',
  review_state: 'resolved',
  disposition: 'no_series_expected',
  lifecycle: 'active',
  reopen_reasons: [],
  aliases: [],
  notes: 'One-off policy notices.',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-10-17T00:00:00Z',
  last_reviewed_at: '2026-10-17T00:00:00Z',
};

const series: DocumentSeries[] = [{
  id: 'series-1',
  source_correspondent_id: 41,
  name: 'Checking ending 4821',
  kind: 'statement',
  discriminator: '4821',
  document_ids: [101, 102, 103],
  membership_revision: 2,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-10-18T00:00:00Z',
}];

const expectation: DocumentExpectation = {
  id: 'expectation-1',
  source_correspondent_id: 41,
  series_id: 'series-1',
  name: 'Checking statement',
  kind: 'statement',
  expectation_mode: 'recurring',
  status: 'confirmed',
  cadence: {
    frequency: 'monthly',
    expected_day: 28,
    availability_delay_days: 0,
    grace_period_days: 7,
  },
  importance: 'routine',
  monitoring_mode: 'presence',
  grace_period_days: 7,
  evidence: {
    source: 'user',
    reason_codes: ['user_confirmed_series'],
    sample_size: 3,
  },
};

const signal: ExternalSignal = {
  id: 'signal-1',
  kind: 'accountStatementCandidate',
  active: true,
  display_hint: 'Harbor Bank checking •••• 4821',
  confidence: 0.94,
  basis: ['institution_name_match', 'account_suffix_match'],
  source_as_of: '2026-10-18T00:00:00Z',
  account_name: 'Checking',
  institution_name: 'Harbor Bank',
  account_type: 'checking',
  account_last_four: '4821',
  relationship: null,
};

function mockLoadedData({
  sources = [sourceOne, sourceTwo],
  sourceSeries = series,
  expectations = [expectation],
  signals = [signal],
}: {
  sources?: DocumentSource[];
  sourceSeries?: DocumentSeries[];
  expectations?: DocumentExpectation[];
  signals?: ExternalSignal[];
} = {}) {
  vi.mocked(endpoints.documentSources.list).mockResolvedValue(sources);
  vi.mocked(endpoints.documentSources.series).mockResolvedValue(sourceSeries);
  vi.mocked(endpoints.documentSources.expectations).mockResolvedValue(expectations);
  vi.mocked(endpoints.documentSources.externalSignals).mockResolvedValue(signals);
}

function renderPage(path = '/sources') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/sources" element={<DocumentSources />} />
        <Route path="/sources/:correspondentId" element={<DocumentSources />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('Document Sources', () => {
  it('keeps the queue visible while source data loads and shows useful evidence afterward', async () => {
    let resolveSources!: (value: DocumentSource[]) => void;
    vi.mocked(endpoints.documentSources.list).mockReturnValue(new Promise((resolve) => { resolveSources = resolve; }));
    vi.mocked(endpoints.documentSources.series).mockResolvedValue(series);
    vi.mocked(endpoints.documentSources.expectations).mockResolvedValue([expectation]);
    vi.mocked(endpoints.documentSources.externalSignals).mockResolvedValue([signal]);

    renderPage();
    expect(screen.getByRole('status')).toHaveTextContent('Loading source evidence');
    expect(screen.getByRole('heading', { name: 'Document Sources' })).toBeInTheDocument();

    resolveSources([sourceOne, sourceTwo]);
    expect(await screen.findByRole('heading', { name: 'Harbor Bank' })).toBeInTheDocument();
    expect(screen.getByText('Series revisions do not rewrite expectation policy.')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /open representative document/i })).toHaveLength(3);
  });

  it('switches between review, external signal, and complete directory modes', async () => {
    mockLoadedData();
    renderPage();
    await screen.findByRole('heading', { name: 'Harbor Bank' });

    fireEvent.click(screen.getByRole('button', { name: /external signals/i }));
    expect(await screen.findByRole('heading', { name: 'Harbor Bank checking •••• 4821' })).toBeInTheDocument();
    expect(screen.getByText(/never establishes expectation policy/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /all correspondents/i }));
    fireEvent.click(screen.getByRole('button', { name: /pine mutual/i }));
    expect(await screen.findByRole('heading', { name: 'Pine Mutual' })).toBeInTheDocument();
    expect(screen.getByText(/No Series Expected was recorded/i)).toBeInTheDocument();
  });

  it('filters the directory by disposition from the compact filter popover', async () => {
    mockLoadedData();
    renderPage();
    await screen.findByRole('heading', { name: 'Harbor Bank' });
    fireEvent.click(screen.getByRole('button', { name: /all correspondents/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Filter document sources' }));
    fireEvent.change(screen.getByLabelText('Disposition'), { target: { value: 'no_series_expected' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

    expect(screen.queryByRole('button', { name: /harbor bank/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /pine mutual/i })).toBeInTheDocument();
  });

  it('saves a durable source disposition without creating expectation policy', async () => {
    mockLoadedData({ expectations: [] });
    vi.mocked(endpoints.documentSources.setDisposition).mockResolvedValue({
      ...sourceOne,
      review_state: 'resolved',
      disposition: 'no_series_expected',
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Harbor Bank' });

    expect(screen.getByRole('radio', { name: /Waiting for more history/i })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Review note'), { target: { value: 'Need another quarter of evidence.' } });
    fireEvent.click(screen.getByRole('button', { name: /save disposition/i }));

    await waitFor(() => expect(endpoints.documentSources.setDisposition).toHaveBeenCalledWith(41, {
      disposition: 'no_series_expected',
      waiting_revisit_date: null,
      aliases: ['Harbor Financial'],
      notes: 'Need another quarter of evidence.',
    }));
  });

  it('links an external signal to a source with zero expectation links and never creates policy implicitly', async () => {
    mockLoadedData({ expectations: [] });
    vi.mocked(endpoints.documentSources.setExternalSignalRelationship).mockResolvedValue({
      signal_id: 'signal-1',
      source_correspondent_id: 41,
      expectation_ids: [],
      resolution: 'linked',
      safe_label: signal.display_hint,
      revisit_date: null,
      updated_at: '2026-10-18T00:00:00Z',
    });
    renderPage();
    await screen.findByRole('heading', { name: 'Harbor Bank' });
    fireEvent.click(screen.getByRole('button', { name: /external signals/i }));
    await screen.findByRole('heading', { name: signal.display_hint });

    fireEvent.change(screen.getByLabelText('Document source'), { target: { value: '41' } });
    expect(screen.getByText(/zero expectation links/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /save resolution/i }));

    await waitFor(() => expect(endpoints.documentSources.setExternalSignalRelationship).toHaveBeenCalledWith('signal-1', {
      source_correspondent_id: 41,
      expectation_ids: [],
      resolution: 'linked',
      safe_label: signal.display_hint,
      revisit_date: null,
    }));
    expect(endpoints.documentSources).not.toHaveProperty('createExpectation.mock');
  });

  it('shows explicit empty and error states', async () => {
    mockLoadedData({ sources: [], sourceSeries: [], expectations: [], signals: [] });
    const { unmount } = renderPage();
    expect(await screen.findByRole('heading', { name: 'Nothing is selected' })).toBeInTheDocument();
    unmount();

    vi.mocked(endpoints.documentSources.list).mockRejectedValue(new Error('Paperless source sync is unavailable.'));
    vi.mocked(endpoints.documentSources.series).mockResolvedValue([]);
    vi.mocked(endpoints.documentSources.expectations).mockResolvedValue([]);
    vi.mocked(endpoints.documentSources.externalSignals).mockResolvedValue([]);
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Document Sources could not load' })).toBeInTheDocument();
    expect(screen.getByText('Paperless source sync is unavailable.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled();
  });
});
