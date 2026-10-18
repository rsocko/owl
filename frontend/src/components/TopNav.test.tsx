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

    expect(screen.getByRole('link', { name: 'Needs Review' })).toHaveAttribute('href', '/needs-review');
    expect(screen.queryByText('Triage')).not.toBeInTheDocument();
  });

  it('promotes Inbox and Actions to top-level workflow destinations', () => {
    render(
      <MemoryRouter initialEntries={['/inbox-review']}>
        <TopNav />
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: 'Inbox' })).toHaveAttribute('href', '/inbox-review');
    expect(screen.getByRole('link', { name: 'Actions' })).toHaveAttribute('href', '/action-queue');
    expect(screen.queryByText('Intake Review')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Workflow' })).not.toBeInTheDocument();
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

  it('places OCR Quality with analytics rather than operational document queues', () => {
    render(
      <MemoryRouter initialEntries={['/ocr-quality']}>
        <TopNav />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Analytics' }));
    expect(screen.getByRole('menuitem', { name: 'OCR Quality' })).toHaveAttribute(
      'href',
      '/ocr-quality',
    );
  });
});
