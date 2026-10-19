import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endpoints } from '../lib/api';
import type { DocumentSeries, DocumentSource } from '../lib/documentSources';
import ExpectationSetup from './ExpectationSetup';

vi.mock('../lib/api', () => ({
  endpoints: {
    documentSources: {
      get: vi.fn(),
      series: vi.fn(),
      expectations: vi.fn(),
      acquisitionSources: vi.fn(),
      externalSignals: vi.fn(),
      createExpectation: vi.fn(),
      updateExpectation: vi.fn(),
      setExternalSignalRelationship: vi.fn(),
      setDisposition: vi.fn(),
    },
  },
}));

const source: DocumentSource = {
  correspondent_id: 41,
  current_name: 'Harbor Bank',
  review_state: 'needs_review',
  disposition: null,
  lifecycle: 'active',
  reopen_reasons: ['repeatable_pattern_detected'],
  aliases: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-10-18T00:00:00Z',
};

const series: DocumentSeries = {
  id: 'series-1',
  source_correspondent_id: 41,
  name: 'Checking ending 4821',
  kind: 'statement',
  discriminator: '4821',
  document_ids: [101, 102, 103],
  membership_revision: 4,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-10-18T00:00:00Z',
};

function mockSetupData() {
  vi.mocked(endpoints.documentSources.get).mockResolvedValue(source);
  vi.mocked(endpoints.documentSources.series).mockResolvedValue([series]);
  vi.mocked(endpoints.documentSources.expectations).mockResolvedValue([]);
  vi.mocked(endpoints.documentSources.acquisitionSources).mockResolvedValue([{
    id: 'acq-1',
    channel: 'portal_manual',
    delivery_mode: 'pull',
    instructions: 'Download from portal',
  }]);
  vi.mocked(endpoints.documentSources.externalSignals).mockResolvedValue([{
    id: 'signal-1',
    display_hint: 'Harbor Bank checking •••• 4821',
    relationship: { source_correspondent_id: 41, expectation_ids: [] },
  }]);
}

function renderSetup() {
  return render(
    <MemoryRouter initialEntries={['/sources/41/expectations/new?series=series-1']}>
      <Routes>
        <Route path="/sources/:correspondentId/expectations/new" element={<ExpectationSetup />} />
        <Route path="/sources/:correspondentId" element={<div>Source saved</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSetupData();
});

describe('Expectation Setup', () => {
  it('uses progressive disclosure and explains that irregular series never create missing alerts', async () => {
    renderSetup();
    expect(await screen.findByRole('heading', { name: 'Identify the document' })).toBeInTheDocument();
    expect(screen.getByText(/membership revision 4/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /continue/i }));
    fireEvent.click(screen.getByRole('button', { name: /irregular/i }));
    fireEvent.click(screen.getByRole('button', { name: /continue/i }));

    expect(screen.getByText(/Irregular policy does not create missing-document occurrences/i)).toBeInTheDocument();
    expect(screen.getByText('No missing-document monitoring')).toBeInTheDocument();
  });

  it('keeps Tyrion optional and saves recognition, filing, acquisition, and monitoring policy explicitly', async () => {
    vi.mocked(endpoints.documentSources.createExpectation).mockResolvedValue({
      id: 'expectation-new',
      source_correspondent_id: 41,
      series_id: 'series-1',
      name: 'Checking ending 4821',
      kind: 'statement',
      expectation_mode: 'recurring',
      status: 'confirmed',
      importance: 'routine',
      monitoring_mode: 'presence',
      grace_period_days: 7,
      evidence: { source: 'user', reason_codes: ['user_confirmed_series'], sample_size: 3 },
    });
    vi.mocked(endpoints.documentSources.setDisposition).mockResolvedValue({
      ...source,
      review_state: 'resolved',
      disposition: 'expectations_configured',
    });
    renderSetup();
    await screen.findByRole('heading', { name: 'Identify the document' });

    fireEvent.click(screen.getByRole('button', { name: /continue/i }));
    fireEvent.click(screen.getByRole('button', { name: /continue/i }));
    fireEvent.change(screen.getByLabelText('Expected near day'), { target: { value: '27' } });
    fireEvent.click(screen.getByRole('button', { name: /continue/i }));
    fireEvent.change(screen.getByLabelText('Paperless title convention'), { target: { value: '{correspondent} - {period}' } });
    fireEvent.click(screen.getByRole('button', { name: /continue/i }));

    expect(screen.getByLabelText(/Specific Tyrion relationship/i)).toHaveValue('');
    fireEvent.change(screen.getByLabelText('Acquisition source'), { target: { value: 'acq-1' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm policy/i }));

    await waitFor(() => expect(endpoints.documentSources.createExpectation).toHaveBeenCalledWith(expect.objectContaining({
      source_correspondent_id: 41,
      series_id: 'series-1',
      expectation_mode: 'recurring',
      monitoring_mode: 'presence',
      cadence: expect.objectContaining({ expected_day: 27 }),
      title_convention: expect.objectContaining({ template: '{correspondent} - {period}' }),
      acquisition_source_id: 'acq-1',
    })));
    expect(endpoints.documentSources.setExternalSignalRelationship).not.toHaveBeenCalled();
    expect(endpoints.documentSources.setDisposition).toHaveBeenCalledWith(41, expect.objectContaining({
      disposition: 'expectations_configured',
    }));
    expect(await screen.findByText('Source saved')).toBeInTheDocument();
  });
});

