import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ActionQueueOperations from './ActionQueueOperations';

const {
  projectionHealthMock,
  reconcileProjectionsMock,
  refreshMetadataMock,
  runMock,
} = vi.hoisted(() => ({
  projectionHealthMock: vi.fn(),
  reconcileProjectionsMock: vi.fn(),
  refreshMetadataMock: vi.fn(),
  runMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    actionQueue: {
      run: runMock,
      check: vi.fn(),
      checkCustomFields: vi.fn(),
      backfill: vi.fn(),
      refreshMetadata: refreshMetadataMock,
    },
    relationships: {
      projectionHealth: projectionHealthMock,
      reconcileProjections: reconcileProjectionsMock,
    },
  },
}));

describe('ActionQueueOperations', () => {
  beforeEach(() => {
    runMock.mockReset();
    runMock.mockResolvedValue({});
    refreshMetadataMock.mockReset();
    refreshMetadataMock.mockResolvedValue({});
    projectionHealthMock.mockReset();
    projectionHealthMock.mockResolvedValue({
      pending: 0,
      failed: 0,
      not_intended: 0,
      last_error: null,
    });
    reconcileProjectionsMock.mockReset();
    reconcileProjectionsMock.mockResolvedValue({
      attempted: 0,
      succeeded: 0,
      failed: 0,
      remaining: 0,
      errors: [],
    });
  });

  it('owns custom and dry-run controls outside the daily queue', async () => {
    render(<ActionQueueOperations />);

    fireEvent.change(screen.getByLabelText('Document ID'), { target: { value: '42' } });
    fireEvent.change(screen.getByLabelText('Saved view ID'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('Correspondent'), { target: { value: 'Utility Co' } });
    fireEvent.change(screen.getByLabelText('Document type'), { target: { value: 'Statement' } });
    fireEvent.change(screen.getByLabelText('Added after'), { target: { value: '2026-08-01' } });
    fireEvent.click(screen.getByRole('button', { name: 'Dry run' }));

    await waitFor(() => {
      expect(runMock).toHaveBeenCalledWith(expect.objectContaining({
        dry_run: true,
        document_id: 42,
        saved_view_id: 7,
        correspondent: 'Utility Co',
        document_type: 'Statement',
        added_after: '2026-08-01',
      }));
    });
  });

  it('treats Paperless as the source of truth when refreshing metadata', async () => {
    render(<ActionQueueOperations />);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh all metadata' }));

    await waitFor(() => {
      expect(refreshMetadataMock).toHaveBeenCalledWith({ force: true });
    });
  });

  it('disables relationship repair when projection health is empty', async () => {
    render(<ActionQueueOperations />);

    const repair = await screen.findByRole(
      'button',
      { name: 'Retry failed Paperless relationship sync' },
    );

    expect(repair).toBeDisabled();
    expect(screen.getByText('No relationship projections need repair.')).toBeInTheDocument();
  });

  it('reports relationship projection repair results and errors', async () => {
    projectionHealthMock
      .mockResolvedValueOnce({
        pending: 1,
        failed: 1,
        not_intended: 2,
        last_error: {
          relationship_id: 'rel-2',
          message: 'Paperless unavailable',
          recorded_at: null,
        },
      })
      .mockResolvedValueOnce({
        pending: 0,
        failed: 1,
        not_intended: 2,
        last_error: {
          relationship_id: 'rel-2',
          message: 'Paperless unavailable',
          recorded_at: null,
        },
      });
    reconcileProjectionsMock.mockResolvedValue({
      attempted: 2,
      succeeded: 1,
      failed: 1,
      remaining: 1,
      errors: [{ relationship_id: 'rel-2', message: 'Paperless unavailable' }],
    });
    render(<ActionQueueOperations />);

    const repair = await screen.findByRole(
      'button',
      { name: 'Retry failed Paperless relationship sync' },
    );
    await waitFor(() => expect(repair).toBeEnabled());
    fireEvent.click(repair);

    await waitFor(() => expect(reconcileProjectionsMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Attempted')).toBeInTheDocument();
    expect(screen.getByText('Succeeded')).toBeInTheDocument();
    expect(screen.getAllByText('Paperless unavailable').length).toBeGreaterThan(0);
  });
});
