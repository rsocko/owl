import {
  AlertCircle,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  ExternalLink,
  FileSearch,
  RefreshCw,
  Search,
  ShieldAlert,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Button, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import {
  formatDate,
  humanize,
  type CoverageNearMatch,
  type CoverageOccurrence,
  type CoverageOccurrenceDetail,
  type CoverageState,
  type DocumentKind,
} from '../lib/documentSources';
import { getToastDuration } from '../lib/toast';
import '../styles/document-coverage.css';

const STATE_ORDER: CoverageState[] = [
  'missing',
  'due_soon',
  'fulfilled_late',
  'fulfilled',
  'upcoming',
  'unmonitored',
  'cancelled',
];

const KIND_OPTIONS: Array<{ value: DocumentKind | ''; label: string }> = [
  { value: '', label: 'All document kinds' },
  { value: 'statement', label: 'Statements' },
  { value: 'invoice', label: 'Invoices' },
  { value: 'bill', label: 'Bills' },
  { value: 'tax_document', label: 'Tax documents' },
  { value: 'renewal', label: 'Renewals' },
  { value: 'record', label: 'Records' },
  { value: 'receipt', label: 'Receipts' },
  { value: 'other', label: 'Other' },
];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function horizonStart(): string {
  const value = new Date();
  value.setMonth(value.getMonth() - 12);
  return value.toISOString().slice(0, 10);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Document Coverage could not complete the request.';
}

function stateLabel(state: CoverageState): string {
  if (state === 'fulfilled_late') return 'Fulfilled late';
  if (state === 'unmonitored') return 'Intentionally unmonitored';
  return humanize(state);
}

function StateIcon({ state }: { state: CoverageState }) {
  if (state === 'missing') return <ShieldAlert aria-hidden="true" />;
  if (state === 'due_soon') return <CalendarClock aria-hidden="true" />;
  if (state === 'fulfilled' || state === 'fulfilled_late') return <CheckCircle2 aria-hidden="true" />;
  if (state === 'unmonitored' || state === 'cancelled') return <CircleDashed aria-hidden="true" />;
  return <AlertCircle aria-hidden="true" />;
}

function OccurrenceDrawer({
  occurrence,
  onClose,
  onChanged,
}: {
  occurrence: CoverageOccurrence;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const [detail, setDetail] = useState<CoverageOccurrenceDetail | null>(null);
  const [matches, setMatches] = useState<CoverageNearMatch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [extendUntil, setExtendUntil] = useState(occurrence.grace_extended_until ?? occurrence.due_end);

  useEffect(() => {
    setError(null);
    setDetail(null);
    setMatches(null);
    void endpoints.coverage.detail(occurrence.id)
      .then((response) => setDetail(response as CoverageOccurrenceDetail))
      .catch((requestError) => setError(errorMessage(requestError)));
  }, [occurrence.id]);

  const loadMatches = async () => {
    setBusy(true);
    setError(null);
    try {
      setMatches(await endpoints.coverage.nearMatches(occurrence.id) as CoverageNearMatch[]);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  const extendGrace = async () => {
    setBusy(true);
    setError(null);
    try {
      await endpoints.coverage.extendGrace(occurrence.id, extendUntil);
      onChanged('Grace was extended for this occurrence only.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  const reassign = async (match: CoverageNearMatch) => {
    if (!match.document_date) {
      setError('This Paperless document has no usable document date.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await endpoints.coverage.reassign(occurrence.id, match.document_id, match.document_date.slice(0, 10));
      onChanged('The Paperless document now fulfills this occurrence.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  const retireExpectation = async () => {
    setBusy(true);
    setError(null);
    try {
      await endpoints.documentSources.updateExpectation(occurrence.expectation_id, {
        status: 'retired',
      });
      onChanged('The expectation was retired and will no longer emit active coverage gaps.');
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="coverage-drawer" aria-label={`Coverage detail for ${occurrence.expectation_name}`}>
      <header>
        <div>
          <span>{humanize(occurrence.kind)} · {occurrence.period_key}</span>
          <h2>{occurrence.expectation_name}</h2>
          <p>{occurrence.source_name}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close coverage detail"><X /></button>
      </header>
      {error ? <div className="coverage-inline-error" role="alert">{error}</div> : null}
      {!detail && !error ? <div className="coverage-drawer-loading" role="status">Loading occurrence evidence…</div> : null}
      {detail ? (
        <div className="coverage-drawer-scroll">
          <section className="coverage-finding">
            <StateIcon state={occurrence.state} />
            <div>
              <strong>{stateLabel(occurrence.state)}</strong>
              <p>Expected {formatDate(occurrence.due_start)} through {formatDate(occurrence.due_end)}. A qualifying Paperless document is the only event that marks this occurrence fulfilled.</p>
            </div>
          </section>
          <section>
            <h3>Expectation and evidence</h3>
            <dl className="coverage-detail-list">
              <div><dt>Importance</dt><dd>{humanize(detail.expectation.importance)}</dd></div>
              <div><dt>Monitoring</dt><dd>{humanize(detail.expectation.monitoring_mode)}</dd></div>
              <div><dt>Series</dt><dd>{detail.series?.name ?? 'No reviewed series'}</dd></div>
              <div><dt>Recent evidence</dt><dd>{detail.recent_document_ids.length ? detail.recent_document_ids.map((id) => `#${id}`).join(', ') : 'No recent series documents'}</dd></div>
            </dl>
            {detail.series ? <Link className="coverage-text-link" to={`/coverage/series/${detail.series.id}`}>Correct series membership <ExternalLink /></Link> : null}
          </section>
          <section>
            <h3>Recover this occurrence</h3>
            <p className="coverage-help">Use a near match only when the Paperless document belongs to this source and expected period. Membership changes do not rewrite expectation policy.</p>
            <Button onClick={() => void loadMatches()} disabled={busy}>
              <FileSearch /> {matches ? 'Refresh near matches' : 'Inspect near matches'}
            </Button>
            {matches ? (
              <div className="coverage-match-list">
                {matches.length === 0 ? <p>No nearby Paperless documents were found.</p> : matches.map((match) => (
                  <article key={match.document_id}>
                    <div>
                      <strong>{match.title}</strong>
                      <span>Paperless #{match.document_id} · {formatDate(match.document_date)}{match.document_type ? ` · ${match.document_type}` : ''}</span>
                    </div>
                    <Button size="sm" variant="primary" disabled={busy || match.already_assigned} onClick={() => void reassign(match)}>
                      {match.already_assigned ? 'Already assigned' : 'Use document'}
                    </Button>
                  </article>
                ))}
              </div>
            ) : null}
          </section>
          <section>
            <h3>Wait for a known delay</h3>
            <div className="coverage-grace-control">
              <label>Extend this occurrence through<input type="date" value={extendUntil} min={occurrence.due_end} onChange={(event) => setExtendUntil(event.target.value)} /></label>
              <Button disabled={busy || !extendUntil} onClick={() => void extendGrace()}>Extend grace</Button>
            </div>
          </section>
          <section>
            <h3>Correct the policy</h3>
            <p className="coverage-help">Change cadence, recognition, acquisition guidance, or retire the expectation in the existing source flow. This is separate from document membership.</p>
            <div className="coverage-policy-actions">
              <Link className="coverage-primary-link" to={`/sources/${occurrence.source_correspondent_id}/expectations/new?expectation=${occurrence.expectation_id}`}>Open expectation policy</Link>
              <Button variant="danger" disabled={busy} onClick={() => void retireExpectation()}>Retire expectation</Button>
            </div>
          </section>
          <section className="coverage-acquisition">
            <h3>Acquisition status</h3>
            <p>{detail.expectation.acquisition_notes
              ?? 'No automatic retrieval is claimed here. Follow the expectation’s manual or connector guidance, then refresh Coverage after Paperless ingests the document.'}</p>
          </section>
        </div>
      ) : null}
    </aside>
  );
}

export default function DocumentCoverage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [occurrences, setOccurrences] = useState<CoverageOccurrence[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<CoverageOccurrence | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);
  const [search, setSearch] = useState('');
  const stateFilter = searchParams.get('state') ?? '';
  const kindFilter = searchParams.get('kind') ?? '';
  const importanceFilter = searchParams.get('importance') ?? '';
  const sourceFilter = searchParams.get('source') ?? '';

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);
    try {
      setOccurrences(await endpoints.coverage.list(today(), horizonStart()) as CoverageOccurrence[]);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), getToastDuration(toast.tone));
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const sources = useMemo(() => Array.from(new Set(occurrences.map((item) => item.source_name))).sort(), [occurrences]);
  const visible = useMemo(() => occurrences
    .filter((item) => !stateFilter || item.state === stateFilter)
    .filter((item) => !kindFilter || item.kind === kindFilter)
    .filter((item) => !importanceFilter || item.importance === importanceFilter)
    .filter((item) => !sourceFilter || item.source_name === sourceFilter)
    .filter((item) => !search.trim() || [item.source_name, item.expectation_name, item.kind, item.period_key].some((value) => value.toLowerCase().includes(search.trim().toLowerCase())))
    .sort((left, right) => {
      const state = STATE_ORDER.indexOf(left.state) - STATE_ORDER.indexOf(right.state);
      return state || left.due_end.localeCompare(right.due_end);
    }), [importanceFilter, kindFilter, occurrences, search, sourceFilter, stateFilter]);
  const counts = useMemo(() => Object.fromEntries(STATE_ORDER.map((state) => [state, occurrences.filter((item) => item.state === state).length])), [occurrences]);

  const changed = async (message: string) => {
    setToast({ message, tone: 'success' });
    setSelected(null);
    await load(true);
  };

  return (
    <div className="coverage-workspace">
      <header className="coverage-page-header">
        <div>
          <h1>Document Coverage</h1>
          <p>See what should exist now across statements, invoices, tax documents, renewals, records, and other recurring evidence.</p>
        </div>
        <div>
          <Button onClick={() => void load(true)} disabled={refreshing}><RefreshCw className={refreshing ? 'spin' : ''} /> {refreshing ? 'Refreshing…' : 'Refresh coverage'}</Button>
          <Link className="coverage-primary-link" to="/sources">Manage sources</Link>
        </div>
      </header>
      <section className="coverage-summary" aria-label="Coverage summary">
        {(['missing', 'due_soon', 'fulfilled', 'unmonitored'] as CoverageState[]).map((state) => (
          <button key={state} type="button" className={stateFilter === state ? 'active' : ''} onClick={() => setFilter('state', stateFilter === state ? '' : state)}>
            <StateIcon state={state} /><span>{stateLabel(state)}</span><strong>{counts[state]}</strong>
          </button>
        ))}
      </section>
      <section className="coverage-toolbar" aria-label="Coverage filters">
        <label className="coverage-search"><Search /><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find source, expectation, period, or kind" /></label>
        <select aria-label="Coverage state" value={stateFilter} onChange={(event) => setFilter('state', event.target.value)}>
          <option value="">All coverage states</option>
          {STATE_ORDER.map((state) => <option key={state} value={state}>{stateLabel(state)}</option>)}
        </select>
        <select aria-label="Importance" value={importanceFilter} onChange={(event) => setFilter('importance', event.target.value)}>
          <option value="">All importance</option><option value="critical">Critical</option><option value="important">Important</option><option value="routine">Routine</option>
        </select>
        <select aria-label="Source" value={sourceFilter} onChange={(event) => setFilter('source', event.target.value)}>
          <option value="">All sources</option>{sources.map((source) => <option key={source} value={source}>{source}</option>)}
        </select>
        <select aria-label="Document kind" value={kindFilter} onChange={(event) => setFilter('kind', event.target.value)}>
          {KIND_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </section>
      <main className="coverage-ledger">
        <div className="coverage-ledger-heading"><span>Source and expectation</span><span>Expected window</span><span>Coverage</span><span>Evidence</span></div>
        {loading ? <div className="coverage-state-panel" role="status">Evaluating confirmed expectations…</div> : null}
        {!loading && error ? <div className="coverage-state-panel error" role="alert"><strong>Coverage is unavailable</strong><span>{error}</span><Button onClick={() => void load()}>Retry</Button></div> : null}
        {!loading && !error && visible.length === 0 ? <div className="coverage-state-panel"><strong>No occurrences match these filters</strong><span>Clear a filter or manage source expectations to change what Coverage evaluates.</span></div> : null}
        {!loading && !error ? visible.map((item) => (
          <button type="button" className="coverage-row" key={item.id} onClick={() => setSelected(item)}>
            <span className="coverage-row-title"><strong>{item.source_name}</strong><span>{item.expectation_name} · {humanize(item.kind)} · {humanize(item.importance)}</span></span>
            <span><strong>{formatDate(item.due_start)} – {formatDate(item.due_end)}</strong><small>{item.period_key}{item.grace_extended_until ? ` · extended to ${formatDate(item.grace_extended_until)}` : ''}</small></span>
            <span className={`coverage-status ${item.state}`}><StateIcon state={item.state} />{stateLabel(item.state)}</span>
            <span><strong>{item.fulfilled_document_id ? `Paperless #${item.fulfilled_document_id}` : 'No qualifying document'}</strong><small>{item.fulfilled_at ? formatDate(item.fulfilled_at) : 'Inspect evidence and recovery options'}</small></span>
          </button>
        )) : null}
      </main>
      {selected ? <OccurrenceDrawer occurrence={selected} onClose={() => setSelected(null)} onChanged={(message) => void changed(message)} /> : null}
      {selected ? <button className="coverage-scrim" aria-label="Close coverage detail" onClick={() => setSelected(null)} /> : null}
      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </div>
  );
}
