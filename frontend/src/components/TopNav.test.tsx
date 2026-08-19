import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TopNav } from './TopNav';

describe('TopNav', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
    localStorage.clear();
  });

  it('uses the user-facing Needs Review label for the triage route', () => {
    render(
      <MemoryRouter initialEntries={['/triage']}>
        <TopNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Workflow' }));

    expect(screen.getByRole('menuitem', { name: 'Needs Review' })).toBeInTheDocument();
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
