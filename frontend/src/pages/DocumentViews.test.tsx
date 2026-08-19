import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { endpoints } from '../lib/api';
import DocumentViews from './DocumentViews';

vi.mock('../lib/api', () => ({
  endpoints: {
    documentViews: {
      list: vi.fn(),
    },
  },
}));

const catalog = {
  configured: true,
  generated_at: '2026-08-19T19:00:00Z',
  groups: [
    {
      id: 'daily-review',
      label: 'Daily Review',
      description: 'Frequent checks',
      default_expanded: true,
      views: [
        {
          id: 'inbox',
          label: 'Inbox',
          description: 'Paperless inbox',
          provider: 'paperless',
          source_id: 7,
          launch: 'paperless',
          href: 'https://paperless.test/view/7',
          count: 12,
          availability: 'ready',
          checked_at: '2026-08-19T19:00:00Z',
          error: null,
        },
        {
          id: 'needs-review',
          label: 'Needs Review',
          description: 'Derived OWL review state',
          provider: 'owl',
          source_id: 'triage.pending',
          launch: 'owl',
          href: '/triage',
          count: 3,
          availability: 'ready',
          checked_at: '2026-08-19T19:00:00Z',
          error: null,
        },
      ],
    },
    {
      id: 'cleanup',
      label: 'Cleanup',
      description: null,
      default_expanded: false,
      views: [
        {
          id: 'unsupported',
          label: 'Unsupported count',
          description: null,
          provider: 'paperless',
          source_id: 8,
          launch: 'paperless',
          href: 'https://paperless.test/view/8',
          count: null,
          availability: 'unsupported',
          checked_at: '2026-08-19T19:00:00Z',
          error: {
            code: 'saved_view_unsupported',
            message: 'This saved view uses filter rules OWL cannot safely count.',
          },
        },
      ],
    },
  ],
};

describe('DocumentViews', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(endpoints.documentViews.list).mockResolvedValue(catalog);
  });

  it('groups views and uses provider-aware launch behavior', async () => {
    render(
      <MemoryRouter>
        <DocumentViews />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Inbox' })).toBeInTheDocument();
    expect(screen.getByLabelText('12 documents')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open in Paperless/ })).toHaveAttribute(
      'href',
      'https://paperless.test/view/7',
    );
    expect(screen.getByRole('link', { name: /Open OWL review/ })).toHaveAttribute('href', '/triage');
    expect(screen.getAllByText('Paperless')).toHaveLength(1);
    expect(screen.getByText('OWL')).toBeInTheDocument();
  });

  it('collapses and expands configured groups while preserving launch access on count errors', async () => {
    render(
      <MemoryRouter>
        <DocumentViews />
      </MemoryRouter>,
    );

    const cleanup = await screen.findByRole('button', { name: /Cleanup/ });
    expect(cleanup).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(cleanup);

    expect(screen.getByRole('heading', { name: 'Unsupported count' })).toBeInTheDocument();
    expect(screen.getByText('Count unsupported')).toBeInTheDocument();
    const paperlessLinks = screen.getAllByRole('link', { name: /Open in Paperless/ });
    expect(
      paperlessLinks.find((link) => link.getAttribute('href') === 'https://paperless.test/view/8'),
    ).toBeDefined();
  });

  it('refreshes counts on demand', async () => {
    render(
      <MemoryRouter>
        <DocumentViews />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: 'Inbox' });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh counts' }));

    await waitFor(() => expect(endpoints.documentViews.list).toHaveBeenCalledTimes(2));
  });

  it('shows an unconfigured empty state', async () => {
    vi.mocked(endpoints.documentViews.list).mockResolvedValue({
      configured: false,
      generated_at: '2026-08-19T19:00:00Z',
      groups: [],
    });

    render(
      <MemoryRouter>
        <DocumentViews />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Document Views is not configured')).toBeInTheDocument();
  });

  it('shows a retryable global error', async () => {
    vi.mocked(endpoints.documentViews.list).mockRejectedValue(new Error('Catalog unavailable'));

    render(
      <MemoryRouter>
        <DocumentViews />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Catalog unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
