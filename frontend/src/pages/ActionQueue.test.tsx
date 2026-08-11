import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ActionQueue from './ActionQueue';
import { customReminderUntil, reminderUntil } from './actionReminder';
import { buildQueueRunBody } from './actionQueueRunBody';
import { TooltipProvider } from '../components/ui';

const { statusMock, actionsMock, updateActionMock, feedbackMock } = vi.hoisted(() => ({
  statusMock: vi.fn(),
  actionsMock: vi.fn(),
  updateActionMock: vi.fn(),
  feedbackMock: vi.fn(),
}));

vi.mock('../hooks/useStreamingAction', () => ({
  useStreamingAction: () => [{ error: null }, vi.fn(), vi.fn()],
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    actionQueue: {
      check: vi.fn(),
      checkCustomFields: vi.fn(),
      run: vi.fn(),
      runStreamUrl: '/api/queue/run/stream',
      status: statusMock,
      actions: actionsMock,
      updateAction: updateActionMock,
      bulk: vi.fn(),
      backfill: vi.fn(),
      feedback: feedbackMock,
      settings: vi.fn(),
      updateSettings: vi.fn(),
      metadataTags: vi.fn(),
      metadataSavedViews: vi.fn(),
      metadataCorrespondents: vi.fn(),
      metadataDocumentTypes: vi.fn(),
    },
    statements: {
      documentThumb: vi.fn().mockReturnValue('/thumb.png'),
    },
    documents: {
      previewUrl: vi.fn().mockReturnValue('/documents/1'),
    },
  },
}));

const initialAction = {
  id: 1,
  document_id: 1,
  document_title: 'Electric Bill',
  action_type: 'PAY',
  title: 'Pay electric bill',
  summary: 'Monthly electric bill due',
  due_date: '2026-08-01',
  amount: 89.12,
  urgency: 'CRITICAL',
  confidence: 91,
  risk_score: 94,
  status: 'pending',
  correspondent: 'Utility Co',
  preview_url: '/documents/1/details',
  version: 3,
  created_at: '2026-07-20T10:00:00Z',
};

const updatedAction = {
  ...initialAction,
  action_type: 'TASK',
  title: 'Call utility company',
  summary: 'Confirm latest balance',
  due_date: '2026-08-15',
  amount: 123.45,
  urgency: 'HIGH',
};

beforeEach(() => {
  statusMock.mockReset();
  actionsMock.mockReset();
  updateActionMock.mockReset();
  feedbackMock.mockReset();
  statusMock.mockResolvedValue({
    status: 'idle',
    database: {
      pending: 1,
      acknowledged: 0,
      completed: 0,
      dismissed: 0,
      snoozed: 0,
      not_an_action: 0,
      total: 1,
    },
    progress: {},
  });
  actionsMock
    .mockResolvedValueOnce({ actions: [initialAction], total: 1 })
    .mockResolvedValueOnce({ actions: [updatedAction], total: 1 });
  updateActionMock.mockResolvedValue(updatedAction);
  feedbackMock.mockResolvedValue({});
});

describe('ActionQueue', () => {
  it('shows outcome-oriented actions instead of acknowledge and dismiss', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Done' })).toBeTruthy();
    });

    expect(screen.getByRole('button', { name: 'Remind me later…' })).toBeTruthy();
    expect(screen.getByRole('button', { name: "Won't do" })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'No action needed' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();
  });

  it('saves corrected action details from the drawer', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /pay electric bill/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /pay electric bill/i }));
    fireEvent.click(screen.getByRole('button', { name: /edit details/i }));

    fireEvent.change(screen.getByLabelText('Action type'), { target: { value: 'TASK' } });
    fireEvent.change(screen.getByLabelText('Task name'), { target: { value: 'Call utility company' } });
    fireEvent.change(screen.getByLabelText('Summary'), { target: { value: 'Confirm latest balance' } });
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-08-15' } });
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '123.45' } });
    fireEvent.change(screen.getByLabelText('Urgency'), { target: { value: 'HIGH' } });

    fireEvent.click(screen.getByRole('button', { name: /save details/i }));

    await waitFor(() => {
      expect(updateActionMock).toHaveBeenCalledWith('1', {
        version: 3,
        action_type: 'TASK',
        title: 'Call utility company',
        summary: 'Confirm latest balance',
        due_date: '2026-08-15',
        amount: 123.45,
        urgency: 'HIGH',
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Action details updated.')).toBeTruthy();
    });
  });

  it('explains Paperless metadata changes before marking no action needed', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'No action needed' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'No action needed' }));

    expect(screen.getByRole('dialog', { name: 'Mark as no action needed?' })).toHaveTextContent(
      'Document Amount is kept; Action Type, Due Date, Urgency, Summary, and Action Count are cleared.',
    );
    expect(feedbackMock).not.toHaveBeenCalled();
  });
});

describe('reminder dates', () => {
  it('sets tomorrow and next week to 9 AM local time', () => {
    const now = new Date(2026, 7, 10, 23, 0, 0);
    const tomorrow = new Date(reminderUntil('tomorrow', now));
    const nextWeek = new Date(reminderUntil('next_week', now));

    expect(tomorrow.getDate()).toBe(11);
    expect(tomorrow.getHours()).toBe(9);
    expect(nextWeek.getDate()).toBe(17);
    expect(nextWeek.getHours()).toBe(9);
  });

  it('turns a custom date into a 9 AM local reminder', () => {
    const now = new Date(2026, 7, 10, 12, 0, 0);
    const reminder = new Date(customReminderUntil('2026-08-20', now) ?? '');

    expect(reminder.getFullYear()).toBe(2026);
    expect(reminder.getMonth()).toBe(7);
    expect(reminder.getDate()).toBe(20);
    expect(reminder.getHours()).toBe(9);
    expect(customReminderUntil('', now)).toBeNull();
    expect(customReminderUntil('2026-08-10', now)).toBeNull();
    expect(customReminderUntil('not-a-date', now)).toBeNull();
  });
});

describe('buildQueueRunBody', () => {
  it('does not force a normal live rerun', () => {
    expect(buildQueueRunBody(false)).toEqual({ dry_run: false });
  });

  it('does not force a dry run', () => {
    expect(buildQueueRunBody(true, 1234)).toEqual({ dry_run: true });
  });

  it('forces an explicit single-document live rerun', () => {
    expect(buildQueueRunBody(false, 1234)).toEqual({ dry_run: false, force: true });
  });
});
