import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Badge, Button, EmptyState, ErrorState, PageHeader, SkeletonLoader } from '../components/ui';
import { endpoints } from '../lib/api';
import '../styles/document-views.css';

type ViewProvider = 'paperless' | 'owl';
type ViewLaunch = 'paperless' | 'owl';
type ViewAvailability = 'ready' | 'unavailable' | 'unsupported';

interface DocumentView {
  id: string;
  label: string;
  description: string | null;
  provider: ViewProvider;
  source_id: number | string;
  launch: ViewLaunch;
  href: string | null;
  count: number | null;
  availability: ViewAvailability;
  checked_at: string;
  error: { code: string; message: string } | null;
}

interface DocumentViewGroup {
  id: string;
  label: string;
  description: string | null;
  default_expanded: boolean;
  views: DocumentView[];
}

interface DocumentViewCatalog {
  configured: boolean;
  generated_at: string;
  groups: DocumentViewGroup[];
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Document views could not be loaded.';
}

function checkedLabel(value: string): string {
  const checked = new Date(value);
  if (Number.isNaN(checked.getTime())) return 'Freshness unavailable';
  return `Checked ${checked.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
}

function availabilityLabel(view: DocumentView): string {
  if (view.availability === 'unsupported') return 'Count unsupported';
  if (view.availability === 'unavailable') return 'Count unavailable';
  return checkedLabel(view.checked_at);
}

function ViewDestination({ view }: { view: DocumentView }) {
  const contents = (
    <>
      Open {view.launch === 'paperless' ? 'in Paperless' : 'OWL review'}
      <span aria-hidden="true">{view.launch === 'paperless' ? '↗' : '→'}</span>
    </>
  );

  if (!view.href) {
    return (
      <span className="document-view-link document-view-link--disabled" aria-disabled="true">
        Destination unavailable
      </span>
    );
  }
  if (view.launch === 'paperless') {
    return (
      <a className="document-view-link" href={view.href} target="_blank" rel="noreferrer">
        {contents}
      </a>
    );
  }
  return (
    <Link className="document-view-link" to={view.href}>
      {contents}
    </Link>
  );
}

export default function DocumentViews() {
  const [catalog, setCatalog] = useState<DocumentViewCatalog | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadCatalog = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      const response = (await endpoints.documentViews.list()) as DocumentViewCatalog;
      setCatalog(response);
      setExpandedGroups((current) => {
        if (current.size > 0) return current;
        return new Set(response.groups.filter((group) => group.default_expanded).map((group) => group.id));
      });
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  const toggleGroup = (groupId: string) => {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  };

  return (
    <>
      <PageHeader
        title="Document Views"
        desc="Launch curated Paperless searches and OWL review workflows from one grouped workspace."
        actions={
          <Button onClick={() => void loadCatalog(true)} disabled={loading || refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh counts'}
          </Button>
        }
      />

      {loading ? <SkeletonLoader variant="cards" /> : null}
      {!loading && error ? <ErrorState message={error} onRetry={() => void loadCatalog()} /> : null}

      {!loading && !error && catalog && catalog.groups.length === 0 ? (
        <EmptyState
          icon="📂"
          title={catalog.configured ? 'No views are allowlisted' : 'Document Views is not configured'}
          desc={
            catalog.configured
              ? 'Add at least one view to the configured catalog.'
              : 'Set DOCUMENT_VIEWS_CONFIG to a validated view catalog to enable this launcher.'
          }
        />
      ) : null}

      {!loading && !error && catalog && catalog.groups.length > 0 ? (
        <div className="document-view-groups">
          {catalog.groups.map((group) => {
            const expanded = expandedGroups.has(group.id);
            return (
              <section className="document-view-group" key={group.id}>
                <button
                  type="button"
                  className="document-view-group-toggle"
                  onClick={() => toggleGroup(group.id)}
                  aria-expanded={expanded}
                  aria-controls={`document-view-group-${group.id}`}
                >
                  <span>
                    <span className="document-view-group-title">{group.label}</span>
                    {group.description ? (
                      <span className="document-view-group-description">{group.description}</span>
                    ) : null}
                  </span>
                  <span className="document-view-group-meta">
                    {group.views.length} {group.views.length === 1 ? 'view' : 'views'}
                    <span className={`document-view-chevron ${expanded ? 'open' : ''}`} aria-hidden="true">
                      ▾
                    </span>
                  </span>
                </button>

                {expanded ? (
                  <div className="document-view-list" id={`document-view-group-${group.id}`}>
                    {group.views.length === 0 ? (
                      <EmptyState title="No views in this group" />
                    ) : (
                      group.views.map((view) => (
                        <article className="document-view-card" key={view.id}>
                          <div className="document-view-count" aria-label={`${view.count ?? 'Unavailable'} documents`}>
                            {view.count ?? '—'}
                          </div>
                          <div className="document-view-content">
                            <div className="document-view-heading">
                              <h2>{view.label}</h2>
                              <Badge tone={view.provider === 'paperless' ? 'muted' : 'info'}>
                                {view.provider === 'paperless' ? 'Paperless' : 'OWL'}
                              </Badge>
                            </div>
                            {view.description ? <p>{view.description}</p> : null}
                            <div className="document-view-status">
                              <span className={`document-view-status-dot ${view.availability}`} aria-hidden="true" />
                              {availabilityLabel(view)}
                            </div>
                            {view.error ? (
                              <div className="document-view-error" role="status">
                                {view.error.message}
                              </div>
                            ) : null}
                          </div>
                          <ViewDestination view={view} />
                        </article>
                      ))
                    )}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : null}
    </>
  );
}
