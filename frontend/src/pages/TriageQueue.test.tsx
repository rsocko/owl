import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import TriageQueue from './TriageQueue';

const { queueMock, statsMock, resolveMock } = vi.hoisted(() => ({
  queueMock: vi.fn(),
  statsMock: vi.fn(),
  resolveMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    triage: {
      queue: queueMock,
      stats: statsMock,
      resolve: resolveMock,
      defer: vi.fn(),
      dismiss: vi.fn(),
      undo: vi.fn(),
      bulk: vi.fn(),
      bulkConfirmThreshold: vi.fn(),
      populate: vi.fn(),
    },
    documents: {
      metadata: vi.fn().mockResolvedValue({ title: 'Uncertain bill' }),
      thumbnailUrl: vi.fn().mockReturnValue('/thumbnail/777'),
      downloadUrl: vi.fn().mockReturnValue('/download/777'),
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
  resolveMock.mockReset();
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
  resolveMock.mockResolvedValue({ action_ready: true, review_state: 'ready' });
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

  it('deep-links to an action review and submits edited correction details', async () => {
    queueMock.mockResolvedValue({
      items: [{
        id: 'action-review-1',
        item_type: 'action_classification',
        priority: 80,
        status: 'pending',
        source: 'action_queue',
        target_type: 'action',
        target_id: '42',
        reason: 'Low-confidence action classification',
        metadata: {
          action_type: 'PAY',
          title: 'Pay guessed bill',
          summary: 'Original guess',
          amount: 25,
          document_id: 777,
        },
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
      by_type: { action_classification: 1 },
      by_status: { pending: 1 },
      pending: 1,
    });

    render(
      <MemoryRouter initialEntries={['/triage?type=action_classification&item=action-review-1']}>
        <TriageQueue />
      </MemoryRouter>,
    );

    const type = await screen.findByLabelText('Corrected action type');
    expect(screen.queryByRole('checkbox')).toBeNull();
    fireEvent.change(type, { target: { value: 'FILE' } });
    fireEvent.change(screen.getByLabelText('Corrected task name'), {
      target: { value: 'File annual statement' },
    });
    fireEvent.change(screen.getByLabelText('Corrected amount'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Correct and continue' }));

    await waitFor(() => {
      expect(resolveMock).toHaveBeenCalledWith('action-review-1', {
        action: 'correct',
        payload: {
          action_type: 'FILE',
          title: 'File annual statement',
          summary: 'Original guess',
          due_date: null,
          amount: null,
        },
      });
    });
  });

  it('disables action-review resolution controls after the item is resolved', async () => {
      queueMock.mockResolvedValue({
        items: [{
          id: 'action-review-resolved',
          item_type: 'action_classification',
          priority: 80,
          status: 'confirmed',
          source: 'action_queue',
          target_type: 'action',
          target_id: '42',
          reason: 'Confirmed',
          metadata: {
            action_type: 'PAY',
            action_title: 'Pay utility bill',
            document_id: 777,
          },
          deferred_until: null,
          resolved_at: '2026-08-11T12:00:00Z',
          resolved_action: 'confirm',
          created_at: '2026-08-10T12:00:00Z',
        }],
        count: 1,
        offset: 0,
        limit: 200,
      });

    render(
      <MemoryRouter>
        <TriageQueue />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: 'Confirm action' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Correct and continue' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'No action needed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Re-evaluate' })).toBeDisabled();
  });
});
