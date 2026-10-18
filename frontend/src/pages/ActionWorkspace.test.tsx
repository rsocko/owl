import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ActionWorkspace from './ActionWorkspace';

const {
  statusMock,
  actionsMock,
  updateActionMock,
  splitActionMock,
  bulkMock,
  feedbackMock,
  documentOcrMock,
  ocrDocumentDetailMock,
  ocrCandidatesMock,
  paymentCheckMock,
  checkPaymentMock,
  confirmPaymentAndCompleteMock,
} = vi.hoisted(() => ({
  statusMock: vi.fn(),
  actionsMock: vi.fn(),
  updateActionMock: vi.fn(),
  splitActionMock: vi.fn(),
  bulkMock: vi.fn(),
  feedbackMock: vi.fn(),
  documentOcrMock: vi.fn(),
  ocrDocumentDetailMock: vi.fn(),
  ocrCandidatesMock: vi.fn(),
  paymentCheckMock: vi.fn(),
  checkPaymentMock: vi.fn(),
  confirmPaymentAndCompleteMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    actionQueue: {
      status: statusMock,
      actions: actionsMock,
      updateAction: updateActionMock,
      splitAction: splitActionMock,
      bulk: bulkMock,
      feedback: feedbackMock,
      paymentCheck: paymentCheckMock,
      checkPayment: checkPaymentMock,
      confirmPaymentAndComplete: confirmPaymentAndCompleteMock,
    },
    intake: {
      documentOcr: documentOcrMock,
    },
    ocrQuality: {
      documentDetail: ocrDocumentDetailMock,
      candidates: {
        list: ocrCandidatesMock,
      },
    },
    statements: {
      documentThumb: (id: string) => `/thumb/${id}`,
    },
    documents: {
      previewUrl: (id: number) => `/preview/${id}`,
    },
  },
}));

const actions = [
  {
    id: 1,
    document_id: 101,
    document_title: 'Utility bill',
    action_type: 'PAY',
    title: 'Pay electric bill',
    summary: 'Pay the current balance.',
    payee_or_owner: 'City Utilities',
    reference: 'INV-101',
    due_date: '2026-10-20',
    amount: 82.45,
    confidence: 92,
    status: 'pending',
    correspondent: 'City Utilities',
    document_type: 'Invoice',
    document_date: '2026-10-10',
    version: 2,
    is_primary: true,
    linked_documents: [],
    history: [],
  },
  {
    id: 2,
    document_id: 101,
    document_title: 'Utility bill',
    action_type: 'SIGN',
    title: 'Sign autopay authorization',
    due_date: '2026-10-22',
    confidence: 81,
    status: 'pending',
    correspondent: 'City Utilities',
    document_type: 'Invoice',
    document_date: '2026-10-10',
    version: 1,
    is_primary: false,
    linked_documents: [],
    history: [
      {
        id: 20,
        event_type: 'created',
        created_at: '2026-10-11T09:00:00Z',
      },
    ],
  },
  {
    id: 3,
    document_id: 202,
    document_title: 'Insurance renewal',
    action_type: 'REVIEW',
    title: 'Review renewal options',
    due_date: '2026-10-30',
    confidence: 88,
    status: 'pending',
    correspondent: 'Example Insurance',
    document_type: 'Policy',
    document_date: '2026-10-12',
    version: 1,
    is_primary: true,
    linked_documents: [],
    history: [],
  },
];

function renderWorkspace() {
  return render(
    <MemoryRouter initialEntries={['/action-queue']}>
      <ActionWorkspace />
    </MemoryRouter>,
  );
}

describe('ActionWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    statusMock.mockResolvedValue({
      database: { pending: 3, snoozed: 0, completed: 0, dismissed: 0, not_an_action: 0 },
      read_only: false,
    });
    actionsMock.mockResolvedValue({ actions: actions.map((action) => ({ ...action })), total: actions.length });
    documentOcrMock.mockResolvedValue({ text: 'Extracted document text', quality_score: 86 });
    ocrDocumentDetailMock.mockImplementation((documentId: number) => Promise.resolve({
      document_id: documentId,
      overlay_score: 86,
      machine_score: 82,
      review_status: 'GOOD',
      quality_scorer_version: '2.0.0',
      assessed_at: '2026-10-17T10:00:00Z',
      reasons: [],
      document_profile: { page_count: 1 },
    }));
    ocrCandidatesMock.mockResolvedValue({ candidates: [] });
    updateActionMock.mockResolvedValue({});
    splitActionMock.mockResolvedValue({ ...actions[1], id: 4 });
    bulkMock.mockResolvedValue({ affected: 2, action: 'complete' });
    feedbackMock.mockResolvedValue({});
    paymentCheckMock.mockResolvedValue({
      payment_check: {
        state: 'unchecked',
        checked_at: null,
        stale_after: null,
        source: 'tyrion_bill_match',
        revision: 'v1:2:1',
        message: 'Payment has not been checked.',
        candidates: [],
      },
    });
    checkPaymentMock.mockResolvedValue({
      payment: {
        state: 'posted_match',
        checked_at: '2026-10-18T12:01:00Z',
        stale_after: '2026-10-25T12:00:00Z',
        source: 'tyrion_bill_match',
        revision: 'v1:2:3',
        message: 'A posted payment candidate requires confirmation.',
        candidates: [{
          id: 'evidence-1',
          payee: 'City Utilities',
          amount: 82.45,
          currency: 'USD',
          transaction_date: '2026-10-18',
          status: 'posted',
          account_display_name: 'Household checking',
          account_last_four: '1234',
          confidence: 'high',
          score: 96,
          match_reasons: ['amount_exact', 'date_strong'],
        }],
      },
    });
    confirmPaymentAndCompleteMock.mockResolvedValue({});
  });

  it('shows each document once while exposing every action on the selected document', async () => {
    renderWorkspace();

    const utilityRows = await screen.findAllByRole('button', { name: /Utility bill/ });
    expect(utilityRows).toHaveLength(1);
    expect(screen.getByRole('tab', { name: 'Actions 2' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: /PAY · Pay electric bill/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SIGN · Sign autopay authorization/ })).toBeInTheDocument();
    expect(screen.getByText('2 actions · PAY + SIGN')).toBeInTheDocument();
  });

  it('requires an explicit save for action edits', async () => {
    renderWorkspace();

    const title = await screen.findByDisplayValue('Pay electric bill');
    fireEvent.change(title, { target: { value: 'Pay utility balance' } });

    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save action' }));

    await waitFor(() => expect(updateActionMock).toHaveBeenCalledWith('1', expect.objectContaining({
      version: 2,
      action_type: 'PAY',
      title: 'Pay utility balance',
      payee_or_owner: 'City Utilities',
      amount: 82.45,
      reference: 'INV-101',
    })));
  });

  it('moves to the next document without changing action lifecycle', async () => {
    renderWorkspace();

    expect(await screen.findByRole('heading', { name: 'Utility bill' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next doc' }));

    expect(await screen.findByRole('heading', { name: 'Insurance renewal' })).toBeInTheDocument();
    expect(updateActionMock).not.toHaveBeenCalled();
    expect(feedbackMock).not.toHaveBeenCalled();
  });

  it('uses the shared authoritative OCR summary without changing action lifecycle', async () => {
    renderWorkspace();

    await screen.findByRole('heading', { name: 'Utility bill' });
    fireEvent.click(screen.getByRole('tab', { name: 'OCR' }));

    expect(await screen.findByRole('heading', { name: 'OCR assessment' })).toBeInTheDocument();
    expect(screen.getByText('Good')).toBeInTheDocument();
    expect(screen.getByText('Overlay / readability')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Extracted document text')).toBeInTheDocument();
    expect(updateActionMock).not.toHaveBeenCalled();
    expect(feedbackMock).not.toHaveBeenCalled();
  });

  it('confirms classifier feedback before marking an item Not an action', async () => {
    renderWorkspace();

    await screen.findByRole('heading', { name: 'Utility bill' });
    fireEvent.click(screen.getByRole('button', { name: 'Not an action' }));

    const dialog = screen.getByRole('dialog', { name: 'Mark as Not an action?' });
    expect(within(dialog).getByText(/incorrectly identified work/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save feedback' }));

    await waitFor(() => expect(feedbackMock).toHaveBeenCalledWith('1', {
      feedback_type: 'not_an_action',
    }));
  });

  it('offers a confirmed bulk completion path for multi-action documents', async () => {
    renderWorkspace();

    await screen.findByRole('heading', { name: 'Utility bill' });
    fireEvent.click(screen.getByRole('button', { name: 'More completion options' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Mark all pending actions done…' }));

    const dialog = screen.getByRole('dialog', { name: 'Mark every pending action done?' });
    expect(within(dialog).getByText('Pay electric bill')).toBeInTheDocument();
    expect(within(dialog).getByText('Sign autopay authorization')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark all done' }));

    await waitFor(() => expect(bulkMock).toHaveBeenCalledWith({
      action: 'complete',
      action_ids: [1, 2],
    }));
  });

  it('requires explicit confirmation before completing a posted payment match', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    renderWorkspace();

    expect(await screen.findByText('Ready to check')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Check for payment' }));

    expect(await screen.findByText('Posted payment found')).toBeInTheDocument();
    expect(screen.getByText('Household checking •••• 1234')).toBeInTheDocument();
    expect(updateActionMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm payment and complete' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm this posted payment?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm payment and complete' }));

    await waitFor(() => expect(confirmPaymentAndCompleteMock).toHaveBeenCalledWith('1', {
      revision: 'v1:2:3',
      idempotency_key: 'owl-confirm-payment:1:v1:2:3',
    }));
  });

  it('animates a restored in-progress payment check', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    paymentCheckMock.mockResolvedValue({
      payment_check: {
        state: 'checking',
        checked_at: '2026-10-18T16:14:08.398188',
        stale_after: null,
        source: 'tyrion_bill_match',
        revision: 'v1:2:2',
        message: 'Checking all financial accounts except any configured exclusions.',
        candidates: [],
      },
    });
    renderWorkspace();

    const button = await screen.findByRole('button', { name: 'Checking…' });
    expect(button).toBeDisabled();
    expect(button.querySelector('svg')).toHaveClass('spin');
    expect(screen.getByText('Checking all financial accounts except any configured exclusions.')).toBeInTheDocument();
  });

  it('allows another check after stale recovery', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    paymentCheckMock.mockResolvedValue({
      payment_check: {
        state: 'source_unavailable',
        checked_at: '2026-10-18T16:14:53.398188',
        stale_after: null,
        source: 'tyrion_bill_match',
        revision: 'v1:2:2',
        message: 'The payment source could not complete the check.',
        candidates: [],
      },
    });
    renderWorkspace();

    expect(await screen.findByText('Payment source unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check again' })).toBeEnabled();
  });
});
