import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MetadataCorrection from './MetadataCorrection';

const { correctMock, getMock, writebackMock } = vi.hoisted(() => ({
  correctMock: vi.fn(),
  getMock: vi.fn(),
  writebackMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    metadata: {
      get: getMock,
      correct: correctMock,
      confirm: vi.fn(),
      writeback: writebackMock,
    },
  },
}));

const response = {
  document_id: 42,
  title: 'October statement',
  document_summary: {
    document_id: 42,
    title: 'October statement',
    account_identifier_display: 'ACCT-123456',
  },
  paperless_url: '/documents/42/details',
  extracted_fields: [
    {
      field_name: 'account_identifier',
      paperless_field: 'Account Identifier',
      value: 'ACCT-123456',
      has_value: true,
    },
  ],
  corrections: [],
  latest_corrections: {},
  field_mapping: { account_identifier: 'Account Identifier' },
  source_modified: '2026-10-18T12:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  getMock.mockResolvedValue(response);
  correctMock.mockResolvedValue({ written_to_paperless: true });
  writebackMock.mockResolvedValue({
    document_id: 42,
    written_fields: [],
    missing_fields: [],
    update_count: 0,
  });
});

describe('MetadataCorrection account identifier', () => {
  it('shows the exact value and writes edits directly to Paperless with a classification', async () => {
    render(
      <MemoryRouter initialEntries={['/metadata/42']}>
        <Routes>
          <Route path="/metadata/:docId" element={<MetadataCorrection />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findAllByText('ACCT-123456')).not.toHaveLength(0);

    fireEvent.click(screen.getByTitle('Click to edit'));
    fireEvent.change(screen.getByLabelText('Account Identifier'), {
      target: { value: 'ACCT-654321' },
    });
    fireEvent.change(screen.getByLabelText('Identifier type'), {
      target: { value: 'provider_account' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(correctMock).toHaveBeenCalledWith('42', {
        field_name: 'account_identifier',
        corrected_value: 'ACCT-654321',
        original_value: 'ACCT-123456',
        confidence: undefined,
        identifier_class: 'provider_account',
        expected_modified: '2026-10-18T12:00:00Z',
        initiated_by: 'owl-metadata-correction',
      });
    });
    expect(await screen.findByText('Account Identifier updated in Paperless')).toBeInTheDocument();
  });

  it('resolves writeback conflicts field by field', async () => {
    getMock.mockResolvedValue({
      ...response,
      extracted_fields: [{
        field_name: 'provider_name',
        paperless_field: 'Provider Name',
        value: 'Loaded Provider',
        has_value: true,
      }],
      latest_corrections: {
        provider_name: {
          id: 'correction-1',
          document_id: 42,
          field_name: 'provider_name',
          original_value: 'Loaded Provider',
          corrected_value: 'OWL Provider',
          confidence: 80,
          correction_type: 'corrected',
          source_region: null,
          notes: null,
          created_at: null,
          created_by: 'user',
        },
      },
      field_mapping: { provider_name: 'Provider Name' },
    });
    writebackMock
      .mockRejectedValueOnce(Object.assign(new Error('Paperless changed'), {
        details: {
          error: {
            code: 'source_revision_conflict',
            message: 'Paperless metadata changed.',
            current_modified: 'revision-2',
            conflicts: [{
              field: 'provider_name',
              label: 'Provider Name',
              loaded_value: 'Loaded Provider',
              paperless_value: 'External Provider',
              owl_value: 'OWL Provider',
            }],
          },
        },
      }))
      .mockResolvedValueOnce({
        document_id: 42,
        written_fields: ['provider_name'],
        missing_fields: [],
        update_count: 1,
      });

    render(
      <MemoryRouter initialEntries={['/metadata/42']}>
        <Routes>
          <Route path="/metadata/:docId" element={<MetadataCorrection />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Save & Write to Paperless/ }));
    expect(await screen.findByText('Paperless metadata changed')).toBeInTheDocument();
    expect(screen.getByText('External Provider')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /Apply OWL/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Resolve & write' }));

    await waitFor(() => expect(writebackMock).toHaveBeenLastCalledWith('42', {
      expected_modified: 'revision-2',
      base_values: { provider_name: 'Loaded Provider' },
      field_names: ['provider_name'],
      initiated_by: 'owl-metadata-correction',
    }));
  });

  it('resolves an immediate account identifier conflict without discarding the edit', async () => {
    correctMock
      .mockRejectedValueOnce(Object.assign(new Error('Paperless changed'), {
        details: {
          error: {
            code: 'source_revision_conflict',
            message: 'This field changed in Paperless.',
            current_modified: 'revision-2',
            conflicts: [{
              field: 'account_identifier',
              label: 'Account Identifier',
              loaded_value: 'ACCT-123456',
              paperless_value: 'ACCT-999999',
              owl_value: 'ACCT-654321',
            }],
          },
        },
      }))
      .mockResolvedValueOnce({ written_to_paperless: true });

    render(
      <MemoryRouter initialEntries={['/metadata/42']}>
        <Routes>
          <Route path="/metadata/:docId" element={<MetadataCorrection />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByTitle('Click to edit'));
    fireEvent.change(screen.getByLabelText('Account Identifier'), {
      target: { value: 'ACCT-654321' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Paperless metadata changed')).toBeInTheDocument();
    expect(screen.getByText('ACCT-999999')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /Apply OWL/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Resolve & write' }));

    await waitFor(() => expect(correctMock).toHaveBeenLastCalledWith(
      '42',
      expect.objectContaining({
        corrected_value: 'ACCT-654321',
        expected_modified: 'revision-2',
      }),
    ));
  });
});
