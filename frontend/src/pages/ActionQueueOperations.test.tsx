import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ActionQueueOperations from './ActionQueueOperations';

const {
  projectionHealthMock,
  reconcileProjectionsMock,
  metadataSchemaMock,
  setupMetadataSchemaMock,
  refreshMetadataMock,
  runMock,
} = vi.hoisted(() => ({
  projectionHealthMock: vi.fn(),
  reconcileProjectionsMock: vi.fn(),
  metadataSchemaMock: vi.fn(),
  setupMetadataSchemaMock: vi.fn(),
  refreshMetadataMock: vi.fn(),
  runMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    actionQueue: {
      run: runMock,
      check: vi.fn(),
      checkMetadataSchema: metadataSchemaMock,
      setupMetadataSchema: setupMetadataSchemaMock,
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
    metadataSchemaMock.mockReset();
    metadataSchemaMock.mockResolvedValue({
      status: 'ready',
      category: null,
      write_enabled: true,
      can_setup: false,
      ready_count: 16,
      total_count: 16,
      fields: [],
    });
    setupMetadataSchemaMock.mockReset();
    setupMetadataSchemaMock.mockResolvedValue({
      status: 'ready',
      category: null,
      write_enabled: true,
      can_setup: false,
      ready_count: 16,
      total_count: 16,
      fields: [],
    });
  });

  it('previews a forced single-document rescan', async () => {
    render(<ActionQueueOperations />);

    fireEvent.change(screen.getByLabelText('Scope'), { target: { value: 'document' } });
    fireEvent.change(screen.getByLabelText('Document ID'), { target: { value: '42' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview scope' }));

    await waitFor(() => {
      expect(runMock).toHaveBeenCalledWith({
        dry_run: true,
        force: true,
        document_id: 42,
      });
    });
  });

  it('forces a filtered all-document rescan after confirmation', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ActionQueueOperations />);

    fireEvent.change(screen.getByLabelText('Scope'), { target: { value: 'all' } });
    fireEvent.change(screen.getByLabelText('Correspondent'), { target: { value: 'Utility Co' } });
    fireEvent.change(screen.getByLabelText('Document type'), { target: { value: 'Statement' } });
    fireEvent.change(screen.getByLabelText('Added after'), { target: { value: '2026-08-01' } });
    fireEvent.click(screen.getByRole('button', { name: 'Force rescan' }));

    await waitFor(() => {
      expect(runMock).toHaveBeenCalledWith({
        dry_run: false,
        force: true,
        all_documents: true,
        correspondent: 'Utility Co',
        document_type: 'Statement',
        added_after: '2026-08-01',
      });
    });
    expect(confirm).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });

  it('requires the selected scope value before running', async () => {
    render(<ActionQueueOperations />);

    fireEvent.change(screen.getByLabelText('Scope'), { target: { value: 'saved_view' } });

    expect(screen.getByRole('button', { name: 'Preview scope' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Run new or stale' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Force rescan' })).toBeDisabled();
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
          code: 'paperless_not_configured',
          message: 'Paperless is not configured.',
          recorded_at: null,
        },
      })
      .mockResolvedValueOnce({
        pending: 0,
        failed: 1,
        not_intended: 2,
        last_error: {
          relationship_id: 'rel-2',
          code: 'paperless_not_configured',
          message: 'Paperless is not configured.',
          recorded_at: null,
        },
      });
    reconcileProjectionsMock.mockResolvedValue({
      attempted: 2,
      succeeded: 1,
      failed: 1,
      remaining: 1,
      errors: [{
        relationship_id: 'rel-2',
        code: 'paperless_not_configured',
        message: 'Paperless is not configured.',
      }],
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
    expect(screen.getAllByText(/Paperless is not configured/).length).toBeGreaterThan(0);
    expect(projectionHealthMock).toHaveBeenCalledTimes(2);
  });

  it('offers one complete setup when any active metadata field is missing', async () => {
    metadataSchemaMock.mockResolvedValue({
      status: 'missing',
      category: 'paperless_schema_setup_required',
      write_enabled: true,
      can_setup: true,
      ready_count: 15,
      total_count: 16,
      fields: [
        {
          key: 'patient_name',
          name: 'Patient Name',
          type: 'string',
          options: [],
          status: 'missing',
          diagnostics: ['missing_field'],
        },
      ],
    });
    render(<ActionQueueOperations />);

    const setup = await screen.findByRole(
      'button',
      { name: 'Set up complete metadata schema' },
    );
    fireEvent.click(setup);

    await waitFor(() => expect(setupMetadataSchemaMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(projectionHealthMock).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByText('Complete Paperless metadata schema is ready.'),
    ).toBeInTheDocument();
  });
});
