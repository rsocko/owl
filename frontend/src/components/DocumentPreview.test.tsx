import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DocumentPreview from './DocumentPreview';

const mocks = vi.hoisted(() => ({
  metadata: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  endpoints: {
    documents: {
      metadata: mocks.metadata,
      thumbnailUrl: (id: number) => `/thumbnail/${id}`,
      downloadUrl: (id: number) => `/download/${id}`,
    },
  },
}));

describe('DocumentPreview', () => {
  beforeEach(() => {
    mocks.metadata.mockReset();
  });

  it('shows the document title and original filename in compact mode', async () => {
    mocks.metadata.mockResolvedValue({
      id: 9746,
      title: 'Mutual One Heat Loan Statement – May 2026',
      original_file_name: 'document_20260529.pdf',
    });

    render(<DocumentPreview documentId={9746} variant="compact" />);

    expect(await screen.findByText('Mutual One Heat Loan Statement – May 2026')).toBeInTheDocument();
    expect(screen.getByText('document_20260529.pdf')).toBeInTheDocument();
  });

  it('does not repeat the filename when it is also the title', async () => {
    mocks.metadata.mockResolvedValue({
      id: 9746,
      title: 'document_20260529.pdf',
      original_file_name: 'document_20260529.pdf',
    });

    render(<DocumentPreview documentId={9746} variant="compact" />);

    expect(await screen.findByText('document_20260529.pdf')).toBeInTheDocument();
    expect(screen.getAllByText('document_20260529.pdf')).toHaveLength(1);
  });
});
