import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endpoints } from '../lib/api';
import Settings from './Settings';

const { updateSettingsMock, updateTyrionMock } = vi.hoisted(() => ({
  updateSettingsMock: vi.fn(),
  updateTyrionMock: vi.fn(),
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
    statements: {
      externalCandidateConnection: vi.fn().mockResolvedValue({
        configured: false,
        token_configured: false,
        verify_ssl: true,
        timeout_seconds: 30,
      }),
      updateExternalCandidateConnection: updateTyrionMock,
      deleteExternalCandidateConnection: vi.fn(),
    },
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
  updateTyrionMock.mockResolvedValue({
    configured: true,
    base_url: 'https://tyrion.test',
    connector_ref: 'owl',
    token_configured: true,
    verify_ssl: true,
    timeout_seconds: 30,
  });
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

  describe('Tyrion settings', () => {
    it('saves a user-managed Tyrion connection', async () => {
      render(<Settings />);

      fireEvent.change(await screen.findByLabelText('Tyrion base URL'), {
        target: { value: 'https://tyrion.test' },
      });
      fireEvent.change(screen.getByLabelText('Tyrion connector reference'), {
        target: { value: 'owl' },
      });
      fireEvent.change(screen.getByLabelText('API token'), {
        target: { value: 'secret-token' },
      });
      fireEvent.click(screen.getByRole('button', { name: /save tyrion connection/i }));

      await waitFor(() => {
        expect(endpoints.statements.updateExternalCandidateConnection).toHaveBeenCalledWith({
          base_url: 'https://tyrion.test',
          connector_ref: 'owl',
          api_token: 'secret-token',
          verify_ssl: true,
          timeout_seconds: 30,
        });
      });
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
