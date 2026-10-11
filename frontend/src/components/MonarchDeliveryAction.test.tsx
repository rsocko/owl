import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MonarchDeliveryAction from './MonarchDeliveryAction';

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  send: vi.fn(),
  reconcile: vi.fn(),
  retry: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    documents: {
      monarchDelivery: mocks,
    },
  },
}));

const notSubmitted = {
  schema_version: '1.0',
  delivery_ref: 'safe-ref',
  state: 'not_submitted',
  attempt_count: 0,
  can_send: true,
  can_reconcile: false,
  can_retry: false,
  display_tag_pending: false,
};

describe('MonarchDeliveryAction', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((mock) => mock.mockReset());
    mocks.status.mockResolvedValue(notSubmitted);
  });

  it('requires explicit confirmation before sending', async () => {
    mocks.send.mockResolvedValue({
      ...notSubmitted,
      state: 'acknowledged',
      can_send: false,
      attempt_count: 1,
    });
    render(<MonarchDeliveryAction documentId={42} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Send to Monarch' }));
    expect(mocks.send).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getAllByRole('button', { name: 'Send to Monarch' })[1],
    );
    await waitFor(() => expect(mocks.send).toHaveBeenCalledWith(42));
    expect(await screen.findByRole('button', { name: 'Sent to Monarch' })).toBeDisabled();
  });

  it('reconciles unknown outcomes without presenting a blind send action', async () => {
    mocks.status.mockResolvedValue({
      ...notSubmitted,
      state: 'unknown',
      can_send: false,
      can_reconcile: true,
      can_retry: true,
      reason_code: 'submit_outcome_unknown',
    });
    mocks.reconcile.mockResolvedValue({
      ...notSubmitted,
      state: 'retryable',
      attempt_count: 1,
      can_send: false,
      can_reconcile: true,
      can_retry: true,
    });
    render(<MonarchDeliveryAction documentId={42} />);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Reconcile Monarch status' }),
    );

    await waitFor(() => expect(mocks.reconcile).toHaveBeenCalledWith(42));
    expect(mocks.retry).not.toHaveBeenCalled();
    expect(
      await screen.findByRole('button', { name: 'Retry Monarch send' }),
    ).toBeEnabled();
  });

  it('keeps recovery-origin documents blocked', async () => {
    mocks.status.mockResolvedValue({
      ...notSubmitted,
      state: 'blocked_recovery',
      can_send: false,
      reason_code: 'monarch_recovery_loop_prevented',
    });
    render(<MonarchDeliveryAction documentId={42} />);

    expect(
      await screen.findByRole('button', { name: 'Monarch recovery document' }),
    ).toBeDisabled();
  });

  it('shows the default-false write gate as disabled', async () => {
    mocks.status.mockResolvedValue({
      ...notSubmitted,
      can_send: false,
    });
    render(<MonarchDeliveryAction documentId={42} />);

    expect(
      await screen.findByRole('button', { name: 'Monarch send disabled' }),
    ).toBeDisabled();
  });
});
