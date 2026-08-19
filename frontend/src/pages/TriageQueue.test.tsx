import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import TriageQueue from './TriageQueue';

const { queueMock, statsMock } = vi.hoisted(() => ({
  queueMock: vi.fn(),
  statsMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    triage: {
      queue: queueMock,
      stats: statsMock,
      resolve: vi.fn(),
      defer: vi.fn(),
      dismiss: vi.fn(),
      undo: vi.fn(),
      bulk: vi.fn(),
      bulkConfirmThreshold: vi.fn(),
      populate: vi.fn(),
    },
  },
}));

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  queueMock.mockReset();
  statsMock.mockReset();
  vi.stubGlobal('ResizeObserver', ResizeObserverStub);
  queueMock.mockResolvedValue({
    items: [{
      id: 'review-1',
      item_type: 'other',
      priority: 50,
      status: 'pending',
      source: 'auto_flag',
      target_type: 'other',
      target_id: 'target-1',
      reason: 'Uncertain system decision',
      metadata: null,
      deferred_until: null,
      resolved_at: null,
      resolved_action: null,
      created_at: '2026-08-10T12:00:00Z',
    }],
    count: 1,
    offset: 0,
    limit: 200,
  });
  statsMock.mockResolvedValue({
    total: 1,
    by_type: { other: 1 },
    by_status: { pending: 1 },
    pending: 1,
  });
});

describe('Needs Review actions', () => {
  it('uses decision language and keeps Dismiss under More', async () => {
    render(
      <MemoryRouter>
        <TriageQueue />
      </MemoryRouter>,
    );

    const item = await screen.findByText('OTHER: target-1');
    fireEvent.click(item);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Accept' })).toBeTruthy();
    });

    expect(screen.getByRole('button', { name: 'Reject' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Review later' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'More…' }));
    expect(await screen.findByRole('button', { name: 'Dismiss' })).toBeTruthy();
  });

  it('initializes the queue filter from a validated type query parameter', async () => {
    render(
      <MemoryRouter initialEntries={['/triage?type=eob_match_review']}>
        <TriageQueue />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(queueMock).toHaveBeenCalledWith('type=eob_match_review&status=pending&limit=200');
    });
  });

  it('ignores unknown type query parameters', async () => {
    render(
      <MemoryRouter initialEntries={['/triage?type=not-a-real-queue']}>
        <TriageQueue />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(queueMock).toHaveBeenCalledWith('status=pending&limit=200');
    });
  });
});
