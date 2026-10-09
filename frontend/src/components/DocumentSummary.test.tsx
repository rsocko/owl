import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import DocumentSummary from './DocumentSummary';
import { documentSummaryLabel } from './documentSummaryFormat';

describe('DocumentSummary', () => {
  it('renders normalized document context and an accessible label', () => {
    const summary = {
      document_id: 42,
      title: 'October statement',
      correspondent: 'Example Bank',
      document_type: 'Statement',
      document_date: '2026-10-01',
      date_label: 'Statement date',
      account_identifier_display: 'ending 3456',
    };

    render(<DocumentSummary summary={summary} density="review" />);

    expect(screen.getByText('October statement')).toBeInTheDocument();
    expect(screen.getByText(/Statement date/)).toBeInTheDocument();
    expect(screen.getByText('Account ending 3456')).toBeInTheDocument();
    expect(screen.getByLabelText(documentSummaryLabel(summary))).toBeInTheDocument();
  });

  it('falls back to stable document identity', () => {
    render(<DocumentSummary summary={{ document_id: 'abc' }} />);

    expect(screen.getByText('Document abc')).toBeInTheDocument();
    expect(screen.getByText('#abc')).toBeInTheDocument();
  });
});
