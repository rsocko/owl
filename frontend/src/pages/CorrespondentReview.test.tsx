import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CorrespondentReview from './CorrespondentReview';

const mocks = vi.hoisted(() => ({
  profiles: vi.fn(),
  analyze: vi.fn(),
  createExpectation: vi.fn(),
  updateProfile: vi.fn(),
  expectations: vi.fn(),
  previewPolicy: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    statements: {
      correspondentProfiles: mocks.profiles,
      acquisitionSources: vi.fn().mockResolvedValue([]),
      paperlessUrl: vi.fn().mockResolvedValue({ paperless_url: 'https://paperless.test' }),
      correspondentExpectations: mocks.expectations,
      analyzeCorrespondentProfile: mocks.analyze,
      createCorrespondentExpectation: mocks.createExpectation,
      updateCorrespondentProfile: mocks.updateProfile,
      syncCorrespondentProfiles: vi.fn(),
      analyzeCorrespondentProfiles: vi.fn(),
      relinkCorrespondentProfile: vi.fn(),
      updateDocumentExpectation: vi.fn(),
      previewDocumentExpectationPolicy: mocks.previewPolicy,
      createAcquisitionSource: vi.fn(),
    },
  },
}));

const suggestion = {
  suggestion_key: 'example-bank-checking',
  kind: 'statement',
  series_discriminator: 'Checking',
  statement_series_id: 'checking',
  expectation_mode: 'recurring',
  cadence: {
    frequency: 'monthly',
    expected_day: 3,
    availability_delay_days: 0,
    grace_period_days: 5,
  },
  evidence: {
    source: 'paperless',
    reason_codes: ['monthly_cadence'],
    confidence: 0.92,
    sample_size: 4,
    observed_from: '2026-01-03',
    observed_to: '2026-04-03',
  },
  title: {
    convention: {
      template: '{series} - {kind} - {period}',
      date_basis: 'period',
      example: 'Checking - Statement - 2026-04',
    },
    coverage: 1,
    exception_document_ids: [],
    examples: [{
      document_id: 4,
      before: 'Example Bank Statement April 2026',
      after: 'Checking - Statement - 2026-04',
      missing_fields: [],
    }],
    reason_codes: ['deterministic_template'],
  },
  metadata: {
    policy: { all_of: [7], any_of: [], none_of: [], required_document_type_id: 3 },
    tag_names: { 7: 'Finance' },
    required_document_type_name: 'Statement',
    confidence: 1,
    reason_codes: ['consistent_document_type'],
  },
  acquisition: {
    channel: 'unknown',
    reason_codes: ['ingestion_source_unavailable'],
  },
  document_ids: [1, 2, 3, 4],
  sample_document_ids: [2, 3, 4],
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.profiles.mockResolvedValue([{
    correspondent_id: 42,
    current_name: 'Example Bank',
    review_status: 'unreviewed',
    lifecycle_status: 'active',
    aliases: [],
    observed_summary: {
      document_count: 4,
      document_type_counts: { Statement: 4 },
      title_pattern_count: 1,
      tag_family_counts: { Finance: 4 },
      candidate_series_count: 1,
    },
    last_analyzed_at: null,
  }]);
  mocks.analyze.mockResolvedValue({
    correspondent_id: 42,
    correspondent_name: 'Example Bank',
    analyzed_at: '2026-08-23T00:00:00Z',
    observed_summary: {
      document_count: 4,
      document_type_counts: { Statement: 4 },
      title_pattern_count: 1,
      tag_family_counts: { Finance: 4 },
      candidate_series_count: 1,
    },
    suggestions: [suggestion],
  });
  mocks.createExpectation.mockResolvedValue({ id: 'expectation-1' });
  mocks.updateProfile.mockResolvedValue({});
  mocks.expectations.mockResolvedValue([]);
  mocks.previewPolicy.mockResolvedValue({
    expectation_id: 'expectation-1',
    correspondent_id: 42,
    matched_document_count: 1,
    compliant_document_count: 0,
    findings: [{
      preview_id: 'stable-preview',
      operation: {
        expectation_id: 'expectation-1',
        document_id: 4,
        expected: {
          title: 'Old statement title',
          tag_ids: [99],
          tag_names: ['Old'],
          document_type_id: 4,
          document_type_name: 'Invoice',
        },
        patch: {
          title: 'Checking - Statement - 2026-04',
          tags: [7],
          document_type: 3,
        },
      },
      proposed: {
        title: 'Checking - Statement - 2026-04',
        tag_ids: [7],
        tag_names: ['Finance'],
        document_type_id: 3,
        document_type_name: 'Statement',
      },
      violations: ['title_mismatch', 'forbidden_tags', 'wrong_document_type'],
      unresolved_violations: [],
      missing_title_fields: [],
    }],
  });
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/correspondents/42']}>
      <Routes>
        <Route path="/correspondents/:correspondentId" element={<CorrespondentReview />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Correspondent Review', () => {
  it('shows prioritized profiles and explainable review evidence', async () => {
    renderPage();

    expect((await screen.findAllByText('Example Bank')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /analyze history/i }));

    expect(await screen.findByText('Checking')).toBeInTheDocument();
    expect(screen.getByText('100% coverage · 0 exceptions')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open in paperless/i })).toHaveAttribute(
      'href',
      'https://paperless.test/documents/4/details',
    );

    fireEvent.change(screen.getByLabelText('Title convention'), {
      target: { value: '{series} - {period}' },
    });
    expect(screen.getByText('Checking - 2026-04')).toBeInTheDocument();
  });

  it('confirms policy locally without invoking document metadata endpoints', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /analyze history/i }));
    fireEvent.click(await screen.findByRole('button', { name: /confirm expectation/i }));

    await waitFor(() => {
      expect(mocks.createExpectation).toHaveBeenCalledWith(
        42,
        expect.objectContaining({
          statement_series_id: 'checking',
          status: 'confirmed',
          expectation_mode: 'recurring',
        }),
      );
      expect(mocks.updateProfile).toHaveBeenCalledWith(
        42,
        expect.objectContaining({ review_status: 'reviewed' }),
      );
    });
  });

  it('masks long account-like values and locks ignored profiles', async () => {
    mocks.profiles.mockResolvedValue([{
      correspondent_id: 42,
      current_name: 'Example Bank 1234567890123456',
      review_status: 'ignored',
      lifecycle_status: 'active',
      aliases: [],
      observed_summary: {
        document_count: 4,
        document_type_counts: { Statement: 4 },
        title_pattern_count: 1,
        tag_family_counts: { Finance: 4 },
        candidate_series_count: 1,
      },
      last_analyzed_at: null,
    }]);

    renderPage();

    expect((await screen.findAllByText('Example Bank [redacted]')).length).toBeGreaterThan(0);
    expect(screen.queryByText('1234567890123456')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /analyze history/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /mark reviewed/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /return to review/i })).toBeEnabled();
  });

  it('shows exact old and proposed metadata from a read-only policy preview', async () => {
    mocks.expectations.mockResolvedValue([{
      id: 'expectation-1',
      correspondent_id: 42,
      kind: 'statement',
      statement_series_id: 'checking',
      series_discriminator: 'Checking',
      expectation_mode: 'recurring',
      status: 'confirmed',
      cadence: suggestion.cadence,
      evidence: suggestion.evidence,
      title_convention: suggestion.title.convention,
      metadata_policy: suggestion.metadata.policy,
    }]);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /preview violations/i }));

    expect(await screen.findByText('Old statement title')).toBeInTheDocument();
    expect(screen.getByText('Checking - Statement - 2026-04')).toBeInTheDocument();
    expect(screen.getByText('Old')).toBeInTheDocument();
    expect(screen.getByText('Finance')).toBeInTheDocument();
    expect(screen.getByText('Invoice')).toBeInTheDocument();
    expect(screen.getByText('Statement')).toBeInTheDocument();
    expect(mocks.previewPolicy).toHaveBeenCalledWith('expectation-1');
  });
});
