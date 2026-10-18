import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DocumentWorkspace from './DocumentWorkspace';

vi.mock('../components/document-workspace/workflows/InboxWorkflow', () => ({
  default: () => <div>Inbox workflow</div>,
}));

vi.mock('../components/document-workspace/workflows/ActionsWorkflow', () => ({
  default: () => <div>Actions workflow</div>,
}));

vi.mock('../components/document-workspace/workflows/ReviewWorkflow', () => ({
  default: () => <div>Review workflow</div>,
}));

describe('DocumentWorkspace', () => {
  it.each([
    ['inbox', 'Inbox workflow'],
    ['actions', 'Actions workflow'],
    ['review', 'Review workflow'],
  ] as const)('renders the %s workflow through the canonical page', (mode, label) => {
    render(<DocumentWorkspace mode={mode} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
