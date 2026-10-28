import { ArrowLeft, Merge, Scissors, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Button, Toast } from '../components/ui';
import { SourceDocumentInspector, SourceDocumentList } from '../components/SourceDocumentEvidence';
import { endpoints } from '../lib/api';
import { humanize, type DocumentExpectation, type DocumentSeries, type SourceDocument } from '../lib/documentSources';
import '../styles/document-coverage.css';

function message(error: unknown): string {
  return error instanceof Error ? error.message : 'The series membership change could not be saved.';
}

export default function DocumentSeriesDetail() {
  const { seriesId = '' } = useParams();
  const [series, setSeries] = useState<DocumentSeries | null>(null);
  const [allSeries, setAllSeries] = useState<DocumentSeries[]>([]);
  const [expectations, setExpectations] = useState<DocumentExpectation[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [newName, setNewName] = useState('');
  const [targetId, setTargetId] = useState('');
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [adding, setAdding] = useState<number[]>([]);
  const [search, setSearch] = useState('');
  const [visibleLimit, setVisibleLimit] = useState(20);
  const [preview, setPreview] = useState<SourceDocument | null>(null);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [seriesResponse, expectationResponse] = await Promise.all([
        endpoints.documentSources.series(),
        endpoints.documentSources.expectations(),
      ]) as [DocumentSeries[], DocumentExpectation[]];
      setAllSeries(seriesResponse);
      const current = seriesResponse.find((item) => item.id === seriesId) ?? null;
      setSeries(current);
      setExpectations(expectationResponse);
      if (current) {
        setDocumentsLoading(true);
        const evidence = await endpoints.documentSources.documents(current.source_correspondent_id) as SourceDocument[];
        setDocuments(evidence);
      } else {
        setError('This reviewed series no longer exists. Return to Document Sources and refresh.');
      }
    } catch (requestError) {
      setError(message(requestError));
    } finally {
      setDocumentsLoading(false);
    }
  }, [seriesId]);
  useEffect(() => { void load(); }, [load]);

  const attached = useMemo(() => expectations.filter((item) => item.series_id === seriesId), [expectations, seriesId]);
  const otherSeries = useMemo(() => allSeries.filter((item) => item.id !== seriesId && item.source_correspondent_id === series?.source_correspondent_id), [allSeries, series, seriesId]);
  const memberDocuments = documents.filter((document) => series?.document_ids.includes(document.id));
  const unavailableIds = series?.document_ids.filter((id) => !documents.some((document) => document.id === id)) ?? [];
  const assignedIds = new Set(allSeries.filter((item) => item.source_correspondent_id === series?.source_correspondent_id).flatMap((item) => item.document_ids));
  const availableDocuments = documents.filter((document) => !assignedIds.has(document.id));
  const matches = availableDocuments.filter((document) => [
    document.title, document.document_type, document.created, document.account_identifier_display,
    ...document.tags, String(document.id),
  ].filter(Boolean).join(' ').toLowerCase().includes(search.trim().toLowerCase()));
  const visibleDocuments = matches.slice(0, visibleLimit);

  const addDocuments = async () => {
    if (!adding.length) return;
    setBusy(true);
    setError(null);
    try {
      await endpoints.documentSources.addSeriesMembers(seriesId, adding);
      setAdding([]);
      setToast('Selected documents added. Existing members and expectation policy were preserved.');
      await load();
    } catch (requestError) {
      setError(message(requestError));
    } finally {
      setBusy(false);
    }
  };

  const saveMembers = async (documentIds: number[], success: string) => {
    setBusy(true);
    try {
      await endpoints.documentSources.updateSeries(seriesId, { document_ids: documentIds });
      setSelected([]);
      setToast(success);
      await load();
    } catch (requestError) {
      setError(message(requestError));
    } finally {
      setBusy(false);
    }
  };

  const split = async () => {
    if (!series || !newName.trim() || selected.length === 0) return;
    setBusy(true);
    try {
      await endpoints.documentSources.createSeries({
        source_correspondent_id: series.source_correspondent_id,
        name: newName.trim(),
        kind: series.kind,
        document_ids: selected,
      });
      await endpoints.documentSources.updateSeries(series.id, {
        document_ids: series.document_ids.filter((id) => !selected.includes(id)),
      });
      setNewName('');
      setSelected([]);
      setToast('Series split saved. Expectation policy was not changed.');
      await load();
    } catch (requestError) {
      setError(message(requestError));
    } finally {
      setBusy(false);
    }
  };

  const merge = async () => {
    if (!series || !targetId) return;
    const target = allSeries.find((item) => item.id === targetId);
    if (!target) return;
    setBusy(true);
    try {
      await endpoints.documentSources.updateSeries(target.id, {
        document_ids: Array.from(new Set([...target.document_ids, ...series.document_ids])),
      });
      await endpoints.documentSources.updateSeries(series.id, { document_ids: [] });
      setToast('Membership merged into the selected series. Expectation policy was not changed.');
      await load();
    } catch (requestError) {
      setError(message(requestError));
    } finally {
      setBusy(false);
    }
  };

  if (error && !series) return <div className="coverage-state-panel error" role="alert">{error}</div>;
  if (!series) return <div className="coverage-state-panel" role="status">Loading reviewed series…</div>;

  return (
    <main className="series-editor">
      <Link to="/coverage" className="coverage-text-link"><ArrowLeft /> Back to Document Coverage</Link>
      <header>
        <div><h1>{series.name}</h1><p>{humanize(series.kind)} series · membership revision {series.membership_revision}</p></div>
        <Link className="coverage-primary-link" to={`/sources/${series.source_correspondent_id}/expectations/new`}>Configure monitoring policy</Link>
      </header>
      <section className="series-policy-boundary">
        <strong>Membership and policy are separate</strong>
        <p>Add, remove, split, merge, or reassign documents here. These corrections recalculate coverage but never silently change cadence, importance, recognition, or acquisition policy.</p>
        <span>{attached.length ? `${attached.length} expectation${attached.length === 1 ? '' : 's'} reference this series` : 'No expectation currently references this series'}</span>
      </section>
      {error ? <div className="coverage-inline-error" role="alert">{error}</div> : null}
      <div className="series-editor-grid">
        <section>
          <h2>Documents in series</h2>
          <div className="series-member-list">
            {documentsLoading ? <p role="status">Loading document titles and evidence…</p> : null}
            {series.document_ids.length === 0 ? <p>No documents are assigned to this series.</p> : null}
            {memberDocuments.map((document) => (
              <div key={document.id}>
                <SourceDocumentList documents={[document]} selected={selected}
                  onToggle={(id) => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])}
                  onPreview={setPreview} />
                <Button size="sm" disabled={busy} onClick={() => void saveMembers(series.document_ids.filter((item) => item !== document.id), 'Document removed. Expectation policy was preserved.')}><Trash2 /> Remove</Button>
              </div>
            ))}
            {!documentsLoading && unavailableIds.map((id) => (
              <div key={id}><p>Document #{id} is unavailable in this source. Refresh or check Paperless.</p>
                <Button size="sm" disabled={busy} onClick={() => void saveMembers(series.document_ids.filter((item) => item !== id), 'Unavailable document removed. Expectation policy was preserved.')}><Trash2 /> Remove</Button>
              </div>
            ))}
          </div>
          <section className="series-evidence-picker">
            <h2>Add documents from this source</h2>
            <p>Review titles, dates, tags, account evidence, and previews before adding. Documents already in a reviewed series are excluded.</p>
            <label>Find documents<input type="search" value={search} onChange={(event) => { setSearch(event.target.value); setVisibleLimit(20); }}
              placeholder="Search title, date, type, tag, or account suffix" /></label>
            <p>{matches.length} matching documents · showing {visibleDocuments.length}. {adding.length} selected.</p>
            <SourceDocumentList documents={visibleDocuments} selected={adding}
              onToggle={(id) => setAdding((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])}
              onPreview={setPreview} />
            {visibleDocuments.length < matches.length ? <Button onClick={() => setVisibleLimit((current) => current + 20)}>Show more documents</Button> : null}
            {!documentsLoading && !matches.length ? <p>No unassigned documents match. Change the search or review another series to move its members.</p> : null}
            <Button variant="primary" disabled={busy || documentsLoading || !adding.length}
              onClick={() => void addDocuments()}>Add {adding.length || ''} selected documents</Button>
          </section>
        </section>
        <aside>
          <section>
            <h2><Scissors /> Split selected documents</h2>
            <p>Create a second reviewed series from the selected members.</p>
            <label>New series name<input value={newName} onChange={(event) => setNewName(event.target.value)} /></label>
            <Button variant="primary" disabled={busy || !newName.trim() || selected.length === 0} onClick={() => void split()}>Split {selected.length || ''} document{selected.length === 1 ? '' : 's'}</Button>
          </section>
          <section>
            <h2><Merge /> Merge membership</h2>
            <p>Move every member into another series from this source.</p>
            <label>Target series<select value={targetId} onChange={(event) => setTargetId(event.target.value)}><option value="">Choose a series</option>{otherSeries.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <Button disabled={busy || !targetId} onClick={() => void merge()}>Merge into target</Button>
          </section>
        </aside>
      </div>
      {toast ? <Toast message={toast} tone="success" onDismiss={() => setToast(null)} /> : null}
      {preview ? <SourceDocumentInspector document={preview} documents={documents}
        onChange={setPreview} onClose={() => setPreview(null)} /> : null}
    </main>
  );
}
