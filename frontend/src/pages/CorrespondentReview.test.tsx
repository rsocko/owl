import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endpoints } from '../lib/api';
import CorrespondentReview from './CorrespondentReview';

vi.mock('../lib/api', () => ({
  endpoints: {
    statements: {
      correspondentProfiles: {
        get: vi.fn(),
        analysis: vi.fn(),
        expectations: vi.fn(),
        inventory: vi.fn(),
        update: vi.fn(),
        relink: vi.fn(),
        dismissSuggestion: vi.fn(),
        createExpectation: vi.fn(),
      },
      acquisitionSources: {
        list: vi.fn(),
        create: vi.fn(),
      },
      updateExpectation: vi.fn(),
      paperlessUrl: vi.fn(),
    },
  },
}));

const profile = {
  correspondent_id: 42,
  current_name: 'Example Bank',
  review_status: 'unreviewed',
  lifecycle_status: 'active',
  aliases: [],
  notes: null,
  profile_defaults: { title_convention: null, metadata_policy: null },
  observed_summary: {
    document_count: 0,
    document_type_counts: {},
    title_pattern_count: 0,
    tag_family_counts: {},
    candidate_series_count: 0,
  },
  last_analyzed_at: null,
  last_reviewed_at: null,
  orphaned_at: null,
  relinked_from_correspondent_id: null,
  created_at: null,
  updated_at: null,
};

function suggestion(seriesId: string, mode: 'recurring' | 'unknown') {
  return {
    statement_series_id: seriesId,
    source_statement_series_id: null,
    series_discriminator: seriesId === 'checking' ? 'Checking ending 1234' : 'Savings ending 5678',
    candidate_series: false,
    existing_expectation_id: null,
    kind: 'statement',
    expectation_mode: mode,
    cadence: mode === 'recurring' ? {
      frequency: 'monthly',
      expected_day: 3,
      availability_delay_days: 2,
      grace_period_days: 5,
    } : null,
    evidence: {
      source: 'paperless',
      reason_codes: ['monthly_pattern'],
      confidence: 0.9,
      sample_size: 6,
      observed_from: '2026-01-03',
      observed_to: '2026-06-03',
    },
    document_ids: [101, 102, 103],
    title: {
      convention: {
        template: '{series} - {kind} - {period}',
        date_basis: 'period',
        example: 'Checking ending 1234 - Statement - 2026-06',
      },
      coverage: 0.83,
      confidence: 0.9,
      exception_document_ids: [103],
      examples: [101, 102, 103].map((documentId) => ({
        document_id: documentId,
        before: `Old redacted title ${documentId}`,
        after: `New redacted title ${documentId}`,
      })),
      missing_required_fields: [{ document_id: 103, missing_fields: ['period'] }],
      reason_codes: ['dominant_title_pattern'],
    },
    metadata: {
      policy: { all_of: [1], any_of: [], none_of: [], required_document_type_id: 3 },
      confidence: 0.88,
      required_tag_families: [
        {
          family: 'ACCOUNT',
          child_tag_ids: [11, 12],
          child_tag_names: ['ACCOUNT:Checking', 'ACCOUNT:Savings'],
          coverage: 1,
          reason_codes: ['child_tag_family_coverage'],
        },
        {
          family: 'OWNER',
          child_tag_ids: [21, 22],
          child_tag_names: ['OWNER:Primary', 'OWNER:Joint'],
          coverage: 0.9,
          reason_codes: ['child_tag_family_coverage'],
        },
      ],
      reason_codes: ['multiple_tag_families_require_separate_rules'],
    },
    acquisition: {
      channel: 'paperless_mail',
      delivery_mode: 'push',
      confidence: 0.85,
      reason_codes: ['configured_mail_rule_evidence'],
      sample_size: 6,
    },
  };
}

describe('CorrespondentReview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(endpoints.statements.correspondentProfiles.get).mockResolvedValue(profile);
    vi.mocked(endpoints.statements.correspondentProfiles.analysis).mockResolvedValue({
      correspondent_id: 42,
      correspondent_name: 'Example Bank',
      document_count: 12,
      observed_from: '2026-01-03',
      observed_to: '2026-06-03',
      suggestions: [suggestion('checking', 'recurring'), suggestion('savings', 'unknown')],
      unassigned_document_ids: [],
      reason_codes: ['multiple_existing_series'],
    });
    vi.mocked(endpoints.statements.correspondentProfiles.expectations).mockResolvedValue([]);
    vi.mocked(endpoints.statements.correspondentProfiles.inventory).mockResolvedValue([{ profile }]);
    vi.mocked(endpoints.statements.acquisitionSources.list).mockResolvedValue([]);
    vi.mocked(endpoints.statements.paperlessUrl).mockResolvedValue({ paperless_url: 'https://paperless.test' });
    vi.mocked(endpoints.statements.correspondentProfiles.createExpectation).mockResolvedValue({});
    vi.mocked(endpoints.statements.correspondentProfiles.dismissSuggestion).mockResolvedValue(undefined);
  });

  function renderPage() {
    render(
      <MemoryRouter initialEntries={['/correspondents/42']}>
        <Routes>
          <Route path="/correspondents/:correspondentId" element={<CorrespondentReview />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it('shows independent series, all three redacted renders, Paperless links, and separate tag families', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Checking ending 1234' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Savings ending 5678' })).toBeInTheDocument();
    expect(screen.getAllByText(/Old redacted title/)).toHaveLength(6);
    expect(screen.getAllByText(/New redacted title/)).toHaveLength(6);
    expect(screen.getAllByText('ACCOUNT').length).toBeGreaterThan(0);
    expect(screen.getAllByText('OWNER').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/will not flatten them together/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: 'Document 101 ↗' })[0]).toHaveAttribute(
      'href',
      'https://paperless.test/documents/101/details',
    );
  });

  it('requires a persistable mode for unknown analysis and confirms through the policy API', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Checking ending 1234' });

    const confirmButtons = screen.getAllByRole('button', { name: 'Confirm expectation' });
    expect(confirmButtons[0]).toBeEnabled();
    expect(confirmButtons[1]).toBeDisabled();
    fireEvent.click(confirmButtons[0]);

    await waitFor(() =>
      expect(endpoints.statements.correspondentProfiles.createExpectation).toHaveBeenCalledWith(
        42,
        expect.objectContaining({
          statement_series_id: 'checking',
          expectation_mode: 'recurring',
          status: 'confirmed',
        }),
      ),
    );
  });

  it('dismisses an unknown unbound suggestion without inventing policy', async () => {
    renderPage();
    await screen.findByRole('heading', { name: 'Savings ending 5678' });

    fireEvent.click(screen.getAllByRole('button', { name: 'Dismiss' })[1]);

    await waitFor(() =>
      expect(endpoints.statements.correspondentProfiles.dismissSuggestion).toHaveBeenCalledWith(
        42,
        {
          statement_series_id: 'savings',
          source_statement_series_id: null,
          series_discriminator: 'Savings ending 5678',
          kind: 'statement',
        },
      ),
    );
    expect(endpoints.statements.correspondentProfiles.createExpectation).not.toHaveBeenCalled();
  });
});
