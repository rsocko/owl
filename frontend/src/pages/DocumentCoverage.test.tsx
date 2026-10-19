import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DocumentCoverage from './DocumentCoverage';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  detail: vi.fn(),
  nearMatches: vi.fn(),
  extendGrace: vi.fn(),
  reassign: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    coverage: mocks,
    documentSources: { updateExpectation: vi.fn() },
  },
}));

const rows = [
  {
    id: 'missing-tax',
    expectation_id: 'tax-policy',
    source_correspondent_id: 1,
    source_name: 'County Tax Office',
    expectation_name: 'Annual assessment',
    kind: 'tax_document',
    period_key: '2026',
    due_start: '2026-02-01',
    due_end: '2026-02-15',
    importance: 'critical',
    grace_period_days: 3,
    grace_extended_until: null,
    state: 'missing',
    fulfilled_document_id: null,
    fulfilled_at: null,
    evaluated_at: '2026-10-18T12:00:00Z',
  },
  {
    id: 'statement',
    expectation_id: 'statement-policy',
    source_correspondent_id: 2,
    source_name: 'Harbor Bank',
    expectation_name: 'Monthly checking',
    kind: 'statement',
    period_key: '2026-10',
    due_start: '2026-10-20',
    due_end: '2026-10-25',
    importance: 'routine',
    grace_period_days: 0,
    grace_extended_until: null,
    state: 'due_soon',
    fulfilled_document_id: null,
    fulfilled_at: null,
    evaluated_at: '2026-10-18T12:00:00Z',
  },
  {
    id: 'renewal',
    expectation_id: 'renewal-policy',
    source_correspondent_id: 3,
    source_name: 'Pine Mutual',
    expectation_name: 'Homeowners renewal',
    kind: 'renewal',
    period_key: '2026',
    due_start: '2026-10-01',
    due_end: '2026-10-20',
    importance: 'important',
    grace_period_days: 0,
    grace_extended_until: null,
    state: 'fulfilled',
    fulfilled_document_id: 55,
    fulfilled_at: '2026-10-08',
    evaluated_at: '2026-10-18T12:00:00Z',
  },
  {
    id: 'invoice',
    expectation_id: 'invoice-policy',
    source_correspondent_id: 4,
    source_name: 'Cedar Dental',
    expectation_name: 'Patient invoice',
    kind: 'invoice',
    period_key: 'arrival',
    due_start: '2026-10-01',
    due_end: '2026-10-31',
    importance: 'important',
    grace_period_days: 0,
    grace_extended_until: null,
    state: 'unmonitored',
    fulfilled_document_id: null,
    fulfilled_at: null,
    evaluated_at: '2026-10-18T12:00:00Z',
  },
];

describe('DocumentCoverage', () => {
  beforeEach(() => {
    mocks.list.mockReset().mockResolvedValue(rows);
    mocks.detail.mockReset().mockResolvedValue({
      occurrence: rows[0],
      expectation: {
        id: 'tax-policy',
        source_correspondent_id: 1,
        series_id: 'tax-series',
        name: 'Annual assessment',
        kind: 'tax_document',
        expectation_mode: 'recurring',
        status: 'confirmed',
        importance: 'critical',
        monitoring_mode: 'presence',
        grace_period_days: 3,
        evidence: { source: 'user', reason_codes: ['user_confirmed'], sample_size: 1 },
      },
      series: {
        id: 'tax-series',
        source_correspondent_id: 1,
        name: 'Property assessment',
        kind: 'tax_document',
        document_ids: [10],
        membership_revision: 1,
        created_at: '2026-01-01',
        updated_at: '2026-01-01',
      },
      recent_document_ids: [10],
    });
    mocks.nearMatches.mockReset().mockResolvedValue([
      {
        document_id: 77,
        title: '2026 property assessment',
        document_date: '2026-02-18',
        document_type: 'Tax form',
        already_assigned: false,
      },
    ]);
    mocks.extendGrace.mockReset().mockResolvedValue({});
    mocks.reassign.mockReset().mockResolvedValue({});
  });

  it('filters coverage across document kinds and states', async () => {
    render(<MemoryRouter><DocumentCoverage /></MemoryRouter>);
    expect((await screen.findAllByText('County Tax Office')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Harbor Bank').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Pine Mutual').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Cedar Dental').length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('Document kind'), { target: { value: 'statement' } });
    expect(screen.getAllByText('Harbor Bank').length).toBeGreaterThan(0);
    expect(screen.queryAllByText('County Tax Office').some((element) => element.tagName === 'STRONG')).toBe(false);

    fireEvent.change(screen.getByLabelText('Coverage state'), { target: { value: 'missing' } });
    expect(screen.getByText('No occurrences match these filters')).toBeInTheDocument();
  });

  it('supports occurrence recovery without claiming connector fulfillment', async () => {
    render(<MemoryRouter><DocumentCoverage /></MemoryRouter>);
    fireEvent.click((await screen.findAllByText('County Tax Office')).find((element) => element.tagName === 'STRONG')!);
    expect(await screen.findByText('Expectation and evidence')).toBeInTheDocument();
    expect(screen.getByText(/No automatic retrieval is claimed here/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Inspect near matches' }));
    expect(await screen.findByText('2026 property assessment')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Use document' }));
    await waitFor(() => expect(mocks.reassign).toHaveBeenCalledWith('missing-tax', 77, '2026-02-18'));
  });

  it('shows a recoverable error state when coverage fails', async () => {
    mocks.list.mockRejectedValueOnce(new Error('Paperless is unavailable'));
    render(<MemoryRouter><DocumentCoverage /></MemoryRouter>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Paperless is unavailable');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
  });
});
