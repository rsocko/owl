import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OrphansDupes from './OrphansDupes';

const mocks = vi.hoisted(() => ({
  triageQueue: vi.fn(),
  actions: vi.fn(),
  alerts: vi.fn(),
  settings: vi.fn(),
  preview: vi.fn(),
  start: vi.fn(),
  status: vi.fn(),
  metadataTags: vi.fn(),
  metadataSavedViews: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    triage: { queue: mocks.triageQueue },
    actionQueue: {
      actions: mocks.actions,
      updateAction: vi.fn(),
      metadataTags: mocks.metadataTags,
      metadataSavedViews: mocks.metadataSavedViews,
    },
    alerts: { list: mocks.alerts },
    duplicates: {
      settings: mocks.settings,
      updateSettings: vi.fn(),
      previewRelationshipScan: mocks.preview,
      startRelationshipScan: mocks.start,
      relationshipScanStatus: mocks.status,
    },
  },
}));

describe('OrphansDupes relationship maintenance', () => {
  beforeEach(() => {
    mocks.triageQueue.mockResolvedValue({ items: [], count: 0, offset: 0, limit: 200 });
    mocks.actions.mockResolvedValue({ actions: [] });
    mocks.alerts.mockResolvedValue({ alerts: [] });
    mocks.settings.mockResolvedValue({ auto_detect_enabled: false });
    mocks.metadataTags.mockResolvedValue({ tags: [{ id: 1, name: 'Bills' }] });
    mocks.metadataSavedViews.mockResolvedValue({
      saved_views: [{ id: 7, name: 'Relationship candidates' }],
    });
    mocks.preview.mockResolvedValue({
      scope: { kind: 'action_queue', description: 'Action Queue tags: Inbox' },
      documents_discovered: 12,
      metadata_eligible_documents: 10,
      documents_excluded: 2,
      exclusion_reasons: { missing_obligation_identity: 2 },
      candidate_groups: 2,
      candidate_pairs: 3,
      relationships_created: 2,
      relationships_reused: 1,
      review_candidates: 3,
    });
    mocks.start.mockResolvedValue({
      run_id: 'scan-1',
      status: 'completed',
      counts: {
        documents_examined: 12,
        relationships_created: 2,
        relationships_reused: 1,
        triage_items_created: 3,
        conflicts: 1,
        failures: 0,
      },
      errors: [],
    });
  });

  it('reveals a non-mutating preview before starting the scan', async () => {
    render(<OrphansDupes />);

    const reveal = await screen.findByRole(
      'button',
      { name: 'Scan existing documents for relationships' },
    );
    fireEvent.click(reveal);

    expect(screen.getByText(/Documents are preserved/)).toBeInTheDocument();
    await waitFor(() => expect(mocks.preview).toHaveBeenCalledTimes(1));
    expect(mocks.start).not.toHaveBeenCalled();
    expect(await screen.findByText('Action Queue tags: Inbox')).toBeInTheDocument();
    expect(screen.getByText('Discovered documents')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start relationship scan' }));

    await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Scan completed.')).toBeInTheDocument();
    expect(screen.getByText('Review items created')).toBeInTheDocument();
    expect(screen.getByText('Conflicts')).toBeInTheDocument();
    expect(screen.getByText('Failures')).toBeInTheDocument();
  });

  it('refreshes preview and reveals only fields for the selected scope', async () => {
    render(<OrphansDupes />);
    fireEvent.click(await screen.findByRole(
      'button',
      { name: 'Scan existing documents for relationships' },
    ));
    await waitFor(() => expect(mocks.preview).toHaveBeenCalledWith({ kind: 'action_queue' }));

    fireEvent.change(screen.getByLabelText('Scan scope'), {
      target: { value: 'saved_view' },
    });
    expect(await screen.findByLabelText('Paperless saved view')).toBeInTheDocument();
    expect(screen.queryByLabelText('Paperless tag')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Paperless saved view'), {
      target: { value: '7' },
    });
    await waitFor(() => expect(mocks.preview).toHaveBeenLastCalledWith({
      kind: 'saved_view',
      saved_view_id: 7,
    }));

    fireEvent.change(screen.getByLabelText('Scan scope'), {
      target: { value: 'all' },
    });
    expect(screen.getByText(/may take a while for a large library/i)).toBeInTheDocument();
  });
});
