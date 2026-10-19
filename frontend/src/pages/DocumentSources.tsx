import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  Filter,
  Link2,
  LoaderCircle,
  Search,
  SlidersHorizontal,
  RefreshCw,
  Unlink,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import {
  formatDate,
  humanize,
  sourceDocumentCount,
  type DocumentExpectation,
  type DocumentSeries,
  type DocumentSource,
  type ExternalSignal,
  type SourceDisposition,
} from '../lib/documentSources';

type SourceMode = 'review' | 'signals' | 'directory';
type FilterState = {
  reason: string;
  disposition: string;
  evidence: string;
  relationship: string;
};

const EMPTY_FILTERS: FilterState = {
  reason: '',
  disposition: '',
  evidence: '',
  relationship: '',
};

const DISPOSITIONS: Array<{ value: SourceDisposition; label: string; description: string }> = [
  {
    value: 'expectations_configured',
    label: 'Expectations configured',
    description: 'One or more confirmed document expectations intentionally exist.',
  },
  {
    value: 'no_series_expected',
    label: 'No series expected',
    description: 'Documents from this correspondent are isolated or one-off.',
  },
  {
    value: 'waiting_for_history',
    label: 'Waiting for more history',
    description: 'Evidence is too sparse; revisit after more history or on a chosen date.',
  },
  {
    value: 'ignored_retired',
    label: 'Ignored or retired',
    description: 'Keep the audit history but remove this source from active intelligence.',
  },
];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The Document Sources workspace could not complete the request.';
}

function sourceReason(source: DocumentSource): string {
  if (source.reopen_reasons.length === 0) return 'Manual review';
  return source.reopen_reasons.map(humanize).join(', ');
}

function sourceRelationshipCount(sourceId: number, signals: ExternalSignal[]): number {
  return signals.filter((signal) => signal.relationship?.source_correspondent_id === sourceId).length;
}

function normalizeMatchText(value: string): string {
  return value
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function bigramSimilarity(left: string, right: string): number {
  const a = normalizeMatchText(left);
  const b = normalizeMatchText(right);
  if (!a || !b) return 0;
  if (a === b) return 1;
  if (a.includes(b) || b.includes(a)) return Math.min(a.length, b.length) / Math.max(a.length, b.length);

  const bigrams = (value: string) => {
    const result = new Map<string, number>();
    for (let index = 0; index < value.length - 1; index += 1) {
      const pair = value.slice(index, index + 2);
      result.set(pair, (result.get(pair) ?? 0) + 1);
    }
    return result;
  };
  const leftPairs = bigrams(a);
  const rightPairs = bigrams(b);
  let overlap = 0;
  leftPairs.forEach((count, pair) => {
    overlap += Math.min(count, rightPairs.get(pair) ?? 0);
  });
  return (2 * overlap) / Math.max(1, a.length + b.length - 2);
}

function sourceMatchScore(source: DocumentSource, signal: ExternalSignal): number {
  const sourceNames = [source.current_name, ...source.aliases];
  const institutionName = signal.institution_name;
  const institutionScore = institutionName
    ? Math.max(...sourceNames.map((name) => bigramSimilarity(name, institutionName)))
    : 0;
  const context = [signal.account_name, signal.display_hint].filter((value): value is string => Boolean(value));
  const contextScore = context.length > 0
    ? Math.max(...sourceNames.flatMap((name) => context.map((value) => bigramSimilarity(name, value))))
    : 0;
  return Math.max(institutionScore, contextScore * 0.82);
}

function recommendedSourceForSignal(sources: DocumentSource[], signal: ExternalSignal): DocumentSource | null {
  const ranked = sources
    .filter((source) => source.lifecycle === 'active')
    .map((source) => ({ source, score: sourceMatchScore(source, signal) }))
    .sort((left, right) => right.score - left.score || left.source.current_name.localeCompare(right.source.current_name));
  return ranked[0]?.score >= 0.55 ? ranked[0].source : null;
}

function SourceTypeahead({
  signal,
  sources,
  sourceId,
  onChange,
}: {
  signal: ExternalSignal;
  sources: DocumentSource[];
  sourceId: string;
  onChange: (sourceId: string) => void;
}) {
  const activeSources = useMemo(() => sources.filter((source) => source.lifecycle === 'active'), [sources]);
  const rankedSources = useMemo(() => activeSources
    .map((source) => ({ source, score: sourceMatchScore(source, signal) }))
    .sort((left, right) => right.score - left.score || left.source.current_name.localeCompare(right.source.current_name)), [activeSources, signal]);
  const recommended = rankedSources[0]?.score >= 0.55 ? rankedSources[0] : null;
  const exactInstitutionMatch = recommended && signal.institution_name
    ? [recommended.source.current_name, ...recommended.source.aliases]
        .some((name) => normalizeMatchText(name) === normalizeMatchText(signal.institution_name!))
    : false;
  const selectedSource = activeSources.find((source) => source.correspondent_id.toString() === sourceId) ?? null;
  const [query, setQuery] = useState(selectedSource?.current_name ?? '');
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = `signal-source-options-${signal.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;

  const visibleSources = useMemo(() => {
    const normalizedQuery = normalizeMatchText(query);
    if (!normalizedQuery || selectedSource?.current_name === query) return rankedSources.slice(0, 8);
    return rankedSources
      .map((item) => ({
        ...item,
        queryScore: Math.max(
          bigramSimilarity(item.source.current_name, normalizedQuery),
          ...item.source.aliases.map((alias) => bigramSimilarity(alias, normalizedQuery)),
        ),
      }))
      .filter((item) => item.queryScore >= 0.3)
      .sort((left, right) => right.queryScore - left.queryScore || right.score - left.score)
      .slice(0, 8);
  }, [query, rankedSources, selectedSource]);

  useEffect(() => {
    setQuery('');
    setActiveIndex(0);
  }, [signal.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (sourceId) {
      setQuery(activeSources.find((source) => source.correspondent_id.toString() === sourceId)?.current_name ?? '');
    }
  }, [activeSources, sourceId]);

  const selectSource = (source: DocumentSource) => {
    onChange(source.correspondent_id.toString());
    setQuery(source.current_name);
    setOpen(false);
  };

  return (
    <div className="source-typeahead">
      <label htmlFor={`${listboxId}-input`}>Document source</label>
      <div className="source-typeahead-control">
        <Search aria-hidden="true" />
        <input
          ref={inputRef}
          id={`${listboxId}-input`}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={open && visibleSources[activeIndex] ? `${listboxId}-${visibleSources[activeIndex].source.correspondent_id}` : undefined}
          autoComplete="off"
          placeholder="Search Paperless correspondents"
          value={query}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onChange={(event) => {
            setQuery(event.target.value);
            onChange('');
            setActiveIndex(0);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              setOpen(true);
              setActiveIndex((current) => Math.min(current + 1, visibleSources.length - 1));
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              setActiveIndex((current) => Math.max(current - 1, 0));
            } else if (event.key === 'Enter' && open && visibleSources[activeIndex]) {
              event.preventDefault();
              selectSource(visibleSources[activeIndex].source);
            } else if (event.key === 'Escape') {
              setOpen(false);
              setQuery(selectedSource?.current_name ?? '');
            }
          }}
        />
        <button type="button" aria-label="Show Paperless correspondents" onClick={() => { setOpen((current) => !current); inputRef.current?.focus(); }}>
          <ChevronDown aria-hidden="true" />
        </button>
      </div>
      {recommended ? (
        <p className="source-typeahead-hint">
          Tyrion match: <strong>{recommended.source.current_name}</strong>
          <span>{exactInstitutionMatch ? 'Exact institution match' : 'Closest Tyrion text match'}</span>
        </p>
      ) : null}
      {open ? (
        <div className="source-typeahead-options" id={listboxId} role="listbox">
          {visibleSources.length > 0 ? visibleSources.map(({ source }, index) => {
            const isRecommended = source.correspondent_id === recommended?.source.correspondent_id;
            const isSelected = source.correspondent_id.toString() === sourceId;
            return (
              <button
                type="button"
                id={`${listboxId}-${source.correspondent_id}`}
                role="option"
                aria-selected={isSelected}
                className={[isSelected ? 'selected' : '', index === activeIndex ? 'active' : ''].filter(Boolean).join(' ')}
                key={source.correspondent_id}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectSource(source)}
              >
                <span>
                  <strong>{source.current_name}</strong>
                  <small>Paperless #{source.correspondent_id}{source.aliases.length > 0 ? ` · Also known as ${source.aliases.join(', ')}` : ''}</small>
                </span>
                {isRecommended ? <em>Suggested</em> : null}
                {isSelected ? <Check aria-hidden="true" /> : null}
              </button>
            );
          }) : <p>No Paperless correspondents match “{query}”.</p>}
        </div>
      ) : null}
    </div>
  );
}

function FilterPopover({
  filters,
  onChange,
  onClose,
}: {
  filters: FilterState;
  onChange: (filters: FilterState) => void;
  onClose: () => void;
}) {
  const update = (key: keyof FilterState, value: string) => onChange({ ...filters, [key]: value });
  return (
    <div className="sources-filter-popover" role="dialog" aria-label="Filter document sources">
      <header>
        <strong>Filter document sources</strong>
        <button type="button" onClick={() => onChange(EMPTY_FILTERS)}>Clear</button>
      </header>
      <label>
        Review reason
        <select value={filters.reason} onChange={(event) => update('reason', event.target.value)}>
          <option value="">Any reason</option>
          <option value="new_source">New correspondent</option>
          <option value="repeatable_pattern_detected">New pattern detected</option>
          <option value="new_document">New document</option>
          <option value="waiting_revisit_due">Revisit due</option>
          <option value="external_relationship_proposed">External relationship proposed</option>
        </select>
      </label>
      <label>
        Disposition
        <select value={filters.disposition} onChange={(event) => update('disposition', event.target.value)}>
          <option value="">Any disposition</option>
          <option value="unresolved">No durable disposition</option>
          {DISPOSITIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
      </label>
      <label>
        Evidence
        <select value={filters.evidence} onChange={(event) => update('evidence', event.target.value)}>
          <option value="">Any evidence</option>
          <option value="none">No reviewed documents</option>
          <option value="single">Single document</option>
          <option value="series">Reviewed series</option>
          <option value="multiple">Multiple documents</option>
        </select>
      </label>
      <label>
        System relationship
        <select value={filters.relationship} onChange={(event) => update('relationship', event.target.value)}>
          <option value="">Any relationship</option>
          <option value="tyrion">Tyrion linked</option>
          <option value="paperless">Paperless only</option>
        </select>
      </label>
      <Button variant="primary" onClick={onClose}>Apply filters</Button>
    </div>
  );
}

function SourceRail({
  mode,
  onModeChange,
  sources,
  signals,
  series,
  selectedSourceId,
  selectedSignalId,
  onSelectSource,
  onSelectSignal,
  onSync,
  syncing,
  loading,
}: {
  mode: SourceMode;
  onModeChange: (mode: SourceMode) => void;
  sources: DocumentSource[];
  signals: ExternalSignal[];
  series: DocumentSeries[];
  selectedSourceId: number | null;
  selectedSignalId: string | null;
  onSelectSource: (id: number) => void;
  onSelectSignal: (id: string) => void;
  onSync: () => void;
  syncing: boolean;
  loading: boolean;
}) {
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS);
  const [filterOpen, setFilterOpen] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const activeFilterCount = Object.values(filters).filter(Boolean).length;

  const visibleSources = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return sources.filter((source) => {
      if (mode === 'review' && source.review_state !== 'needs_review') return false;
      if (normalized && ![source.current_name, ...source.aliases].some((value) => value.toLowerCase().includes(normalized))) {
        return false;
      }
      if (filters.reason && !source.reopen_reasons.includes(filters.reason as never)) return false;
      if (filters.disposition === 'unresolved' && source.disposition !== null) return false;
      if (filters.disposition && filters.disposition !== 'unresolved' && source.disposition !== filters.disposition) return false;
      const count = sourceDocumentCount(source.correspondent_id, series);
      const hasSeries = series.some((item) => item.source_correspondent_id === source.correspondent_id);
      if (filters.evidence === 'none' && count !== 0) return false;
      if (filters.evidence === 'single' && count !== 1) return false;
      if (filters.evidence === 'multiple' && count < 2) return false;
      if (filters.evidence === 'series' && !hasSeries) return false;
      const links = sourceRelationshipCount(source.correspondent_id, signals);
      if (filters.relationship === 'tyrion' && links === 0) return false;
      if (filters.relationship === 'paperless' && links > 0) return false;
      return true;
    });
  }, [filters, mode, search, series, signals, sources]);

  const visibleSignals = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return signals.filter((signal) => {
      const status = signal.relationship?.resolution ?? 'unresolved';
      if (!['unresolved', 'deferred'].includes(status)) return false;
      return !normalized || [
        signal.display_hint,
        signal.account_name,
        signal.institution_name,
        signal.account_last_four,
      ].some((value) => value?.toLowerCase().includes(normalized));
    });
  }, [search, signals]);

  const navigateList = (direction: 1 | -1) => {
    if (mode === 'signals') {
      const current = visibleSignals.findIndex((item) => item.id === selectedSignalId);
      const next = visibleSignals[Math.max(0, Math.min(visibleSignals.length - 1, current + direction))];
      if (next) onSelectSignal(next.id);
      return;
    }
    const current = visibleSources.findIndex((item) => item.correspondent_id === selectedSourceId);
    const next = visibleSources[Math.max(0, Math.min(visibleSources.length - 1, current + direction))];
    if (next) onSelectSource(next.correspondent_id);
  };

  return (
    <aside className="sources-rail" aria-label="Document source queue">
      <header className="sources-rail-heading">
        <div>
          <h1>Document Sources</h1>
          <p>{mode === 'review' ? `${visibleSources.length} correspondents need review` : mode === 'signals' ? `${visibleSignals.length} unresolved signals` : `${visibleSources.length} Paperless correspondents`}</p>
        </div>
        <button type="button" className="sources-sync" onClick={onSync} disabled={syncing} aria-label="Sync Paperless correspondents">
          <RefreshCw className={syncing ? 'spin' : ''} />
        </button>
      </header>
      <div className="sources-rail-tools">
        <div className="sources-mode-switch" role="group" aria-label="Document source mode">
          <button className={mode === 'review' ? 'active' : ''} onClick={() => onModeChange('review')} type="button">
            Needs review <span>{sources.filter((item) => item.review_state === 'needs_review').length}</span>
          </button>
          <button className={mode === 'signals' ? 'active' : ''} onClick={() => onModeChange('signals')} type="button">
            External signals <span>{signals.filter((item) => ['unresolved', 'deferred'].includes(item.relationship?.resolution ?? 'unresolved')).length}</span>
          </button>
          <button className={mode === 'directory' ? 'active' : ''} onClick={() => onModeChange('directory')} type="button">
            All correspondents <span>{sources.length}</span>
          </button>
        </div>
        <div className="sources-search-row">
          <label className="sources-search">
            <Search aria-hidden="true" />
            <input
              type="search"
              aria-label={mode === 'signals' ? 'Search external signals' : 'Search document sources'}
              placeholder={mode === 'signals' ? 'Find account or obligation' : 'Find correspondent or series'}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          {mode !== 'signals' ? (
            <div className="sources-filter-wrap">
              <button
                className="sources-filter-trigger"
                type="button"
                aria-label="Filter document sources"
                aria-expanded={filterOpen}
                onClick={() => setFilterOpen((open) => !open)}
              >
                <Filter aria-hidden="true" />
                {activeFilterCount > 0 ? <span>{activeFilterCount}</span> : null}
              </button>
              {filterOpen ? <FilterPopover filters={filters} onChange={setFilters} onClose={() => setFilterOpen(false)} /> : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="sources-list-label">
        <span>{mode === 'review' ? 'Correspondents needing review' : mode === 'signals' ? 'Signals needing reconciliation' : 'Paperless correspondent directory'}</span>
        <span><ArrowUp /> <ArrowDown /> navigate</span>
      </div>
      <div
        className="sources-list"
        ref={listRef}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            navigateList(event.key === 'ArrowDown' ? 1 : -1);
          }
        }}
      >
        {loading ? (
          Array.from({ length: 6 }).map((_, index) => <div className="sources-list-skeleton" key={index} />)
        ) : mode === 'signals' ? (
          visibleSignals.length > 0 ? visibleSignals.map((signal) => (
            <button
              type="button"
              className={`source-list-item ${selectedSignalId === signal.id ? 'active' : ''}`}
              key={signal.id}
              onClick={() => onSelectSignal(signal.id)}
            >
              <span>
                <strong>{signal.display_hint}</strong>
                <small>{signal.kind === 'accountStatementCandidate' ? 'Tyrion account' : 'Tyrion obligation'} · {Math.round(signal.confidence * 100)}% evidence</small>
                <small>{signal.relationship?.resolution === 'deferred' ? `Deferred until ${formatDate(signal.relationship.revisit_date)}` : 'No reviewed relationship'}</small>
              </span>
              <span className="source-list-badge signal">Signal</span>
            </button>
          )) : <QueueEmpty title="No external signals match" body="Clear the search or wait for new Tyrion evidence." />
        ) : visibleSources.length > 0 ? visibleSources.map((source) => {
          const count = sourceDocumentCount(source.correspondent_id, series);
          const links = sourceRelationshipCount(source.correspondent_id, signals);
          return (
            <button
              type="button"
              className={`source-list-item ${selectedSourceId === source.correspondent_id ? 'active' : ''}`}
              key={source.correspondent_id}
              onClick={() => onSelectSource(source.correspondent_id)}
            >
              <span>
                <strong>{source.current_name}</strong>
                <small>{source.review_state === 'needs_review' ? sourceReason(source) : humanize(source.disposition ?? 'not reviewed')}</small>
                <small>{count} reviewed {count === 1 ? 'document' : 'documents'} · {links > 0 ? `${links} Tyrion ${links === 1 ? 'link' : 'links'}` : 'Paperless only'}</small>
              </span>
              <span className={`source-list-badge ${source.review_state === 'resolved' ? 'resolved' : ''}`}>
                {source.review_state === 'needs_review' ? 'Review' : 'Done'}
              </span>
            </button>
          );
        }) : <QueueEmpty title="No document sources match" body="Clear search or filters to restore the queue." />}
      </div>
      <footer className="sources-rail-footer">
        {mode === 'review'
          ? 'This queue contains only new or materially changed correspondents.'
          : mode === 'signals'
            ? 'Tyrion supplies advisory evidence. OWL owns every reviewed relationship.'
            : 'The full Paperless directory includes intentionally inactive and one-off sources.'}
      </footer>
    </aside>
  );
}

function QueueEmpty({ title, body }: { title: string; body: string }) {
  return (
    <div className="sources-queue-empty">
      <Search aria-hidden="true" />
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}

function SourceEvidence({
  source,
  series,
  expectations,
  signals,
  onConfigure,
}: {
  source: DocumentSource;
  series: DocumentSeries[];
  expectations: DocumentExpectation[];
  signals: ExternalSignal[];
  onConfigure: (seriesId?: string) => void;
}) {
  const sourceSeries = series.filter((item) => item.source_correspondent_id === source.correspondent_id);
  const sourceExpectations = expectations.filter((item) => item.source_correspondent_id === source.correspondent_id);
  const linkedSignals = signals.filter((item) => item.relationship?.source_correspondent_id === source.correspondent_id);
  const documentCount = sourceDocumentCount(source.correspondent_id, series);

  return (
    <section className="source-evidence" aria-label="Selected source evidence">
      <header className="source-evidence-header">
        <div>
          <span>Paperless correspondent #{source.correspondent_id}</span>
          <h2>{source.current_name}</h2>
        </div>
        <dl>
          <div><dt>Documents</dt><dd>{documentCount}</dd></div>
          <div><dt>Reviewed series</dt><dd>{sourceSeries.length}</dd></div>
          <div><dt>Tyrion context</dt><dd>{linkedSignals.length}</dd></div>
        </dl>
      </header>
      <div className="source-evidence-scroll">
        <div className="source-reason">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>{source.review_state === 'needs_review' ? 'Why this source needs review' : 'Durable source decision'}</strong>
            <p>{source.review_state === 'needs_review'
              ? `${sourceReason(source)}. Review the observed series and choose one durable source outcome.`
              : `${humanize(source.disposition ?? 'not reviewed')} was recorded ${formatDate(source.last_reviewed_at)}.`}</p>
          </div>
        </div>
        <section className="source-section">
          <header>
            <div>
              <h3>Linked accounts and obligations</h3>
              <p>Tyrion provides context only. A source link never creates or selects an expectation.</p>
            </div>
          </header>
          {linkedSignals.length > 0 ? (
            <div className="source-relationships">
              {linkedSignals.map((signal) => (
                <article key={signal.id}>
                  <span>Tyrion {signal.kind === 'accountStatementCandidate' ? 'account' : 'obligation'}</span>
                  <strong>{signal.relationship?.safe_label || signal.display_hint}</strong>
                  <small>{signal.relationship?.expectation_ids.length
                    ? `Scoped to ${signal.relationship.expectation_ids.length} specific expectation${signal.relationship.expectation_ids.length === 1 ? '' : 's'}`
                    : 'Source-level context only · no expectation selected'}</small>
                </article>
              ))}
            </div>
          ) : <div className="source-inline-empty"><Unlink /> No Tyrion relationship is required for this source.</div>}
        </section>
        <section className="source-section">
          <header>
            <div>
              <h3>Reviewed document series</h3>
              <p>Membership identifies which documents belong together. Expectation policy remains separate.</p>
            </div>
          </header>
          {sourceSeries.length > 0 ? (
            <div className="source-series-list">
              {sourceSeries.map((item) => {
                const expectation = sourceExpectations.find((candidate) => candidate.series_id === item.id);
                return (
                  <article className="source-series-card" key={item.id}>
                    <div className="source-series-copy">
                      <div>
                        <span>{humanize(item.kind)} · membership revision {item.membership_revision}</span>
                        <h4>{item.name}</h4>
                        <p>{item.document_ids.length} reviewed {item.document_ids.length === 1 ? 'document' : 'documents'}{item.discriminator ? ` · ${item.discriminator}` : ''}</p>
                      </div>
                      <span className={`source-series-status ${expectation?.status === 'confirmed' ? 'confirmed' : ''}`}>
                        {expectation?.status === 'confirmed' ? humanize(expectation.expectation_mode) : 'Needs policy'}
                      </span>
                    </div>
                    <div className="source-document-strip" aria-label={`Representative Paperless evidence for ${item.name}`}>
                      {item.document_ids.slice(0, 4).map((documentId) => (
                        <a
                          key={documentId}
                          href={endpoints.statements.documentPreview(String(documentId))}
                          target="_blank"
                          rel="noreferrer"
                          className="source-document-sample"
                        >
                          <img src={endpoints.statements.documentThumb(String(documentId))} alt="" loading="lazy" />
                          <span><FileText /> Paperless #{documentId}</span>
                          <strong>Open representative document</strong>
                        </a>
                      ))}
                    </div>
                    <footer>
                      <span>Series revisions do not rewrite expectation policy.</span>
                      <Button variant={expectation ? 'default' : 'primary'} onClick={() => onConfigure(item.id)}>
                        {expectation ? 'Review expectation' : 'Configure expectation'} <ChevronRight />
                      </Button>
                    </footer>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="source-inline-empty">
              <FileText /> No reviewed series exists yet. You can still record a source disposition without inventing policy.
            </div>
          )}
        </section>
        <section className="source-section source-history">
          <header><div><h3>Review history</h3><p>Current evidence and durable decision timestamps.</p></div></header>
          <ol>
            <li><time>{formatDate(source.updated_at)}</time><span>Source record updated from canonical Paperless identity.</span></li>
            {source.last_evidence_at ? <li><time>{formatDate(source.last_evidence_at)}</time><span>Material source evidence changed.</span></li> : null}
            {source.last_reviewed_at ? <li><time>{formatDate(source.last_reviewed_at)}</time><span>Operator recorded a durable disposition.</span></li> : null}
          </ol>
        </section>
      </div>
    </section>
  );
}

function DispositionPanel({
  source,
  expectationCount,
  onSave,
  saving,
}: {
  source: DocumentSource;
  expectationCount: number;
  onSave: (disposition: SourceDisposition, revisitDate: string, notes: string) => Promise<void>;
  saving: boolean;
}) {
  const [disposition, setDisposition] = useState<SourceDisposition>(source.disposition ?? 'no_series_expected');
  const [revisitDate, setRevisitDate] = useState(source.waiting_revisit_date ?? '');
  const [notes, setNotes] = useState(source.notes ?? '');

  const configuredInvalid = disposition === 'expectations_configured' && expectationCount === 0;
  const waitingInvalid = disposition === 'waiting_for_history' && !revisitDate;

  return (
    <aside className="source-decision" aria-label="Complete source review">
      <header>
        <SlidersHorizontal aria-hidden="true" />
        <div><h3>Complete source review</h3><p>Choose a durable source-level outcome. Needs review remains workflow-only.</p></div>
      </header>
      <div className="source-disposition-options">
        {DISPOSITIONS.map((item) => (
          <label
            className={disposition === item.value ? 'active' : ''}
            key={item.value}
          >
            <input
              type="radio"
              name="source-disposition"
              value={item.value}
              checked={disposition === item.value}
              onChange={() => setDisposition(item.value)}
            />
            <span>{disposition === item.value ? <Check /> : null}</span>
            <strong>{item.label}</strong>
            <small>{item.description}</small>
          </label>
        ))}
      </div>
      {disposition === 'waiting_for_history' ? (
        <label>
          Revisit date
          <input type="date" value={revisitDate} onChange={(event) => setRevisitDate(event.target.value)} />
          <small>New material evidence can reopen the source before this date.</small>
        </label>
      ) : null}
      <label>
        Review note
        <textarea rows={4} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Record the evidence behind this durable decision." />
      </label>
      {configuredInvalid ? <p className="source-form-error" role="alert">Confirm at least one expectation before marking this source configured.</p> : null}
      {waitingInvalid ? <p className="source-form-error" role="alert">Choose a revisit date before waiting for more history.</p> : null}
      <Button
        variant="primary"
        disabled={saving || configuredInvalid || waitingInvalid}
        onClick={() => void onSave(disposition, revisitDate, notes)}
      >
        {saving ? <LoaderCircle className="spin" /> : <Check />} Save disposition
      </Button>
    </aside>
  );
}

function SignalWorkspace({
  signal,
  sources,
  expectations,
  onSave,
  saving,
}: {
  signal: ExternalSignal;
  sources: DocumentSource[];
  expectations: DocumentExpectation[];
  onSave: (body: {
    source_correspondent_id: number | null;
    expectation_ids: string[];
    resolution: 'linked' | 'not_expected' | 'deferred';
    safe_label: string | null;
    revisit_date: string | null;
  }) => Promise<void>;
  saving: boolean;
}) {
  const initialSuggestedSource = recommendedSourceForSignal(sources, signal);
  const [sourceId, setSourceId] = useState(
    signal.relationship?.source_correspondent_id?.toString() ?? initialSuggestedSource?.correspondent_id.toString() ?? '',
  );
  const [expectationIds, setExpectationIds] = useState<string[]>(signal.relationship?.expectation_ids ?? []);
  const [resolution, setResolution] = useState<'linked' | 'not_expected' | 'deferred'>(
    signal.relationship?.resolution === 'linked' || signal.relationship?.resolution === 'not_expected' || signal.relationship?.resolution === 'deferred'
      ? signal.relationship.resolution
      : 'linked',
  );
  const [revisitDate, setRevisitDate] = useState(signal.relationship?.revisit_date ?? '');
  const selectedSourceId = sourceId ? Number(sourceId) : null;
  const availableExpectations = expectations.filter((item) => item.source_correspondent_id === selectedSourceId);

  useEffect(() => {
    const suggestedSource = recommendedSourceForSignal(sources, signal);
    setSourceId(signal.relationship?.source_correspondent_id?.toString() ?? suggestedSource?.correspondent_id.toString() ?? '');
    setExpectationIds(signal.relationship?.expectation_ids ?? []);
    setResolution(
      signal.relationship?.resolution === 'linked' || signal.relationship?.resolution === 'not_expected' || signal.relationship?.resolution === 'deferred'
        ? signal.relationship.resolution
        : 'linked',
    );
    setRevisitDate(signal.relationship?.revisit_date ?? '');
  }, [signal, sources]);

  useEffect(() => {
    setExpectationIds((current) => current.filter((id) => availableExpectations.some((item) => item.id === id)));
  }, [sourceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const canSave = resolution === 'not_expected'
    || (resolution === 'deferred' && Boolean(revisitDate))
    || (resolution === 'linked' && selectedSourceId !== null);

  return (
    <section className="signal-workspace" aria-label={`Reconcile ${signal.display_hint}`}>
      <header>
        <div>
          <span>Tyrion {signal.kind === 'accountStatementCandidate' ? 'account signal' : 'obligation signal'}</span>
          <h2>{signal.display_hint}</h2>
          <p>This evidence may indicate a document relationship. It never establishes expectation policy on its own.</p>
        </div>
        <span className="signal-state">{humanize(signal.relationship?.resolution ?? 'unresolved')}</span>
      </header>
      <div className="signal-evidence">
        <section>
          <h3>Signal evidence</h3>
          <dl>
            <div><dt>System of record</dt><dd>Tyrion / Monarch</dd></div>
            <div><dt>Confidence</dt><dd>{Math.round(signal.confidence * 100)}%</dd></div>
            <div><dt>Institution</dt><dd>{signal.institution_name ?? 'Not supplied'}</dd></div>
            <div><dt>Account display</dt><dd>{signal.account_last_four ? `Ending ${signal.account_last_four}` : signal.account_name ?? 'Not supplied'}</dd></div>
          </dl>
          <div className="signal-basis">
            <strong>Why Tyrion proposed it</strong>
            <ul>{signal.basis.map((basis) => <li key={basis}>{humanize(basis)}</li>)}</ul>
          </div>
        </section>
        <section>
          <h3>Relationship invariant</h3>
          <div className="source-invariant">
            <Link2 />
            <p><strong>Source and expectation links are independent.</strong> Link the signal to a Paperless-anchored source first. Select expectations only when this exact account or obligation supports them.</p>
          </div>
        </section>
      </div>
      <aside className="signal-resolution">
        <h3>Resolve the signal</h3>
        <div className="signal-resolution-options">
          <button type="button" className={resolution === 'linked' ? 'active' : ''} onClick={() => setResolution('linked')}>
            <Link2 /><span><strong>Link document relationship</strong><small>Associate a source and optionally specific expectations.</small></span>
          </button>
          <button type="button" className={resolution === 'not_expected' ? 'active' : ''} onClick={() => { setResolution('not_expected'); setSourceId(''); setExpectationIds([]); }}>
            <Unlink /><span><strong>No document relationship</strong><small>Resolve this signal without creating an expectation.</small></span>
          </button>
          <button type="button" className={resolution === 'deferred' ? 'active' : ''} onClick={() => setResolution('deferred')}>
            <LoaderCircle /><span><strong>Defer</strong><small>Keep the evidence unresolved until a chosen date.</small></span>
          </button>
        </div>
        {resolution === 'linked' ? (
          <>
            <SourceTypeahead signal={signal} sources={sources} sourceId={sourceId} onChange={setSourceId} />
            <fieldset disabled={!sourceId}>
              <legend>Specific expectations <span>Optional</span></legend>
              {sourceId && availableExpectations.length === 0 ? <p>No expectations exist for this source. Linking will not create one.</p> : null}
              {availableExpectations.map((expectation) => (
                <label className="signal-check" key={expectation.id}>
                  <input
                    type="checkbox"
                    checked={expectationIds.includes(expectation.id)}
                    onChange={(event) => setExpectationIds((current) => event.target.checked ? [...current, expectation.id] : current.filter((id) => id !== expectation.id))}
                  />
                  <span><strong>{expectation.name}</strong><small>{humanize(expectation.expectation_mode)} · {humanize(expectation.status)}</small></span>
                </label>
              ))}
            </fieldset>
            <div className="source-invariant compact">
              <Check />
              <p>{expectationIds.length === 0
                ? 'This will create a source-level relationship with zero expectation links.'
                : `This will link ${expectationIds.length} existing expectation${expectationIds.length === 1 ? '' : 's'}. No new expectation will be created.`}</p>
            </div>
          </>
        ) : null}
        {resolution === 'deferred' ? (
          <label>Revisit date<input type="date" value={revisitDate} onChange={(event) => setRevisitDate(event.target.value)} /></label>
        ) : null}
        <Button
          variant="primary"
          disabled={!canSave || saving}
          onClick={() => void onSave({
            source_correspondent_id: resolution === 'linked' ? selectedSourceId : null,
            expectation_ids: resolution === 'linked' ? expectationIds : [],
            resolution,
            safe_label: resolution === 'linked' ? signal.display_hint : null,
            revisit_date: resolution === 'deferred' ? revisitDate : null,
          })}
        >
          {saving ? <LoaderCircle className="spin" /> : <Check />} Save resolution
        </Button>
      </aside>
    </section>
  );
}

export default function DocumentSources() {
  const navigate = useNavigate();
  const params = useParams();
  const routeSourceId = params.correspondentId ? Number(params.correspondentId) : null;
  const [mode, setMode] = useState<SourceMode>('review');
  const [sources, setSources] = useState<DocumentSource[]>([]);
  const [series, setSeries] = useState<DocumentSeries[]>([]);
  const [expectations, setExpectations] = useState<DocumentExpectation[]>([]);
  const [signals, setSignals] = useState<ExternalSignal[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(routeSourceId);
  const [selectedSignalId, setSelectedSignalId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sourceData, seriesData, expectationData, signalData] = await Promise.all([
        endpoints.documentSources.list() as Promise<DocumentSource[]>,
        endpoints.documentSources.series() as Promise<DocumentSeries[]>,
        endpoints.documentSources.expectations() as Promise<DocumentExpectation[]>,
        endpoints.documentSources.externalSignals() as Promise<ExternalSignal[]>,
      ]);
      setSources(sourceData);
      setSeries(seriesData);
      setExpectations(expectationData);
      setSignals(signalData);
      const requested = routeSourceId && sourceData.some((source) => source.correspondent_id === routeSourceId) ? routeSourceId : null;
      const fallback = sourceData.find((source) => source.review_state === 'needs_review')?.correspondent_id ?? sourceData[0]?.correspondent_id ?? null;
      setSelectedSourceId((current) => requested ?? (current && sourceData.some((source) => source.correspondent_id === current) ? current : fallback));
      setSelectedSignalId((current) => current && signalData.some((signal) => signal.id === current) ? current : signalData[0]?.id ?? null);
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [routeSourceId]);

  useEffect(() => { void load(); }, [load]);

  const selectedSource = sources.find((source) => source.correspondent_id === selectedSourceId) ?? null;
  const selectedSignal = signals.find((signal) => signal.id === selectedSignalId) ?? null;
  const confirmedExpectationCount = expectations.filter(
    (item) => item.source_correspondent_id === selectedSourceId && item.status === 'confirmed',
  ).length;

  const selectSource = (id: number) => {
    setDetailLoading(true);
    setSelectedSourceId(id);
    navigate(`/sources/${id}`, { replace: true });
    window.setTimeout(() => setDetailLoading(false), 100);
  };

  const saveDisposition = async (disposition: SourceDisposition, revisitDate: string, notes: string) => {
    if (!selectedSource) return;
    setSaving(true);
    try {
      const updated = await endpoints.documentSources.setDisposition(selectedSource.correspondent_id, {
        disposition,
        waiting_revisit_date: disposition === 'waiting_for_history' ? revisitDate : null,
        aliases: selectedSource.aliases,
        notes: notes.trim() || null,
      }) as DocumentSource;
      setSources((current) => current.map((source) => source.correspondent_id === updated.correspondent_id ? updated : source));
      setToast(`${updated.current_name} review completed.`);
      if (mode === 'review') {
        const next = sources.find((source) => source.correspondent_id !== updated.correspondent_id && source.review_state === 'needs_review');
        if (next) selectSource(next.correspondent_id);
      }
    } catch (saveError) {
      setToast(errorMessage(saveError));
    } finally {
      setSaving(false);
    }
  };

  const syncSources = async () => {
    setSyncing(true);
    try {
      const result = await endpoints.documentSources.sync() as { created: number; updated: number; orphaned: number };
      await load();
      setToast(`Paperless sync complete: ${result.created} new, ${result.updated} updated, ${result.orphaned} orphaned.`);
    } catch (syncError) {
      setToast(errorMessage(syncError));
    } finally {
      setSyncing(false);
    }
  };

  const saveSignal = async (body: Parameters<typeof SignalWorkspace>[0] extends never ? never : {
    source_correspondent_id: number | null;
    expectation_ids: string[];
    resolution: 'linked' | 'not_expected' | 'deferred';
    safe_label: string | null;
    revisit_date: string | null;
  }) => {
    if (!selectedSignal) return;
    setSaving(true);
    try {
      const relationship = await endpoints.documentSources.setExternalSignalRelationship(selectedSignal.id, body);
      setSignals((current) => current.map((signal) => signal.id === selectedSignal.id ? { ...signal, relationship: relationship as ExternalSignal['relationship'] } : signal));
      setToast('External signal resolution saved without creating expectation policy.');
      const next = signals.find((signal) => signal.id !== selectedSignal.id && ['unresolved', 'deferred'].includes(signal.relationship?.resolution ?? 'unresolved'));
      if (next) setSelectedSignalId(next.id);
    } catch (saveError) {
      setToast(errorMessage(saveError));
    } finally {
      setSaving(false);
    }
  };

  if (error && !loading) {
    return (
      <div className="sources-page-state" role="alert">
        <AlertTriangle />
        <h1>Document Sources could not load</h1>
        <p>{error}</p>
        <Button onClick={() => void load()}>Try again</Button>
      </div>
    );
  }

  return (
    <div className="sources-workspace">
      <SourceRail
        mode={mode}
        onModeChange={(nextMode) => setMode(nextMode)}
        sources={sources}
        signals={signals}
        series={series}
        selectedSourceId={selectedSourceId}
        selectedSignalId={selectedSignalId}
        onSelectSource={selectSource}
        onSelectSignal={setSelectedSignalId}
        onSync={() => void syncSources()}
        syncing={syncing}
        loading={loading}
      />
      {loading ? (
        <div className="sources-detail-loading" role="status"><LoaderCircle className="spin" /> Loading source evidence…</div>
      ) : mode === 'signals' ? (
        selectedSignal ? <SignalWorkspace signal={selectedSignal} sources={sources} expectations={expectations} onSave={saveSignal} saving={saving} /> : <WorkspaceEmpty />
      ) : selectedSource ? (
        <>
          <div className={detailLoading ? 'sources-detail is-loading' : 'sources-detail'}>
            <SourceEvidence
              source={selectedSource}
              series={series}
              expectations={expectations}
              signals={signals}
              onConfigure={(seriesId) => navigate(`/sources/${selectedSource.correspondent_id}/expectations/new${seriesId ? `?series=${seriesId}` : ''}`)}
            />
            <DispositionPanel
              key={selectedSource.correspondent_id}
              source={selectedSource}
              expectationCount={confirmedExpectationCount}
              onSave={saveDisposition}
              saving={saving}
            />
          </div>
        </>
      ) : <WorkspaceEmpty />}
      {toast ? <Toast message={toast} tone={toast.includes('could not') || toast.includes('failed') ? 'error' : 'success'} onDismiss={() => setToast(null)} /> : null}
    </div>
  );
}

function WorkspaceEmpty() {
  return (
    <div className="sources-page-state">
      <FileText />
      <h1>Nothing is selected</h1>
      <p>Choose a source or external signal from the queue.</p>
    </div>
  );
}
