import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endpoints } from '../lib/api';
import Settings from './Settings';

const {
  getTyrionMock,
  testTyrionMock,
  updateSettingsMock,
  updateTyrionMock,
} = vi.hoisted(() => ({
  getTyrionMock: vi.fn(),
  testTyrionMock: vi.fn(),
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
      externalCandidateConnection: getTyrionMock,
      updateExternalCandidateConnection: updateTyrionMock,
      testExternalCandidateConnection: testTyrionMock,
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
  getTyrionMock.mockResolvedValue({
    configured: false,
    token_configured: false,
    verify_ssl: true,
    timeout_seconds: 30,
  });
  testTyrionMock.mockResolvedValue({
    status: 'connected',
    message: 'OWL successfully connected to Tyrion.',
  });
  updateSettingsMock.mockResolvedValue({ status: 'ok' });
  updateTyrionMock.mockResolvedValue({
    configured: true,
    base_url: 'https://tyrion.test',
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
    it('shows saved configuration as unverified until a test succeeds', async () => {
      getTyrionMock.mockResolvedValueOnce({
        configured: true,
        source: 'saved',
        base_url: 'https://tyrion.test',
        token_configured: true,
        verify_ssl: true,
        timeout_seconds: 30,
      });
      render(<Settings />);

      expect(await screen.findAllByText('Saved · not tested')).toHaveLength(2);
      expect(screen.getAllByText('Connected')).toHaveLength(1);

      fireEvent.click(screen.getAllByRole('button', { name: /test connection/i })[0]);

      await waitFor(() => expect(screen.getAllByText('Connected')).toHaveLength(3));
      expect(screen.getAllByText('The effective configuration reached Tyrion successfully.')).toHaveLength(2);
      expect(testTyrionMock).toHaveBeenCalledWith();
    });

    it('saves a user-managed Tyrion connection and verifies it server-side', async () => {
      render(<Settings />);

      fireEvent.change(await screen.findByLabelText('Tyrion base URL'), {
        target: { value: 'https://tyrion.test' },
      });
      fireEvent.change(screen.getByLabelText('API token'), {
        target: { value: 'secret-token' },
      });
      fireEvent.click(screen.getByRole('button', { name: /save tyrion configuration/i }));

      await waitFor(() => {
        expect(endpoints.statements.updateExternalCandidateConnection).toHaveBeenCalledWith({
          base_url: 'https://tyrion.test',
          api_token: 'secret-token',
          verify_ssl: true,
          timeout_seconds: 30,
        });
        expect(testTyrionMock).toHaveBeenCalledWith();
      });
      await waitFor(() => expect(screen.getAllByText('Connected')).toHaveLength(3));
      expect(screen.getByText('Tyrion configuration saved and connection verified.')).toBeInTheDocument();
      expect((screen.getByLabelText('API token') as HTMLInputElement).value).toBe('');
    });

    it('keeps a saved configuration distinct when automatic verification fails', async () => {
      testTyrionMock.mockRejectedValueOnce(
        new Error("Tyrion's candidate endpoint was not found. Check the Tyrion base URL and version."),
      );
      render(<Settings />);

      fireEvent.change(await screen.findByLabelText('Tyrion base URL'), {
        target: { value: 'https://tyrion.test' },
      });
      fireEvent.click(screen.getByRole('button', { name: /save tyrion configuration/i }));

      expect(await screen.findAllByText('Connection failed')).toHaveLength(2);
      expect(screen.getByText(/configuration was saved, but the connection test failed/i)).toBeInTheDocument();
      expect(screen.getAllByText('Connected')).toHaveLength(1);
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
