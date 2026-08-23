import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Settings from './Settings';

const { updateSettingsMock } = vi.hoisted(() => ({
  updateSettingsMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn().mockResolvedValue({}),
  },
  endpoints: {
    settings: {
      get: vi.fn().mockResolvedValue({ write_to_paperless: true }),
      update: vi.fn(),
    },
    admin: {
      schedules: {
        get: vi.fn().mockResolvedValue({}),
        update: vi.fn(),
      },
      retention: {
        get: vi.fn().mockResolvedValue({}),
        update: vi.fn(),
      },
      storage: vi.fn().mockResolvedValue({}),
      cleanup: vi.fn(),
      documentTypes: vi.fn().mockResolvedValue({ types: [], has_saved_mapping: false }),
      documentTypeMapping: { update: vi.fn() },
    },
    llm: {
      models: vi.fn().mockResolvedValue({ available_models: [] }),
      test: vi.fn(),
    },
    paperlessHealth: vi.fn().mockResolvedValue({ status: 'ok' }),
    actionQueue: {
      settings: vi.fn().mockResolvedValue({
        scan_mode: 'tags',
        monitor_tags: ['Inbox'],
        saved_view_id: null,
        confidence_threshold: 70,
        document_limit: null,
        rate_limit_delay: 0.25,
        remove_source_tag_on_resolve: true,
      }),
      updateSettings: updateSettingsMock,
      metadataTags: vi.fn().mockResolvedValue({
        tags: [
          { id: 1, name: 'Inbox' },
          { id: 2, name: 'Finance' },
        ],
      }),
      metadataSavedViews: vi.fn().mockResolvedValue({ saved_views: [] }),
    },
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  updateSettingsMock.mockResolvedValue({ status: 'ok' });
});

describe('Action Queue settings', () => {
  it('saves the source-tag removal preference', async () => {
    render(<Settings />);

    const checkbox = await screen.findByRole('checkbox', {
      name: /remove intake tags when resolved/i,
    });
    expect((checkbox as HTMLInputElement).checked).toBe(true);

    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: /save source settings/i }));

    await waitFor(() => {
      expect(updateSettingsMock).toHaveBeenCalledWith(
        expect.objectContaining({
          monitor_tags: ['Inbox'],
          remove_source_tag_on_resolve: false,
        }),
      );
    });
  });

  it('searches, adds, and removes source tags by name', async () => {
    render(<Settings />);

    const typeahead = await screen.findByRole('combobox', { name: 'Monitor tags' });
    fireEvent.change(typeahead, { target: { value: 'fin' } });
    fireEvent.click(screen.getByRole('option', { name: 'Finance' }));
    expect(screen.getByText('Finance')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Remove Finance' }));
    expect(screen.queryByText('Finance')).not.toBeInTheDocument();
  });
});
