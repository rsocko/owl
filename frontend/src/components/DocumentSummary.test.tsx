import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import DocumentSummary from './DocumentSummary';

describe('DocumentSummary', () => {
  it('renders normalized context and an accessible differentiating label', () => {
    render(
      <DocumentSummary
        summary={{
          document_id: 42,
          title: 'August statement',
          correspondent: 'Example Bank',
          document_type: 'Statement',
          document_date: '2026-08-01',
          tags: ['finance'],
          account_identifier_display: '••••6789',
        }}
      />,
    );

    expect(screen.getByLabelText(/August statement, Example Bank, Statement/)).toBeInTheDocument();
    expect(screen.getByText('Account ••••6789')).toBeInTheDocument();
    expect(screen.getByText('finance')).toBeInTheDocument();
  });

  it('uses the stable document ID and omits missing rows', () => {
    const { container } = render(<DocumentSummary summary={{ document_id: 'abc' }} />);

    expect(screen.getByText('Document abc')).toBeInTheDocument();
    expect(container.querySelector('.document-summary-account')).toBeNull();
    expect(container.querySelector('.document-summary-tags')).toBeNull();
  });
});
