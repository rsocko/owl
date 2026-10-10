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
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    triage: { queue: mocks.triageQueue },
    actionQueue: { actions: mocks.actions, updateAction: vi.fn() },
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
    mocks.preview.mockResolvedValue({
      documents_examined: 12,
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
    expect(await screen.findByText('12')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start relationship scan' }));

    await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Scan completed.')).toBeInTheDocument();
    expect(screen.getByText('Review items created')).toBeInTheDocument();
    expect(screen.getByText('Conflicts')).toBeInTheDocument();
    expect(screen.getByText('Failures')).toBeInTheDocument();
  });
});
