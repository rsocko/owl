import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MetadataCorrection from './MetadataCorrection';

const { correctMock, getMock } = vi.hoisted(() => ({
  correctMock: vi.fn(),
  getMock: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    metadata: {
      get: getMock,
      correct: correctMock,
      confirm: vi.fn(),
      writeback: vi.fn(),
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
};

beforeEach(() => {
  vi.clearAllMocks();
  getMock.mockResolvedValue(response);
  correctMock.mockResolvedValue({ written_to_paperless: true });
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
      });
    });
    expect(await screen.findByText('Account Identifier updated in Paperless')).toBeInTheDocument();
  });
});
