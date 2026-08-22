import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DuplicateDetail from './DuplicateDetail';

const { getMock, resolveMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  resolveMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    duplicates: {
      get: getMock,
      resolve: resolveMock,
    },
  },
}));

const pair = {
  id: 'pair-1',
  doc_a_id: 10,
  doc_b_id: 20,
  similarity_score: 0.9,
  breakdown: { invoice_number: 1 },
  status: 'pending',
  primary_doc_id: null,
  resolved_at: null,
  created_at: '2026-08-01T00:00:00Z',
  doc_a_metadata: { title: 'Original Bill', provider: 'Utility' },
  doc_b_metadata: { title: 'Second Notice', provider: 'Utility' },
  relationship_proposal: null,
};

beforeEach(() => {
  getMock.mockReset();
  resolveMock.mockReset();
  getMock.mockResolvedValue(pair);
  resolveMock.mockResolvedValue({
    duplicate: { ...pair, status: 'not_duplicate', resolved_at: '2026-08-22T00:00:00Z' },
    relationship: {
      relationship_type: 'follows',
      priority_adjustment: 12,
      priority_explanation: 'Priority +12: second notice',
    },
    projection: { synced: true, error: null },
  });
});

describe('DuplicateDetail related-document resolution', () => {
  it('keeps both documents and creates a typed relationship', async () => {
    render(<DuplicateDetail pairId="pair-1" />);

    fireEvent.click(await screen.findByRole('button', { name: /Keep Both and Link/i }));

    await waitFor(() => {
      expect(resolveMock).toHaveBeenCalledWith('pair-1', {
        resolution: 'related',
        primary_doc_id: 10,
        relationship_type: 'follows',
      });
    });
    expect(await screen.findByText(/Linked as/)).toBeTruthy();
    expect(screen.getByText(/Priority \+12: second notice/)).toBeTruthy();
  });

  it('supports choosing a superseding relationship', async () => {
    render(<DuplicateDetail pairId="pair-1" />);

    fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'supersedes' } });
    fireEvent.click(screen.getByRole('button', { name: /Keep Both and Link/i }));

    await waitFor(() => {
      expect(resolveMock).toHaveBeenCalledWith(
        'pair-1',
        expect.objectContaining({ relationship_type: 'supersedes' }),
      );
    });
  });
});
