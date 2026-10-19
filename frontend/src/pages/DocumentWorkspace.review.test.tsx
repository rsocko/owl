import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import DocumentWorkspace from './DocumentWorkspace';

const { queueMock, statsMock, resolveMock, undoMock } = vi.hoisted(() => ({
  queueMock: vi.fn(),
  statsMock: vi.fn(),
  resolveMock: vi.fn(),
  undoMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    triage: {
      queue: queueMock,
      stats: statsMock,
      resolve: resolveMock,
      defer: vi.fn(),
      dismiss: vi.fn(),
      undo: undoMock,
      bulk: vi.fn(),
      bulkConfirmThreshold: vi.fn(),
      populate: vi.fn(),
    },
    documents: {
      metadata: vi.fn().mockResolvedValue({ title: 'Uncertain bill' }),
      thumbnailUrl: vi.fn().mockReturnValue('/thumbnail/777'),
      previewUrl: vi.fn().mockReturnValue('/preview/777'),
      downloadUrl: vi.fn().mockReturnValue('/download/777'),
    },
  },
}));

vi.mock('../components/OcrFocusInspector', () => ({
  default: ({ documentId }: { documentId: number }) => (
    <div aria-label="OCR focus inspector">OCR inspector for {documentId}</div>
  ),
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
  undoMock.mockReset();
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
    by_type_status: { other: { pending: 1 } },
    pending: 1,
  });
  resolveMock.mockResolvedValue({ action_ready: true, review_state: 'ready' });
  undoMock.mockResolvedValue({ status: 'pending' });
});

describe('Needs Review actions', () => {
  it('uses decision language and keeps Dismiss under More', async () => {
    const { container } = render(
      <MemoryRouter>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    const [item] = await screen.findAllByText('OTHER: target-1');
    expect(container.querySelector('.document-workspace-frame.document-review-workspace')).toBeInTheDocument();
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
      <MemoryRouter initialEntries={['/needs-review?type=eob_match_review']}>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(queueMock).toHaveBeenCalledWith('type=eob_match_review&status=pending&limit=200');
    });
  });

  it('ignores unknown type query parameters', async () => {
    render(
      <MemoryRouter initialEntries={['/needs-review?type=not-a-real-queue']}>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(queueMock).toHaveBeenCalledWith('status=pending&limit=200');
    });
  });

  it('loads OCR reviews with assessment filters and opens the shared focus inspector', async () => {
    queueMock.mockResolvedValue({
      items: [{
        id: 'ocr-review-1',
        item_type: 'ocr_quality_review',
        priority: 90,
        status: 'pending',
        source: 'ocr_quality_assessment',
        target_type: 'document',
        target_id: '777',
        reason: 'Overlay alignment is unreliable.',
        metadata: {
          document_id: 777,
          review_status: 'FAILED',
          weakest_dimension: 'overlay',
          weakest_score: 12,
          candidate_state: 'ready',
        },
        deferred_until: null,
        resolved_at: null,
        resolved_action: null,
        created_at: '2026-08-10T12:00:00Z',
      }],
      count: 1,
      total: 1,
      offset: 0,
      limit: 200,
    });
    statsMock.mockResolvedValue({
      total: 1,
      by_type: { ocr_quality_review: 1 },
      by_status: { pending: 1 },
      by_type_status: { ocr_quality_review: { pending: 1 } },
      pending: 1,
    });

    render(
      <MemoryRouter initialEntries={['/needs-review?type=ocr_quality_review&ocr_review_status=FAILED&item=ocr-review-1']}>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    await waitFor(() => expect(queueMock).toHaveBeenCalledWith(
      'type=ocr_quality_review&status=pending&ocr_review_status=FAILED&limit=200',
    ));
    expect(await screen.findByLabelText('OCR focus inspector')).toHaveTextContent('777');
    expect(screen.queryByRole('checkbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Mark OCR review complete' }));
    await waitFor(() => expect(resolveMock).toHaveBeenCalledWith('ocr-review-1', {
      action: 'mark_reviewed',
    }));
  });

  it('sends search terms to the privacy-safe backend query', async () => {
    render(
      <MemoryRouter initialEntries={['/needs-review?type=ocr_quality_review&q=alignment']}>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    await waitFor(() => expect(queueMock).toHaveBeenCalledWith(
      'type=ocr_quality_review&status=pending&search=alignment&limit=200',
    ));
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

    const { container } = render(
      <MemoryRouter initialEntries={['/needs-review?type=action_classification&item=action-review-1']}>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    const type = await screen.findByLabelText('Corrected action type');
    expect(container.querySelector('.document-workspace-detail-host .document-workspace-document')).toBeInTheDocument();
    expect(container.querySelector('.document-workspace-frame-preview > iframe')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Actions' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Metadata' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'OCR' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).toBeNull();
    fireEvent.change(type, { target: { value: 'TASK' } });
    fireEvent.change(screen.getByLabelText('Corrected task name'), {
      target: { value: 'Send annual statement to accountant' },
    });
    fireEvent.change(screen.getByLabelText('Corrected amount'), { target: { value: '' } });
    expect(screen.getByRole('button', { name: 'Save corrections' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reset to suggested values' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save corrections' }));

    await waitFor(() => {
      expect(resolveMock).toHaveBeenCalledWith('action-review-1', {
        action: 'correct',
        payload: {
          action_type: 'TASK',
          title: 'Send annual statement to accountant',
          summary: 'Original guess',
          due_date: null,
          amount: null,
        },
      });
    });
  });

  it('switches between accepting detected values and saving corrections', async () => {
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
          title: 'Pay utility bill',
          summary: 'Detected summary',
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

    render(
      <MemoryRouter>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    const accept = await screen.findByRole('button', { name: 'Accept suggestion' });
    expect(accept).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Save corrections' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset to suggested values' })).toBeNull();

    fireEvent.change(screen.getByLabelText('Corrected amount'), { target: { value: '30' } });
    expect(screen.getByRole('button', { name: 'Save corrections' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Reset to suggested values' }));

    expect(screen.getByRole('button', { name: 'Accept suggestion' })).toBeEnabled();
    expect(screen.getByLabelText('Corrected amount')).toHaveValue('$25.00');
  });

  it('accepts multiple proposed actions as one document review', async () => {
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
          title: 'Pay utility bill',
          summary: 'Detected summary',
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

    render(
      <MemoryRouter>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Add action' }));
    fireEvent.change(screen.getByLabelText('Corrected action type for action 2'), {
      target: { value: 'SIGN' },
    });
    fireEvent.change(screen.getByLabelText('Corrected task name for action 2'), {
      target: { value: 'Sign autopay authorization' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Accept 2 actions' }));

    await waitFor(() => expect(resolveMock).toHaveBeenCalledWith('action-review-1', {
      action: 'correct',
      payload: {
        actions: [
          {
            action_type: 'PAY',
            title: 'Pay utility bill',
            summary: 'Detected summary',
            due_date: null,
            amount: 25,
          },
          {
            action_type: 'SIGN',
            title: 'Sign autopay authorization',
            summary: '',
            due_date: null,
            amount: null,
          },
        ],
      },
    }));
  });

  it('requires an amount for payment actions but not a due date', async () => {
    queueMock.mockResolvedValue({
      items: [{
        id: 'action-review-1',
        item_type: 'action_classification',
        priority: 80,
        status: 'pending',
        source: 'action_queue',
        target_type: 'action',
        target_id: '42',
        reason: 'PAY is missing critical action details',
        metadata: {
          action_type: 'PAY',
          title: 'Pay utility bill',
          summary: 'Detected summary',
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

    render(
      <MemoryRouter>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Enter an amount to continue. A due date is optional.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Accept suggestion' })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Corrected amount'), { target: { value: '395' } });
    expect(screen.getByRole('button', { name: 'Save corrections' })).toBeEnabled();
  });

  it('shows persisted values and an Action Queue link after the item is resolved', async () => {
      queueMock.mockResolvedValue({
        items: [{
          id: 'action-review-resolved',
          item_type: 'action_classification',
          priority: 80,
          status: 'resolved',
          source: 'action_queue',
          target_type: 'action',
          target_id: '42',
          reason: 'Correction saved',
          metadata: {
            action_type: 'PAY',
            title: 'Pay utility bill',
            summary: 'Pay the corrected invoice.',
            amount: 395,
            document_id: 777,
          },
          deferred_until: null,
          resolved_at: '2026-08-11T12:00:00Z',
          resolved_action: 'correct',
          created_at: '2026-08-10T12:00:00Z',
        }],
        count: 1,
        offset: 0,
        limit: 200,
      });

    render(
      <MemoryRouter>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Corrections saved')).toBeTruthy();
    expect(screen.getByText('$395.00')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'View action' })).toHaveAttribute(
      'href',
      '#/actions/42',
    );
    expect(screen.queryByLabelText('Corrected amount')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save corrections' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Not an action' })).toBeNull();
  });

  it('warns before reopening a resolved action review', async () => {
    queueMock.mockResolvedValue({
      items: [{
        id: 'action-review-1',
        item_type: 'action_classification',
        priority: 80,
        status: 'resolved',
        source: 'action_queue',
        target_type: 'action',
        target_id: '42',
        reason: 'Low-confidence action classification',
        metadata: {
          action_type: 'PAY',
          title: 'Pay utility bill',
          amount: 25,
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
    statsMock.mockResolvedValue({
      total: 1,
      by_type: { action_classification: 1 },
      by_status: { resolved: 1 },
      pending: 0,
    });

    render(
      <MemoryRouter>
        <DocumentWorkspace mode="review" />
      </MemoryRouter>,
    );

    await screen.findAllByText('Pay utility bill');
    fireEvent.click(await screen.findByRole('button', { name: 'Review status' }));
    fireEvent.click(await screen.findByRole('radio', { name: 'Resolved 1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Revise decision' }));
    expect(screen.getByText(/completion history is preserved/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reopen review' }));

    await waitFor(() => expect(undoMock).toHaveBeenCalledWith('action-review-1'));
  });
});
