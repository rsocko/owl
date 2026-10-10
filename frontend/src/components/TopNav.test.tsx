import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TopNav } from './TopNav';

describe('TopNav', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
    localStorage.clear();
  });

  it('uses the user-facing Needs Review label and route', () => {
    render(
      <MemoryRouter initialEntries={['/needs-review']}>
        <TopNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Workflow' }));

    expect(screen.getByRole('menuitem', { name: 'Needs Review' })).toHaveAttribute(
      'href',
      '/needs-review',
    );
    expect(screen.queryByText('Triage')).not.toBeInTheDocument();
  });

  it('links to the grouped Document Views launcher', () => {
    render(
      <MemoryRouter initialEntries={['/document-views']}>
        <TopNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Documents' }));

    expect(screen.getByRole('menuitem', { name: 'Document Views' })).toHaveAttribute(
      'href',
      '/document-views',
    );
  });
});
