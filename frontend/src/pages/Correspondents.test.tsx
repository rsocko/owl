import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endpoints } from '../lib/api';
import Correspondents from './Correspondents';

vi.mock('../lib/api', () => ({
  endpoints: {
    statements: {
      correspondentProfiles: {
        inventory: vi.fn(),
        sync: vi.fn(),
      },
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
    document_count: 8,
    document_type_counts: {},
    title_pattern_count: 0,
    tag_family_counts: {},
    candidate_series_count: 2,
  },
  last_analyzed_at: null,
  last_reviewed_at: null,
  orphaned_at: null,
  relinked_from_correspondent_id: null,
  created_at: null,
  updated_at: null,
};

describe('Correspondents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(endpoints.statements.correspondentProfiles.inventory).mockResolvedValue([
      {
        profile,
        expectation_count: 2,
        suggested_expectation_count: 1,
        statement_series_count: 2,
        analysis_stale: true,
        priority_reasons: ['unreviewed_profile', 'stale_analysis', 'statement_history'],
        metadata_inconsistency_count: null,
        unmatched_external_candidate_count: null,
      },
    ]);
    vi.mocked(endpoints.statements.correspondentProfiles.sync).mockResolvedValue({
      created: 1,
      updated: 0,
      orphaned: 0,
    });
  });

  it('shows prioritized supported signals without inventing unavailable counts', async () => {
    render(
      <MemoryRouter>
        <Correspondents />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Example Bank')).toBeInTheDocument();
    expect(screen.getAllByText('Unreviewed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Stale analysis').length).toBeGreaterThan(0);
    expect(screen.getByText('Statement history')).toBeInTheDocument();
    expect(screen.queryByText(/unmatched/i)).not.toBeInTheDocument();
  });

  it('synchronizes Paperless identities and reloads the inventory', async () => {
    render(
      <MemoryRouter>
        <Correspondents />
      </MemoryRouter>,
    );
    await screen.findByText('Example Bank');

    fireEvent.click(screen.getByRole('button', { name: 'Sync from Paperless' }));

    await waitFor(() => expect(endpoints.statements.correspondentProfiles.sync).toHaveBeenCalledOnce());
    await waitFor(() => expect(endpoints.statements.correspondentProfiles.inventory).toHaveBeenCalledTimes(2));
  });
});
