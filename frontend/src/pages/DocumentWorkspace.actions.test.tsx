import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DocumentWorkspace from './DocumentWorkspace';

vi.mock('../components/OcrFocusInspector', () => ({
  default: ({ documentId, onReturn }: { documentId: number; onReturn?: () => void }) => (
    <section aria-label="OCR focus inspector">
      Focus document {documentId}
      <button type="button" onClick={onReturn}>Return to review</button>
    </section>
  ),
}));

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
  reviewPaymentCandidateMock,
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
  reviewPaymentCandidateMock: vi.fn(),
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
      reviewPaymentCandidate: reviewPaymentCandidateMock,
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
    <MemoryRouter initialEntries={['/actions']}>
      <DocumentWorkspace mode="actions" />
    </MemoryRouter>,
  );
}

describe('DocumentWorkspace actions mode', () => {
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
        search_context: {
          window_start: '2026-10-11',
          window_end: '2026-10-18',
          amount_tolerance: 1.65,
          date_tolerance_days: 7,
          eligible_account_count: 3,
          candidate_pool_size: 18,
        },
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
    reviewPaymentCandidateMock.mockResolvedValue({
      payment_check: {
        state: 'no_match',
        revision: 'v1:2:3',
        message: 'You rejected all suggested transactions.',
        candidates: [],
      },
    });
  });

  it('shows each document once while exposing every action on the selected document', async () => {
    const { container } = renderWorkspace();

    const utilityRows = await screen.findAllByRole('button', { name: /Utility bill/ });
    expect(container.querySelector('.document-workspace-frame.action-document-workspace')).toBeInTheDocument();
    expect(container.querySelector('.document-workspace-document')).toBeInTheDocument();
    expect(utilityRows).toHaveLength(1);
    expect(screen.getByRole('tab', { name: 'Actions 2' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: /PAY · Pay electric bill/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SIGN · Sign autopay authorization/ })).toBeInTheDocument();
    expect(screen.getByText('2 actions · PAY + SIGN')).toBeInTheDocument();
  });

  it('filters action types from the compact filter popover', async () => {
    renderWorkspace();

    await screen.findByRole('button', { name: /Utility bill/ });
    fireEvent.click(screen.getByRole('button', { name: 'Filter actions' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Pay 1' }));

    expect(screen.getByRole('button', { name: /Utility bill/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Insurance renewal/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Pay filter' })).toBeInTheDocument();
  });

  it('requires an explicit save for action edits', async () => {
    renderWorkspace();

    const title = await screen.findByDisplayValue('Pay electric bill');
    expect(screen.getByLabelText('Amount')).toHaveValue('$82.45');
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

  it('opens OCR focus mode in the selected action context and returns without lifecycle changes', async () => {
    renderWorkspace();

    await screen.findByRole('heading', { name: 'Utility bill' });
    fireEvent.click(screen.getByRole('tab', { name: 'OCR' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect OCR' }));

    expect(screen.getByLabelText('OCR focus inspector')).toHaveTextContent('Focus document 101');
    expect(screen.getByRole('button', { name: 'Expand action documents' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Return to review' }));

    expect(await screen.findByRole('heading', { name: 'OCR assessment' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'OCR' })).toHaveAttribute('aria-selected', 'true');
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

  it('shows consistent review actions for a single posted payment match', async () => {
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
    expect(screen.getByRole('button', { name: 'Not the match' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'This is the match' })).toBeInTheDocument();
    expect(updateActionMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'This is the match' }));
    const dialog = screen.getByRole('dialog', { name: 'Confirm this transaction?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm and complete' }));

    await waitFor(() => expect(reviewPaymentCandidateMock).toHaveBeenCalledWith('1', {
      action: 'confirm',
      revision: 'v1:2:3',
      idempotency_key: 'owl-review-payment:confirm:1:v1:2:3:evidence-1',
      evidence_id: 'evidence-1',
    }));
  });

  it('lets the user reject a single posted payment match', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    renderWorkspace();

    fireEvent.click(await screen.findByRole('button', { name: 'Check for payment' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Not the match' }));

    const dialog = screen.getByRole('dialog', { name: 'Mark this as not the match?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Not the match' }));

    await waitFor(() => expect(reviewPaymentCandidateMock).toHaveBeenCalledWith('1', {
      action: 'reject',
      revision: 'v1:2:3',
      idempotency_key: 'owl-review-payment:reject:1:v1:2:3:evidence-1',
      evidence_id: 'evidence-1',
    }));
  });

  it('offers a confirmation action for each ranked posted transaction', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    checkPaymentMock.mockResolvedValue({
      payment: {
        state: 'posted_match',
        revision: 'v1:2:3',
        message: 'A posted payment candidate requires confirmation.',
        candidates: [
          {
            id: 'evidence-deposit',
            rank: 1,
            payee: 'Project Client',
            amount: 5000,
            currency: 'USD',
            transaction_date: '2026-09-04',
            status: 'posted',
            account_display_name: 'Business checking',
            account_last_four: '2468',
            confidence: 'high',
            score: 94,
            match_reasons: ['amount_exact', 'date_strong'],
          },
          {
            id: 'evidence-final',
            rank: 2,
            payee: 'Project Client',
            amount: 5000,
            currency: 'USD',
            transaction_date: '2026-08-03',
            status: 'posted',
            account_display_name: 'Business checking',
            account_last_four: '2468',
            confidence: 'low',
            score: 60,
            match_reasons: ['amount_exact', 'date_partial'],
          },
        ],
      },
    });
    renderWorkspace();

    fireEvent.click(await screen.findByRole('button', { name: 'Check for payment' }));

    expect(await screen.findByText('2 ranked results')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'This is the match' })).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Confirm payment and complete' })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'This is the match' })[1]);
    const dialog = screen.getByRole('dialog', { name: 'Confirm this transaction?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm and complete' }));

    await waitFor(() => expect(reviewPaymentCandidateMock).toHaveBeenCalledWith('1', {
      action: 'confirm',
      revision: 'v1:2:3',
      idempotency_key: 'owl-review-payment:confirm:1:v1:2:3:evidence-final',
      evidence_id: 'evidence-final',
    }));
  });

  it('shows candidate review failures inside the confirmation dialog', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    paymentCheckMock.mockResolvedValue({
      payment_check: {
        state: 'posted_match',
        revision: 'v1:2:3',
        message: 'A posted payment candidate requires confirmation.',
        candidates: [
          {
            id: 'evidence-1',
            rank: 1,
            payee: 'Project Client',
            amount: 5000,
            currency: 'USD',
            transaction_date: '2026-09-04',
            status: 'posted',
            confidence: 'high',
            score: 94,
          },
          {
            id: 'evidence-2',
            rank: 2,
            payee: 'Project Client',
            amount: 5000,
            currency: 'USD',
            transaction_date: '2026-08-03',
            status: 'posted',
            confidence: 'medium',
            score: 82,
          },
        ],
      },
    });
    reviewPaymentCandidateMock.mockRejectedValueOnce(
      new Error('Payment reconciliation request was rejected.'),
    );
    renderWorkspace();

    fireEvent.click((await screen.findAllByRole('button', { name: 'This is the match' }))[0]);
    const dialog = screen.getByRole('dialog', { name: 'Confirm this transaction?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm and complete' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Payment reconciliation request was rejected.',
    );
    expect(within(dialog).getByRole('button', { name: 'Confirm and complete' })).toBeEnabled();
  });

  it('explains the full ranked result set when no candidate meets the threshold', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    paymentCheckMock.mockResolvedValue({
      payment_check: {
        state: 'no_match',
        checked_at: '2026-10-18T12:01:00Z',
        stale_after: '2026-10-25T12:00:00Z',
        source: 'tyrion_bill_match',
        revision: 'v1:2:3',
        message: 'No matching transaction was found.',
        search_context: {
          window_start: '2026-10-11',
          window_end: '2026-10-18',
          amount_tolerance: 1.65,
          date_tolerance_days: 7,
          eligible_account_count: 3,
          candidate_pool_size: 18,
        },
        candidates: [
          {
            id: 'evidence-near-1',
            rank: 1,
            payee: 'Utility Payment Processor',
            amount: 82.45,
            currency: 'USD',
            transaction_date: '2026-10-18',
            status: 'posted',
            account_display_name: 'Household checking',
            confidence: 'low',
            score: 60,
            match_reasons: ['amount_exact', 'date_none', 'payee_none', 'account_notprovided'],
          },
          {
            id: 'evidence-near-2',
            rank: 2,
            payee: 'City Services',
            amount: 80.95,
            currency: 'USD',
            transaction_date: '2026-10-16',
            status: 'posted',
            account_display_name: 'Rewards card',
            account_last_four: '9876',
            confidence: 'low',
            score: 54,
            match_reasons: ['amount_partial', 'date_strong', 'payee_partial', 'account_notprovided'],
          },
        ],
      },
    });

    renderWorkspace();

    expect(await screen.findByText('No payment found')).toBeInTheDocument();
    expect(screen.getByText('3', { selector: '.payment-search-context dd' })).toBeInTheDocument();
    expect(screen.getByText('18', { selector: '.payment-search-context dd' })).toBeInTheDocument();
    expect(screen.getByText('Closest transactions')).toBeInTheDocument();
    expect(screen.getByText('2 ranked results')).toBeInTheDocument();
    expect(screen.getByText('Utility Payment Processor')).toBeInTheDocument();
    expect(screen.getByText('City Services')).toBeInTheDocument();
    expect(screen.getByText('Amount: exact')).toBeInTheDocument();
    expect(screen.getByText('Vendor: no match')).toBeInTheDocument();
    expect(screen.getByText('Date: strong')).toBeInTheDocument();
    expect(screen.getAllByText('Account: not used')).toHaveLength(2);
    expect(screen.getByText("These are the closest results, but none met Tyrion's match threshold.")).toBeInTheDocument();
    expect(screen.getByText('Search details').closest('details')).not.toHaveAttribute('open');
    expect(screen.getAllByText('Why this result?')[0].closest('details')).not.toHaveAttribute('open');
    expect(screen.getAllByRole('button', { name: 'Not the match' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'This is the match' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'None of these' })).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Not the match' })[0]);
    const rejectDialog = screen.getByRole('dialog', { name: 'Mark this as not the match?' });
    fireEvent.click(within(rejectDialog).getByRole('button', { name: 'Not the match' }));

    await waitFor(() => expect(reviewPaymentCandidateMock).toHaveBeenCalledWith('1', {
      action: 'reject',
      revision: 'v1:2:3',
      idempotency_key: 'owl-review-payment:reject:1:v1:2:3:evidence-near-1',
      evidence_id: 'evidence-near-1',
    }));
  });

  it('lets the user select, discard, or reject all ambiguous candidates', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    paymentCheckMock.mockResolvedValue({
      payment_check: {
        state: 'multiple_matches',
        revision: 'v1:2:3',
        message: 'Multiple plausible payment candidates require review.',
        candidates: [
          {
            id: 'evidence-1',
            rank: 1,
            payee: 'City Utilities',
            amount: 82.45,
            currency: 'USD',
            transaction_date: '2026-10-18',
            status: 'posted',
            account_display_name: 'Household checking',
            confidence: 'high',
            score: 92,
            match_reasons: ['amount_exact', 'date_strong'],
          },
          {
            id: 'evidence-2',
            rank: 2,
            payee: 'City Services',
            amount: 82.45,
            currency: 'USD',
            transaction_date: '2026-10-17',
            status: 'pending',
            account_display_name: 'Rewards card',
            confidence: 'medium',
            score: 87,
            match_reasons: ['amount_exact', 'date_partial'],
          },
        ],
      },
    });
    reviewPaymentCandidateMock.mockResolvedValueOnce({
      payment_check: {
        state: 'confirmed',
        revision: 'v1:3:4',
        message: 'Payment was confirmed and the obligation was completed.',
        candidates: [],
      },
    });

    renderWorkspace();

    fireEvent.click(await screen.findByRole('radio', { name: 'Select City Utilities' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm selected' }));
    const confirmDialog = screen.getByRole('dialog', { name: 'Confirm this transaction?' });
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Confirm and complete' }));

    await waitFor(() => expect(reviewPaymentCandidateMock).toHaveBeenCalledWith('1', {
      action: 'confirm',
      revision: 'v1:2:3',
      idempotency_key: 'owl-review-payment:confirm:1:v1:2:3:evidence-1',
      evidence_id: 'evidence-1',
    }));
  });

  it('confirms rejecting every ambiguous candidate', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    paymentCheckMock.mockResolvedValue({
      payment_check: {
        state: 'multiple_matches',
        revision: 'v1:2:3',
        message: 'Multiple plausible payment candidates require review.',
        candidates: [{
          id: 'evidence-1',
          rank: 1,
          payee: 'City Utilities',
          amount: 82.45,
          currency: 'USD',
          transaction_date: '2026-10-18',
          status: 'posted',
          confidence: 'high',
          score: 92,
          match_reasons: ['amount_exact'],
        }],
      },
    });

    renderWorkspace();

    fireEvent.click((await screen.findAllByRole('button', { name: 'Discard' }))[0]);
    const discardDialog = screen.getByRole('dialog', { name: 'Discard this candidate?' });
    fireEvent.click(within(discardDialog).getByRole('button', { name: 'Cancel' }));

    fireEvent.click(await screen.findByRole('button', { name: 'None of these' }));
    const rejectDialog = screen.getByRole('dialog', { name: 'None of these transactions match?' });
    fireEvent.click(within(rejectDialog).getByRole('button', { name: 'Reject all candidates' }));

    await waitFor(() => expect(reviewPaymentCandidateMock).toHaveBeenCalledWith('1', {
      action: 'reject_all',
      revision: 'v1:2:3',
      idempotency_key: 'owl-review-payment:reject_all:1:v1:2:3:all',
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
        candidates: [{
          id: 'failure-audit-evidence',
          payee: 'Brochu Bros',
          amount: 2598.22,
          currency: 'USD',
          transaction_date: null,
          status: null,
          account_display_name: null,
          account_last_four: null,
          confidence: null,
          score: null,
          rank: 1,
          score_gap_basis_points: null,
          match_reasons: ['tyrion_transport_unavailable'],
          receipt_association: null,
        }],
      },
    });
    renderWorkspace();

    expect(await screen.findByText('Payment source unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check again' })).toBeEnabled();
    expect(screen.queryByText('Matched transaction')).not.toBeInTheDocument();
    expect(screen.queryByText('Brochu Bros')).not.toBeInTheDocument();
  });

  it('reloads missing payment status instead of rendering a dead check button', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    let resolveReload: ((value: unknown) => void) | undefined;
    paymentCheckMock
      .mockRejectedValueOnce(new Error('Payment status could not be loaded.'))
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveReload = resolve;
      }));
    renderWorkspace();

    expect(await screen.findByText('Payment status unavailable')).toBeInTheDocument();
    expect(screen.getByText('Payment status could not be loaded.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Retry check' }));

    const checkingButton = await screen.findByRole('button', { name: 'Checking…' });
    expect(checkingButton).toBeDisabled();
    expect(checkingButton.querySelector('svg')).toHaveClass('spin');

    await act(async () => {
      resolveReload?.({
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
    });

    await waitFor(() => expect(checkPaymentMock).toHaveBeenCalledWith('1', {
      revision: 'v1:2:2',
      idempotency_key: 'owl-check-payment:1:v1:2:2',
    }));
    expect(await screen.findByText('Posted payment found')).toBeInTheDocument();
  });

  it('ignores a late payment status response after switching documents', async () => {
    actionsMock.mockResolvedValue({
      actions: actions.map((action) => (
        action.id === 1 ? { ...action, obligation_id: 'obligation-1' } : { ...action }
      )),
      total: actions.length,
    });
    let resolvePaymentStatus: ((value: unknown) => void) | undefined;
    paymentCheckMock.mockImplementationOnce(() => new Promise((resolve) => {
      resolvePaymentStatus = resolve;
    }));
    renderWorkspace();

    await screen.findByRole('heading', { name: 'Utility bill' });
    fireEvent.click(screen.getByRole('button', { name: 'Next doc' }));
    expect(await screen.findByRole('heading', { name: 'Insurance renewal' })).toBeInTheDocument();

    await act(async () => {
      resolvePaymentStatus?.({
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
    });

    expect(screen.queryByRole('region', { name: 'Payment check' })).not.toBeInTheDocument();
  });
});
