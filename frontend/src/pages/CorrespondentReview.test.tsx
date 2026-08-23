import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import CorrespondentReview from './CorrespondentReview';

const mocks = vi.hoisted(() => ({
  profiles: vi.fn(),
  analyze: vi.fn(),
  createExpectation: vi.fn(),
  updateProfile: vi.fn(),
  externalCandidates: vi.fn(),
  reviewCandidate: vi.fn(),
  syncCandidates: vi.fn(),
  expectations: vi.fn(),
  previewPolicy: vi.fn(),
  applyPolicy: vi.fn(),
  undoPolicy: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    actionQueue: {
      metadataTags: vi.fn().mockResolvedValue({
        tags: [
          { id: 7, name: 'Finance' },
          { id: 280, name: 'Work Expenses' },
        ],
      }),
      metadataDocumentTypes: vi.fn().mockResolvedValue({
        document_types: [
          { id: 2, name: 'Receipt' },
          { id: 3, name: 'Statement' },
        ],
      }),
    },
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
      externalCandidates: mocks.externalCandidates,
      externalCandidateConnection: vi.fn().mockResolvedValue({
        configured: true,
        connector_ref: 'opaque-connector',
      }),
      syncExternalCandidates: mocks.syncCandidates,
      reviewExternalCandidate: mocks.reviewCandidate,
      previewDocumentExpectationPolicy: mocks.previewPolicy,
      applyDocumentExpectationPolicy: mocks.applyPolicy,
      undoDocumentExpectationPolicy: mocks.undoPolicy,
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
  mocks.externalCandidates.mockResolvedValue([]);
  mocks.reviewCandidate.mockResolvedValue({});
  mocks.syncCandidates.mockResolvedValue({
    source_generation: 'generation-2',
    active_candidates: 1,
    deactivated_candidates: 0,
    idempotent: false,
  });
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
  mocks.applyPolicy.mockResolvedValue({
    expectation_id: 'expectation-1',
    results: [{
      preview_id: 'stable-preview',
      document_id: 4,
      status: 'succeeded',
      audit_event_id: 'audit-1',
      message: 'Approved metadata correction applied.',
    }],
  });
  mocks.undoPolicy.mockResolvedValue({
    preview_id: 'stable-preview',
    document_id: 4,
    status: 'succeeded',
    audit_event_id: 'undo-1',
    message: 'Correction undone without changing unrelated metadata.',
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
    expect(screen.getAllByText('Finance').length).toBeGreaterThan(0);
    expect(screen.getByRole('combobox', { name: 'Required document type' })).toHaveValue('Statement');
    expect(screen.queryByText('All required tag IDs')).not.toBeInTheDocument();
    expect(screen.queryByText('Required document type ID')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open in paperless/i })).toHaveAttribute(
      'href',
      'https://paperless.test/documents/4/details',
    );

    fireEvent.change(screen.getByLabelText('Title convention'), {
      target: { value: '{series} - {period}' },
    });
    expect(screen.getByText('Checking - 2026-04')).toBeInTheDocument();
  });

  it('groups the review queue and provides candidate section navigation', async () => {
    mocks.externalCandidates.mockResolvedValue([{
      id: 'candidate-1',
      kind: 'accountStatementCandidate',
      active: true,
      display_hint: 'Credit account',
      confidence: 0.6,
      basis: ['active_non_cash_account'],
      outcome: 'unreviewed',
      correspondent_id: null,
      likely_multiple_statement_series: false,
      recurrence_evidence: 'high',
    }]);

    const { container } = renderPage();

    expect((await screen.findAllByText('Unreviewed')).length).toBeGreaterThan(0);
    expect(container.querySelector('.correspondent-inventory-list')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /external candidates 1/i })).toHaveAttribute(
      'href',
      '#correspondent-external-candidates',
    );
    expect(screen.getByText('Account statement candidates')).toBeInTheDocument();
  });

  it('keeps the workspace visible while analysis refreshes profile data', async () => {
    const { container } = renderPage();
    await screen.findAllByText('Example Bank');
    const profilePayload = await mocks.profiles.mock.results[0].value;
    let finishRefresh!: (value: unknown) => void;
    mocks.profiles.mockReturnValueOnce(new Promise((resolve) => {
      finishRefresh = resolve;
    }));

    fireEvent.click(screen.getByRole('button', { name: /analyze history/i }));

    expect(await screen.findByText('Checking')).toBeInTheDocument();
    expect(container.querySelector('.skeleton-cards')).toBeInTheDocument();
    expect(container.querySelector('.skeleton-detail-panel')).not.toBeInTheDocument();

    finishRefresh(profilePayload);
    await waitFor(() => {
      expect(container.querySelector('.skeleton-cards')).not.toBeInTheDocument();
    });
  });

  it('submits selected metadata names as Paperless IDs', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /analyze history/i }));
    fireEvent.change(await screen.findByRole('combobox', { name: 'Any required tags' }), {
      target: { value: 'work' },
    });
    fireEvent.click(screen.getByRole('option', { name: 'Work Expenses' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Required document type' }), {
      target: { value: 'receipt' },
    });
    fireEvent.click(screen.getByRole('option', { name: 'Receipt' }));
    fireEvent.click(await screen.findByRole('button', { name: /confirm expectation/i }));

    await waitFor(() => {
      expect(mocks.createExpectation).toHaveBeenCalledWith(
        42,
        expect.objectContaining({
          statement_series_id: 'checking',
          status: 'confirmed',
          expectation_mode: 'recurring',
          document_type_id: 2,
          metadata_policy: {
            all_of: [7],
            any_of: [280],
            none_of: [],
            required_document_type_id: 2,
          },
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

  it('supports explicit external candidate review without inferring cadence', async () => {
    mocks.externalCandidates.mockResolvedValue([{
      id: 'candidate-1',
      kind: 'accountStatementCandidate',
      active: true,
      display_hint: 'Credit account',
      confidence: 0.6,
      basis: ['active_non_cash_account'],
      outcome: 'unreviewed',
      correspondent_id: null,
      likely_multiple_statement_series: false,
      recurrence_evidence: 'high',
    }]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /create suggestion/i }));

    await waitFor(() => {
      expect(mocks.reviewCandidate).toHaveBeenCalledWith('candidate-1', {
        outcome: 'suggested',
        correspondent_id: 42,
      });
    });
    expect(screen.getByText(/do not establish cadence/i)).toBeInTheDocument();
  });

  it('synchronizes a Tyrion generation from the review workspace', async () => {
    renderPage();

    fireEvent.change(await screen.findByLabelText('Tyrion source generation'), {
      target: { value: 'generation-2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /sync tyrion candidates/i }));

    await waitFor(() => {
      expect(mocks.syncCandidates).toHaveBeenCalledWith('generation-2');
    });
  });

  it('records documentless external evidence as durable not-expected policy', async () => {
    mocks.externalCandidates.mockResolvedValue([{
      id: 'candidate-1',
      kind: 'recurringDocumentCandidate',
      active: true,
      display_hint: 'Recurring expense',
      confidence: 0.6,
      basis: ['active_recurring_obligation'],
      outcome: 'unreviewed',
      correspondent_id: null,
      likely_multiple_statement_series: false,
      recurrence_evidence: 'none',
    }]);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /record not expected/i }));

    await waitFor(() => {
      expect(mocks.reviewCandidate).toHaveBeenCalledWith('candidate-1', {
        outcome: 'not_applicable',
        correspondent_id: 42,
      });
    });
    expect(screen.getByText(/recurring obligations do not create invoice/i)).toBeInTheDocument();
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

  it('applies only selected preview operations and offers bounded undo', async () => {
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
    fireEvent.click(await screen.findByRole('checkbox', { name: /select document 4/i }));
    fireEvent.change(screen.getByLabelText(/reason for checking/i), {
      target: { value: 'Reviewed exact metadata changes' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply selected/i }));

    await waitFor(() => expect(mocks.applyPolicy).toHaveBeenCalledWith(
      'expectation-1',
      {
        actor: 'user',
        reason: 'Reviewed exact metadata changes',
        operations: [{
          preview_id: 'stable-preview',
          operation: expect.objectContaining({ document_id: 4 }),
        }],
      },
    ));
    fireEvent.click(await screen.findByRole('button', { name: /undo correction/i }));
    await waitFor(() => expect(mocks.undoPolicy).toHaveBeenCalledWith(
      'audit-1',
      expect.objectContaining({
        actor: 'user',
        preview_id: 'stable-preview',
        operation: expect.objectContaining({ document_id: 4 }),
      }),
    ));
  });
});
