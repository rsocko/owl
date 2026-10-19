import { ArrowLeft, ArrowRight, Check, ChevronDown, FileCheck2, LoaderCircle, ShieldCheck } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button } from '../components/ui';
import { endpoints } from '../lib/api';
import {
  humanize,
  type AcquisitionSource,
  type DocumentExpectation,
  type DocumentKind,
  type DocumentSeries,
  type DocumentSource,
  type ExpectationMode,
} from '../lib/documentSources';

type Step = 0 | 1 | 2 | 3 | 4;
const STEPS = ['Identify the document', 'Decide whether it returns', 'Define when it is missing', 'Set filing policy', 'Record acquisition'];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Expectation Setup could not complete the request.';
}

export default function ExpectationSetup() {
  const navigate = useNavigate();
  const { correspondentId } = useParams();
  const [searchParams] = useSearchParams();
  const sourceId = Number(correspondentId);
  const requestedSeriesId = searchParams.get('series') ?? '';
  const requestedExpectationId = searchParams.get('expectation') ?? '';
  const [source, setSource] = useState<DocumentSource | null>(null);
  const [series, setSeries] = useState<DocumentSeries[]>([]);
  const [expectations, setExpectations] = useState<DocumentExpectation[]>([]);
  const [acquisitionSources, setAcquisitionSources] = useState<AcquisitionSource[]>([]);
  const [step, setStep] = useState<Step>(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seriesId, setSeriesId] = useState(requestedSeriesId);
  const [name, setName] = useState('');
  const [kind, setKind] = useState<DocumentKind>('statement');
  const [mode, setMode] = useState<ExpectationMode>('recurring');
  const [frequency, setFrequency] = useState<'monthly' | 'quarterly' | 'annual'>('monthly');
  const [expectedDay, setExpectedDay] = useState('28');
  const [graceDays, setGraceDays] = useState('7');
  const [dueStart, setDueStart] = useState('');
  const [dueEnd, setDueEnd] = useState('');
  const [importance, setImportance] = useState<'routine' | 'important' | 'critical'>('routine');
  const [titleTemplate, setTitleTemplate] = useState('{correspondent} - {period}');
  const [acquisitionSourceId, setAcquisitionSourceId] = useState('');
  const [acquisitionNotes, setAcquisitionNotes] = useState('');
  const [tyrionSignalId, setTyrionSignalId] = useState('');
  const [signals, setSignals] = useState<Array<{ id: string; display_hint: string; relationship?: { source_correspondent_id?: number | null; expectation_ids: string[] } | null }>>([]);

  useEffect(() => {
    if (!Number.isFinite(sourceId)) {
      setError('The source route is invalid.');
      setLoading(false);
      return;
    }
    void Promise.all([
      endpoints.documentSources.get(sourceId) as Promise<DocumentSource>,
      endpoints.documentSources.series(sourceId) as Promise<DocumentSeries[]>,
      endpoints.documentSources.expectations(sourceId) as Promise<DocumentExpectation[]>,
      endpoints.documentSources.acquisitionSources() as Promise<AcquisitionSource[]>,
      endpoints.documentSources.externalSignals() as Promise<Array<{ id: string; display_hint: string; relationship?: { source_correspondent_id?: number | null; expectation_ids: string[] } | null }>>,
    ]).then(([sourceData, seriesData, expectationData, acquisitionData, signalData]) => {
      setSource(sourceData);
      setSeries(seriesData);
      setExpectations(expectationData);
      setAcquisitionSources(acquisitionData);
      setSignals(signalData.filter((signal) => signal.relationship?.source_correspondent_id === sourceId));
      const requestedExpectation = expectationData.find((item) => item.id === requestedExpectationId);
      const selected = seriesData.find((item) => item.id === (requestedExpectation?.series_id ?? requestedSeriesId))
        ?? (!requestedExpectationId ? seriesData.find((item) => item.id === requestedSeriesId) : undefined);
      if (selected) {
        setSeriesId(selected.id);
        setName(selected.name);
        setKind(selected.kind);
      }
      if (requestedExpectation) {
        setSeriesId(requestedExpectation.series_id ?? '');
        setName(requestedExpectation.name);
        setKind(requestedExpectation.kind);
        setMode(requestedExpectation.expectation_mode);
        setFrequency(requestedExpectation.cadence?.frequency ?? 'monthly');
        setExpectedDay(String(requestedExpectation.cadence?.expected_day ?? 28));
        setGraceDays(String(requestedExpectation.grace_period_days));
        setDueStart(requestedExpectation.calendar_rule?.due_start ?? '');
        setDueEnd(requestedExpectation.calendar_rule?.due_end ?? '');
        setImportance(requestedExpectation.importance);
        setTitleTemplate(requestedExpectation.title_convention?.template ?? '{correspondent} - {period}');
        setAcquisitionSourceId(requestedExpectation.acquisition_source_id ?? '');
        setAcquisitionNotes(requestedExpectation.acquisition_notes ?? '');
        setTyrionSignalId(
          signalData.find((signal) => signal.relationship?.expectation_ids.includes(requestedExpectation.id))?.id ?? '',
        );
      } else if (requestedExpectationId) {
        setError('The requested expectation does not belong to this document source.');
      }
    }).catch((loadError) => setError(errorMessage(loadError))).finally(() => setLoading(false));
  }, [requestedExpectationId, requestedSeriesId, sourceId]);

  const selectedSeries = series.find((item) => item.id === seriesId);
  const monitored = mode === 'recurring' || mode === 'periodic';
  const deadlineMonitored = mode === 'one_off';
  const existingExpectation = expectations.find((item) => item.id === requestedExpectationId)
    ?? (seriesId ? expectations.find((item) => item.series_id === seriesId) : undefined);
  const selectedAcquisition = acquisitionSources.find((item) => item.id === acquisitionSourceId);
  const summary = useMemo(() => ({
    expectation: mode === 'recurring' || mode === 'periodic'
      ? `One ${humanize(kind).toLowerCase()} every ${frequency === 'monthly' ? 'month' : frequency === 'quarterly' ? 'quarter' : 'year'}`
      : mode === 'one_off' ? 'One known occurrence' : mode === 'irregular' ? 'Recognize this irregular series without absence alerts' : 'No document expected',
    monitoring: monitored
      ? `Presence monitoring after day ${expectedDay}, with a ${graceDays}-day grace period`
      : deadlineMonitored
        ? `Deadline monitoring from ${dueStart || 'an unset start date'} through ${dueEnd || 'an unset due date'}`
        : 'No missing-document monitoring',
    filing: `${humanize(kind)} · titles follow “${titleTemplate}”`,
    acquisition: selectedAcquisition ? `${humanize(selectedAcquisition.channel)} · ${humanize(selectedAcquisition.delivery_mode)}` : acquisitionNotes || 'No acquisition guidance recorded',
  }), [acquisitionNotes, deadlineMonitored, dueEnd, dueStart, expectedDay, frequency, graceDays, kind, mode, monitored, selectedAcquisition, titleTemplate]);

  const canContinue = step !== 0 || Boolean(name.trim());

  const save = async (status: 'suggested' | 'confirmed') => {
    if (!source || !name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const policy = {
        name: name.trim(),
        series_id: selectedSeries?.id ?? null,
        expectation_mode: mode,
        status,
        cadence: monitored ? {
          frequency,
          expected_day: Number(expectedDay),
          availability_delay_days: 0,
          grace_period_days: Number(graceDays),
        } : null,
        importance,
        monitoring_mode: monitored ? 'presence' : deadlineMonitored ? 'deadline' : 'none',
        calendar_rule: deadlineMonitored ? {
          rule_type: 'one_off',
          occurrence_id: `${selectedSeries?.id ?? `source-${source.correspondent_id}`}-${dueEnd}`,
          due_start: dueStart,
          due_end: dueEnd,
          occurrence_status: 'pending',
        } : null,
        availability_window: monitored ? { earliest_days: 0, latest_days: 3 } : null,
        grace_period_days: monitored || deadlineMonitored ? Number(graceDays) : 0,
        escalation_policy: {
          reminder_days_before_due: importance === 'routine' ? [] : [1],
          overdue_reminder_interval_days: importance === 'critical' ? 3 : null,
          max_overdue_reminders: importance === 'critical' ? 3 : 0,
        },
        fulfillment_rule: {
          match_series: Boolean(selectedSeries),
          match_kind: true,
          match_calendar_period: monitored || deadlineMonitored,
          required_tag_ids: [],
          required_document_type_id: null,
        },
        evidence: {
          source: 'user',
          reason_codes: [selectedSeries ? 'user_confirmed_series' : 'user_defined_without_series'],
          confidence: null,
          sample_size: selectedSeries?.document_ids.length ?? 0,
          observed_from: null,
          observed_to: null,
        },
        title_convention: titleTemplate.trim() ? {
          template: titleTemplate.trim(),
          date_basis: 'period',
          example: titleTemplate.replace('{correspondent}', source.current_name).replace('{period}', '2026-09').replace('{kind}', humanize(kind)),
        } : null,
        metadata_policy: {
          all_of: [],
          any_of: [],
          none_of: [],
          required_document_type_id: null,
        },
        acquisition_source_id: acquisitionSourceId || null,
        acquisition_notes: acquisitionNotes.trim() || null,
      };
      const body = {
        source_correspondent_id: source.correspondent_id,
        kind,
        ...policy,
      };
      const saved = existingExpectation
        ? await endpoints.documentSources.updateExpectation(existingExpectation.id, policy) as DocumentExpectation
        : await endpoints.documentSources.createExpectation(body) as DocumentExpectation;
      if (tyrionSignalId) {
        const signal = signals.find((item) => item.id === tyrionSignalId);
        await endpoints.documentSources.setExternalSignalRelationship(tyrionSignalId, {
          source_correspondent_id: source.correspondent_id,
          expectation_ids: Array.from(new Set([...(signal?.relationship?.expectation_ids ?? []), saved.id])),
          resolution: 'linked',
          safe_label: signal?.display_hint ?? null,
          revisit_date: null,
        });
      }
      if (status === 'confirmed') {
        await endpoints.documentSources.setDisposition(source.correspondent_id, {
          disposition: 'expectations_configured',
          waiting_revisit_date: null,
          aliases: source.aliases,
          notes: source.notes ?? null,
        });
      }
      navigate(`/sources/${source.correspondent_id}`, { replace: true });
    } catch (saveError) {
      setError(errorMessage(saveError));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="sources-page-state" role="status"><LoaderCircle className="spin" /><h1>Loading expectation policy…</h1></div>;
  }
  if (!source) {
    return <div className="sources-page-state" role="alert"><h1>Expectation Setup could not load</h1><p>{error}</p><Button onClick={() => navigate('/sources')}>Back to Document Sources</Button></div>;
  }

  return (
    <div className="expectation-setup">
      <aside className="expectation-steps">
        <div>
          <button type="button" onClick={() => navigate(`/sources/${source.correspondent_id}`)}><ArrowLeft /> Source evidence</button>
          <h1>{existingExpectation ? 'Edit expectation' : 'Configure expectation'}</h1>
          <p>{source.current_name}{selectedSeries ? ` · ${selectedSeries.name}` : ''}</p>
        </div>
        <ol>
          {STEPS.map((label, index) => (
            <li className={index < step ? 'complete' : index === step ? 'active' : ''} key={label}>
              <button type="button" onClick={() => setStep(index as Step)}>
                <span>{index < step ? <Check /> : index + 1}</span>{label}
              </button>
            </li>
          ))}
        </ol>
      </aside>
      <main className="expectation-form">
        <header>
          <span>Step {step + 1} of {STEPS.length}</span>
          <h2>{STEPS[step]}</h2>
          <p>{[
            'Name the expected document. A reviewed series can provide evidence now or be attached later.',
            'Decide whether another document should arrive. Irregular policy can still recognize and file documents without monitoring absence.',
            'Define the schedule only when absence has real meaning.',
            'Describe how OWL recognizes and files matching documents in Paperless.',
            'Record how the document can be obtained and optionally scope a reviewed Tyrion relationship.',
          ][step]}</p>
        </header>
        {error ? <div className="expectation-error" role="alert">{error}</div> : null}
        <section className="expectation-step-panel">
          {step === 0 ? (
            <>
              <label>Reviewed series <span>Optional</span><select value={seriesId} onChange={(event) => {
                setSeriesId(event.target.value);
                const next = series.find((item) => item.id === event.target.value);
                if (next) { setName(next.name); setKind(next.kind); }
              }}>
                <option value="">No reviewed series yet</option>
                {series.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.document_ids.length} documents</option>)}
              </select></label>
              {series.length === 0 ? <div className="expectation-note">Define the expectation now. OWL can monitor its schedule immediately and a reviewed series can be attached when documents arrive or are identified.</div> : null}
              <label>Expectation name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Harbor Bank checking statement" /></label>
              <label>Document kind<select value={kind} disabled={Boolean(existingExpectation)} onChange={(event) => setKind(event.target.value as DocumentKind)}>
                {['statement', 'invoice', 'bill', 'receipt', 'tax_document', 'renewal', 'record', 'other'].map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
              </select></label>
              {selectedSeries ? <div className="expectation-evidence"><FileCheck2 /><span><strong>{selectedSeries.document_ids.length} reviewed documents</strong><small>Membership revision {selectedSeries.membership_revision}; saving policy will not rewrite membership.</small></span></div> : null}
              {!selectedSeries ? <div className="expectation-evidence"><ShieldCheck /><span><strong>Source-defined expectation</strong><small>No document evidence is required. Fulfillment can be assigned manually until OWL identifies a reviewed series.</small></span></div> : null}
            </>
          ) : null}
          {step === 1 ? (
            <div className="expectation-choice-grid">
              {[
                ['recurring', 'Recurring', 'A regular series where missing periods matter.'],
                ['periodic', 'Periodic', 'A quarterly or annual calendar-specific series.'],
                ['one_off', 'One known occurrence', 'A single required document with a due window.'],
                ['irregular', 'Irregular', 'Recognize and file it; never alert on absence.'],
                ['not_expected', 'Not expected', 'Resolve this specific candidate without source-wide policy.'],
              ].map(([value, label, description]) => (
                <button type="button" className={mode === value ? 'active' : ''} key={value} onClick={() => setMode(value as ExpectationMode)}>
                  <span>{mode === value ? <Check /> : null}</span><strong>{label}</strong><small>{description}</small>
                </button>
              ))}
            </div>
          ) : null}
          {step === 2 ? monitored ? (
            <>
              <div className="expectation-choice-row">
                {(['monthly', 'quarterly', 'annual'] as const).map((value) => (
                  <button type="button" className={frequency === value ? 'active' : ''} key={value} onClick={() => setFrequency(value)}>{humanize(value)}</button>
                ))}
              </div>
              <div className="expectation-field-grid">
                <label>Expected near day<input type="number" min="1" max="31" value={expectedDay} onChange={(event) => setExpectedDay(event.target.value)} /></label>
                <label>Grace period<input type="number" min="0" max="90" value={graceDays} onChange={(event) => setGraceDays(event.target.value)} /></label>
                <label>Importance<select value={importance} onChange={(event) => setImportance(event.target.value as typeof importance)}>
                  <option value="routine">Routine</option><option value="important">Important</option><option value="critical">Critical</option>
                </select></label>
              </div>
              <details>
                <summary>Advanced monitoring behavior <ChevronDown /></summary>
                <p>OWL will require {selectedSeries ? 'series, ' : ''}kind, and calendar-period agreement before a document fulfills an occurrence. Coverage eligibility still requires this source to remain active and disposed as expectations configured.</p>
              </details>
            </>
          ) : deadlineMonitored ? (
            <>
              <div className="expectation-field-grid">
                <label>Due window starts<input type="date" value={dueStart} onChange={(event) => setDueStart(event.target.value)} /></label>
                <label>Due window ends<input type="date" value={dueEnd} onChange={(event) => setDueEnd(event.target.value)} /></label>
                <label>Grace period<input type="number" min="0" max="90" value={graceDays} onChange={(event) => setGraceDays(event.target.value)} /></label>
              </div>
              <label>Importance<select value={importance} onChange={(event) => setImportance(event.target.value as typeof importance)}>
                <option value="routine">Routine</option><option value="important">Important</option><option value="critical">Critical</option>
              </select></label>
            </>
          ) : <div className="expectation-note"><ShieldCheck /> {humanize(mode)} policy does not create missing-document occurrences. Recognition and filing policy remain available.</div> : null}
          {step === 3 ? (
            <>
              <label>Paperless title convention<input value={titleTemplate} onChange={(event) => setTitleTemplate(event.target.value)} /></label>
              <div className="expectation-note">Allowed fields include <code>{'{correspondent}'}</code>, <code>{'{period}'}</code>, and <code>{'{kind}'}</code>. Tag and document-type IDs remain optional. {selectedSeries ? 'The reviewed series is also required.' : 'Until a series is attached, source and document kind provide the recognition boundary.'}</div>
              <div className="expectation-evidence"><FileCheck2 /><span><strong>Recognition policy</strong><small>Match {selectedSeries ? 'the reviewed series, ' : `${source.current_name}, `}{humanize(kind).toLowerCase()} kind, and {monitored ? 'calendar period' : selectedSeries ? 'series identity' : 'source identity'}.</small></span></div>
            </>
          ) : null}
          {step === 4 ? (
            <>
              <label>Acquisition source<select value={acquisitionSourceId} onChange={(event) => setAcquisitionSourceId(event.target.value)}>
                <option value="">No structured acquisition source</option>
                {acquisitionSources.map((item) => <option key={item.id} value={item.id}>{humanize(item.channel)} · {humanize(item.delivery_mode)}</option>)}
              </select></label>
              <label>Acquisition guidance<textarea rows={4} value={acquisitionNotes} onChange={(event) => setAcquisitionNotes(event.target.value)} placeholder="Sign in to the provider portal and download the monthly statement." /></label>
              <label>Specific Tyrion relationship <span>Optional</span><select value={tyrionSignalId} onChange={(event) => setTyrionSignalId(event.target.value)}>
                <option value="">No expectation-scoped Tyrion relationship</option>
                {signals.map((signal) => <option key={signal.id} value={signal.id}>{signal.display_hint}</option>)}
              </select></label>
              <div className="expectation-note"><LinkInvariant /> A source-level Tyrion link does not choose this expectation automatically. Selecting one here updates only the reviewed relationship after the expectation exists.</div>
            </>
          ) : null}
        </section>
        <footer className="expectation-actions">
          <Button disabled={step === 0} onClick={() => setStep((step - 1) as Step)}><ArrowLeft /> Previous</Button>
          <div>
            {existingExpectation?.status !== 'confirmed' ? <Button disabled={saving || !name.trim()} onClick={() => void save('suggested')}>Save draft</Button> : null}
            {step < 4 ? <Button variant="primary" disabled={!canContinue} onClick={() => setStep((step + 1) as Step)}>Continue <ArrowRight /></Button>
              : <Button variant="primary" disabled={saving || !name.trim() || (deadlineMonitored && (!dueStart || !dueEnd))} onClick={() => void save('confirmed')}>{saving ? <LoaderCircle className="spin" /> : <Check />} Confirm policy</Button>}
          </div>
        </footer>
      </main>
      <aside className="expectation-summary" aria-label="Expectation policy summary">
        <h3>Policy summary</h3>
        <p>This is the durable result OWL will store.</p>
        <article>
          <header><strong>{name || 'Unnamed expectation'}</strong><span>{source.current_name} · {humanize(kind)}</span></header>
          <dl>
            <div><dt>Expectation</dt><dd>{summary.expectation}</dd></div>
            <div><dt>Monitoring</dt><dd>{summary.monitoring}</dd></div>
            <div><dt>Importance</dt><dd>{humanize(importance)}</dd></div>
            <div><dt>Recognition</dt><dd>{selectedSeries ? `${selectedSeries.name} and ` : `${source.current_name} and `}{humanize(kind).toLowerCase()} kind</dd></div>
            <div><dt>Filing</dt><dd>{summary.filing}</dd></div>
            <div><dt>Acquisition</dt><dd>{summary.acquisition}</dd></div>
            <div><dt>Tyrion context</dt><dd>{tyrionSignalId ? signals.find((item) => item.id === tyrionSignalId)?.display_hint : 'No expectation-scoped relationship'}</dd></div>
          </dl>
        </article>
        <div className="expectation-summary-warning"><strong>Not active until confirmed.</strong> Coverage evaluates only confirmed monitored expectations on an active source disposed as expectations configured.</div>
      </aside>
    </div>
  );
}

function LinkInvariant() {
  return <ShieldCheck aria-hidden="true" />;
}
