import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ActionQueueOperations from './ActionQueueOperations';

const { runMock } = vi.hoisted(() => ({
  runMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    actionQueue: {
      run: runMock,
      check: vi.fn(),
      checkCustomFields: vi.fn(),
      backfill: vi.fn(),
      refreshMetadata: vi.fn(),
    },
  },
}));

describe('ActionQueueOperations', () => {
  beforeEach(() => {
    runMock.mockReset();
    runMock.mockResolvedValue({});
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
});
