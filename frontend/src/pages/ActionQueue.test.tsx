import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import ActionQueue from './ActionQueue';
import { customReminderUntil, reminderUntil } from './actionReminder';
import { buildBackfillBody, buildQueueRunBody } from './actionQueueRunBody';
import { TooltipProvider } from '../components/ui';

const {
  statusMock,
  actionsMock,
  updateActionMock,
  refreshActionMock,
  feedbackMock,
  metadataTagsMock,
  runPipelineStreamMock,
} = vi.hoisted(() => ({
  statusMock: vi.fn(),
  actionsMock: vi.fn(),
  updateActionMock: vi.fn(),
  refreshActionMock: vi.fn(),
  feedbackMock: vi.fn(),
  metadataTagsMock: vi.fn(),
  runPipelineStreamMock: vi.fn(),
}));

vi.mock('../hooks/useStreamingAction', () => ({
  useStreamingAction: () => [{ error: null, running: false, progress: null }, runPipelineStreamMock, vi.fn()],
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
      refreshAction: refreshActionMock,
      bulk: vi.fn(),
      backfill: vi.fn(),
      refreshMetadata: vi.fn(),
      feedback: feedbackMock,
      settings: vi.fn(),
      updateSettings: vi.fn(),
      metadataTags: metadataTagsMock,
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
  recommended_cta: {
    id: 'pay-online',
    label: 'Pay online',
    url: 'https://billing.example/pay',
  },
  extracted_data: {
    reference_number: 'INV-42',
    email: 'billing@example.com',
    links: [
      { url: 'https://billing.example/pay', label: 'Pay online', purpose: 'payment' },
      { url: 'https://billing.example/help', label: 'Billing help', purpose: 'support' },
      { url: 'javascript:alert(1)', label: 'Unsafe', purpose: 'other' },
    ],
  },
  preview_url: '/documents/1/details',
  tags: ['9', '2', '343', '4'],
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

beforeAll(() => {
  if (!globalThis.ResizeObserver) {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
});

beforeEach(() => {
  statusMock.mockReset();
  actionsMock.mockReset();
  updateActionMock.mockReset();
  refreshActionMock.mockReset();
  feedbackMock.mockReset();
  metadataTagsMock.mockReset();
  runPipelineStreamMock.mockReset();
  metadataTagsMock.mockResolvedValue({
    tags: [
      { id: 9, name: 'Inbox', colour: '#1f6feb' },
      { id: 2, name: 'Utilities', colour: '#fbca04' },
      { id: 343, name: 'Bills', colour: '#d73a4a' },
      { id: 4, name: 'Household', colour: '#7057ff' },
    ],
  });
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
  refreshActionMock.mockResolvedValue({
    ...initialAction,
    document_title: 'Corrected Electric Statement',
    correspondent: 'Updated Utility',
    document_date: '2026-02-03',
    document_type: 'Statement',
    tags: ['Reviewed'],
    version: 4,
  });
  feedbackMock.mockResolvedValue({});
});

describe('ActionQueue', () => {
  it('shows Paperless tag names in their configured colors', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    const inbox = await screen.findByText('Inbox');
    expect(inbox).toHaveStyle({ backgroundColor: '#1f6feb', color: '#ffffff' });
    expect(screen.getByText('Utilities')).toBeTruthy();
    expect(screen.getByText('Bills')).toBeTruthy();
    expect(screen.queryByText('343')).toBeNull();
    expect(screen.getByText('+1')).toBeTruthy();
  });

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

  it('labels the action list for the selected status', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    await screen.findByText('Pending actions (1)');
    fireEvent.click(screen.getByRole('radio', { name: 'Remind later (0)' }));

    expect(await screen.findByText('Remind later actions (1)')).toBeTruthy();
  });

  it('shows safe extracted links and useful action details', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /pay electric bill/i }));

    expect(screen.getByRole('link', { name: 'Pay online' })).toHaveAttribute(
      'href',
      'https://billing.example/pay',
    );
    expect(screen.getByRole('link', { name: 'Billing help' })).toHaveAttribute(
      'href',
      'https://billing.example/help',
    );
    expect(screen.queryByRole('link', { name: 'Unsafe' })).toBeNull();
    expect(screen.getByText('INV-42')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'billing@example.com' })).toHaveAttribute(
      'href',
      'mailto:billing@example.com',
    );
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

  it('refreshes the selected action metadata from Paperless', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /pay electric bill/i })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /pay electric bill/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh from Paperless' }));

    await waitFor(() => {
      expect(refreshActionMock).toHaveBeenCalledWith('1');
      expect(screen.getByText('Corrected Electric Statement')).toBeTruthy();
      expect(screen.getAllByText('Updated Utility')).not.toHaveLength(0);
      expect(screen.getAllByText('Statement')).not.toHaveLength(0);
      expect(screen.getAllByText('Reviewed')).not.toHaveLength(0);
    });
  });

  it('force re-runs analysis for the selected document', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /pay electric bill/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Re-run analysis' }));

    expect(runPipelineStreamMock).toHaveBeenCalledTimes(1);
    const [url, , request] = runPipelineStreamMock.mock.calls[0];
    expect(url).toBe('/api/queue/run/stream');
    expect(request).toMatchObject({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dry_run: false, force: true, document_id: 1 }),
    });
  });

  it('keeps table sorting and column filters after taking action on an item', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    const amountSort = await screen.findByRole('button', { name: 'Sort by Amount' });
    fireEvent.click(amountSort);
    fireEvent.click(screen.getByRole('button', { name: 'Filter by Type' }));
    fireEvent.click(await screen.findByRole('button', { name: 'PAY' }));
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));

    await waitFor(() => {
      expect(updateActionMock).toHaveBeenCalled();
      expect(screen.getByText('No results match the current filters')).toBeTruthy();
    });

    expect(screen.getByRole('button', { name: 'Sort by Amount' })).toHaveTextContent('▼');
    expect(screen.queryByText('Call utility company')).toBeNull();
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

describe('buildBackfillBody', () => {
  it('forces a live backfill so previously synced actions are repaired', () => {
    expect(buildBackfillBody(false)).toEqual({ dry_run: false, force: true });
  });

  it('forces the preview to match the live backfill candidate set', () => {
    expect(buildBackfillBody(true)).toEqual({ dry_run: true, force: true });
  });
});
