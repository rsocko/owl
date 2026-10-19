import { ArrowLeft, Merge, Scissors, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Button, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { humanize, type DocumentExpectation, type DocumentSeries } from '../lib/documentSources';
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
  const [addId, setAddId] = useState('');
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
      setSeries(seriesResponse.find((item) => item.id === seriesId) ?? null);
      setExpectations(expectationResponse);
    } catch (requestError) {
      setError(message(requestError));
    }
  }, [seriesId]);
  useEffect(() => { void load(); }, [load]);

  const attached = useMemo(() => expectations.filter((item) => item.series_id === seriesId), [expectations, seriesId]);
  const otherSeries = useMemo(() => allSeries.filter((item) => item.id !== seriesId && item.source_correspondent_id === series?.source_correspondent_id), [allSeries, series, seriesId]);

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
            {series.document_ids.length === 0 ? <p>No documents are assigned to this series.</p> : series.document_ids.map((id) => (
              <label key={id}>
                <input type="checkbox" checked={selected.includes(id)} onChange={() => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])} />
                <span><strong>Paperless document #{id}</strong><small>Reviewed series member</small></span>
                <Button size="sm" disabled={busy} onClick={() => void saveMembers(series.document_ids.filter((item) => item !== id), 'Document removed. Expectation policy was preserved.')}><Trash2 /> Remove</Button>
              </label>
            ))}
          </div>
          <div className="series-add-row">
            <label>Add Paperless document ID<input inputMode="numeric" value={addId} onChange={(event) => setAddId(event.target.value)} /></label>
            <Button disabled={busy || !Number(addId)} onClick={() => { void saveMembers(Array.from(new Set([...series.document_ids, Number(addId)])), 'Document added. Coverage will use the updated membership.'); setAddId(''); }}>Add document</Button>
          </div>
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
    </main>
  );
}
