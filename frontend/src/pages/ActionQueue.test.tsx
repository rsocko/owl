import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import ActionQueue, {
  ACTION_GROUP_BATCH_SIZE,
  deadlineBucket,
  groupAndSortActions,
} from './ActionQueue';
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
  metadataCorrespondentsMock,
  runPipelineStreamMock,
} = vi.hoisted(() => ({
  statusMock: vi.fn(),
  actionsMock: vi.fn(),
  updateActionMock: vi.fn(),
  refreshActionMock: vi.fn(),
  feedbackMock: vi.fn(),
  metadataTagsMock: vi.fn(),
  metadataCorrespondentsMock: vi.fn(),
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
      metadataCorrespondents: metadataCorrespondentsMock,
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
  updated_at: '2026-07-21T10:00:00Z',
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
  window.localStorage.removeItem('owl.actionQueue.view');
  statusMock.mockReset();
  actionsMock.mockReset();
  updateActionMock.mockReset();
  refreshActionMock.mockReset();
  feedbackMock.mockReset();
  metadataTagsMock.mockReset();
  metadataCorrespondentsMock.mockReset();
  runPipelineStreamMock.mockReset();
  metadataTagsMock.mockResolvedValue({
    tags: [
      { id: 9, name: 'Inbox', colour: '#1f6feb' },
      { id: 2, name: 'Utilities', colour: '#fbca04' },
      { id: 343, name: 'Bills', colour: '#d73a4a' },
      { id: 4, name: 'Household', colour: '#7057ff' },
    ],
  });
  metadataCorrespondentsMock.mockResolvedValue({
    correspondents: [
      { id: 74, name: 'University of Michigan', suggested: true },
      { id: 12, name: 'Utility Co', suggested: false },
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
  it('preserves Paperless metadata in the document flyout', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /open pay electric bill/i }));
    const inbox = await screen.findByText('Inbox');
    expect(inbox).toHaveStyle({ backgroundColor: '#1f6feb', color: '#ffffff' });
    expect(screen.getByText('Utilities')).toBeTruthy();
    expect(screen.getByText('Bills')).toBeTruthy();
    expect(screen.getByText('Household')).toBeTruthy();
    expect(screen.queryByText('343')).toBeNull();
  });

  it('shows outcome-oriented actions instead of acknowledge and dismiss', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /open pay electric bill/i }));
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: 'Done' }).length).toBeGreaterThan(0);
    });

    expect(screen.getByRole('button', { name: 'Remind me later…' })).toBeTruthy();
    expect(screen.getByRole('button', { name: "Won't do" })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'No action needed' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();
  });

  it('requires source filing for FILE work instead of generic completion', async () => {
    const fileAction = {
      ...initialAction,
      action_type: 'FILE',
      title: 'File statement',
      recommended_cta: null,
    };
    actionsMock.mockReset();
    actionsMock.mockResolvedValue({ actions: [fileAction], total: 1 });

    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /open file statement/i }));
    expect((await screen.findAllByRole('button', { name: 'File in Paperless' })).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Remind me later…' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull();

    fireEvent.click(screen.getByRole('checkbox', { name: /select visible/i }));
    expect(screen.getByRole('button', { name: 'Done' })).toBeDisabled();
  });

  it('groups pending work by deadline instead of historical progress', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    expect(await screen.findByRole('button', { name: /overdue/i })).toBeTruthy();
    expect(screen.queryByText(/of 1 resolved/i)).toBeNull();
    expect(screen.queryByText('Check services')).toBeNull();
    expect(screen.getByRole('link', { name: 'Operations' })).toHaveAttribute(
      'href',
      '#/action-queue/operations',
    );
  });

  it('persists the table preference and restores sortable columns', async () => {
    const laterAction = {
      ...initialAction,
      id: 2,
      title: 'Arrange payment',
      due_date: '2026-09-01',
    };
    actionsMock.mockReset();
    actionsMock.mockResolvedValue({ actions: [initialAction, laterAction], total: 2 });

    const firstRender = render(<TooltipProvider><ActionQueue /></TooltipProvider>);
    expect(await screen.findByRole('button', { name: /overdue/i })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Table' }));
    expect(window.localStorage.getItem('owl.actionQueue.view')).toBe('table');
    expect(screen.getByRole('button', { name: 'Sort by Due' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sort by Action' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Sort by Action' }));
    const sortedRows = screen.getAllByRole('row').slice(1);
    expect(within(sortedRows[0]).getByText('Arrange payment')).toBeTruthy();

    firstRender.unmount();
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);
    expect(await screen.findByRole('button', { name: 'Sort by Due' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /overdue/i })).toBeNull();
  });

  it('uses newest-first table history with Re-open for completed actions and bulk selection', async () => {
    const olderDone = {
      ...initialAction,
      id: 2,
      title: 'Older completed action',
      status: 'completed',
      completed_at: '2026-08-20T10:00:00Z',
      updated_at: '2026-08-20T10:00:00Z',
    };
    const newerDone = {
      ...initialAction,
      id: 3,
      title: 'Newer completed action',
      status: 'completed',
      completed_at: null,
      updated_at: '2026-08-24T10:00:00Z',
    };
    statusMock.mockResolvedValue({
      status: 'idle',
      database: {
        pending: 1,
        acknowledged: 0,
        completed: 2,
        dismissed: 0,
        snoozed: 0,
        not_an_action: 0,
        total: 3,
      },
      progress: {},
    });
    actionsMock.mockReset();
    actionsMock.mockImplementation(async (params: string) => (
      params.includes('status=completed')
        ? { actions: [olderDone, newerDone], total: 2 }
        : { actions: [initialAction], total: 1 }
    ));

    render(<TooltipProvider><ActionQueue /></TooltipProvider>);
    fireEvent.click(await screen.findByRole('radio', { name: 'Done (2)' }));

    const rows = await screen.findAllByRole('row');
    expect(within(rows[1]).getByText('Newer completed action')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Grouped' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Table' }));
    expect(window.localStorage.getItem('owl.actionQueue.view')).toBe('grouped');
    expect(screen.getAllByRole('button', { name: 'Re-open' })).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull();

    fireEvent.click(screen.getByRole('checkbox', { name: /select visible/i }));
    expect(screen.getAllByRole('button', { name: 'Re-open' })).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull();

    fireEvent.click(within(rows[1]).getByRole('button', { name: /open newer completed action/i }));
    expect(screen.getAllByRole('button', { name: 'Re-open' }).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull();
  });

  it('includes no-action history in the All table', async () => {
    const noAction = {
      ...initialAction,
      id: 4,
      title: 'Informational document',
      status: 'not_an_action',
      updated_at: '2026-08-24T11:00:00Z',
    };
    actionsMock.mockReset();
    actionsMock.mockImplementation(async (params: string) => (
      params.startsWith('limit=200')
        ? { actions: [initialAction, noAction], total: 2 }
        : { actions: [initialAction], total: 1 }
    ));

    render(<TooltipProvider><ActionQueue /></TooltipProvider>);
    fireEvent.click(await screen.findByRole('radio', { name: /All \(1\)/i }));

    expect(await screen.findByText('Informational document')).toBeTruthy();
    expect(actionsMock).toHaveBeenCalledWith('limit=200&include_resolved_no_action=true');
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
    fireEvent.click(screen.getByRole('button', { name: /correct details/i }));

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

  it('shows Paperless correspondent suggestions first and saves the selection', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /pay electric bill/i }));
    fireEvent.click(screen.getByRole('button', { name: /correct details/i }));
    fireEvent.focus(await screen.findByRole('combobox', { name: 'Correspondent' }));
    fireEvent.click(await screen.findByRole('option', {
      name: 'University of Michigan (Paperless suggestion)',
    }));
    fireEvent.click(screen.getByRole('button', { name: /save details/i }));

    await waitFor(() => {
      expect(metadataCorrespondentsMock).toHaveBeenCalledWith(1);
      expect(updateActionMock).toHaveBeenCalledWith('1', {
        version: 3,
        correspondent: 'University of Michigan',
      });
    });
  });

  it('explains Paperless metadata changes before marking no action needed', async () => {
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('button', { name: /open pay electric bill/i }));
    fireEvent.click(screen.getByRole('button', { name: 'No action needed' }));

    expect(screen.getByRole('dialog', { name: 'Mark as no action needed?' })).toHaveTextContent(
      'keeps durable facts such as Document Amount and masked Account Identifier',
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

  it('persists quick type filters and combines them with search', async () => {
    window.localStorage.removeItem('owl.actionQueue.typeFilter');
    render(<TooltipProvider><ActionQueue /></TooltipProvider>);

    fireEvent.click(await screen.findByRole('radio', { name: 'Respond' }));
    expect(window.localStorage.getItem('owl.actionQueue.typeFilter')).toBe('respond');
    expect(screen.queryByRole('button', { name: /open pay electric bill/i })).toBeNull();

    fireEvent.click(screen.getByRole('radio', { name: 'Pay' }));
    fireEvent.change(screen.getByLabelText('Search actions'), { target: { value: 'missing' } });
    expect(screen.queryByRole('button', { name: /open pay electric bill/i })).toBeNull();
  });
});

describe('action deadline grouping', () => {
  const base = {
    document_title: 'Document',
    title: 'Action',
    status: 'pending',
  };

  it('uses exact deadline buckets and deadline-first deterministic ordering', () => {
    const now = new Date(2026, 7, 24, 12);
    const actions = [
      { ...base, id: 1, action_type: 'FILE', due_date: '2026-08-24', created_at: '2026-08-24T12:00:00Z' },
      { ...base, id: 2, action_type: 'PAY', due_date: '2026-08-24', created_at: '2026-08-20T12:00:00Z' },
      { ...base, id: 3, action_type: 'SIGN', due_date: '2026-08-26', created_at: '2026-08-23T12:00:00Z' },
      { ...base, id: 4, action_type: 'PAY', due_date: '2026-08-25', created_at: '2026-08-01T12:00:00Z' },
      { ...base, id: 5, action_type: 'RESPOND', due_date: null, created_at: '2026-08-24T13:00:00Z' },
    ];

    const grouped = groupAndSortActions(actions, now);
    expect(grouped.today.map((item) => item.id)).toEqual([2, 1]);
    expect(grouped.next7.map((item) => item.id)).toEqual([4, 3]);
    expect(grouped.no_due_date.map((item) => item.id)).toEqual([5]);
    expect(deadlineBucket({ ...base, id: 6, due_date: '2026-08-23' }, now)).toBe('overdue');
  });

  it('keeps the initial group batch manageable', () => {
    expect(ACTION_GROUP_BATCH_SIZE).toBeGreaterThanOrEqual(10);
    expect(ACTION_GROUP_BATCH_SIZE).toBeLessThanOrEqual(20);
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
