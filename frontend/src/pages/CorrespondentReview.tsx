import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Badge, Breadcrumb, Button, Card, ConfidenceBadge, EmptyState, ErrorState, PageHeader, SkeletonLoader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import {
  paperlessDocumentUrl,
  parseTagIds,
  type AcquisitionSource,
  type AnalysisExpectationMode,
  type CorrespondentPolicyAnalysis,
  type CorrespondentProfile,
  type DocumentExpectation,
  type ExpectationMode,
  type SeriesPolicySuggestion,
} from '../lib/correspondentPolicy';
import { getToastDuration } from '../lib/toast';
import '../styles/correspondents.css';

const MODES: { value: ExpectationMode; label: string }[] = [
  { value: 'recurring', label: 'Recurring' },
  { value: 'periodic', label: 'Periodic' },
  { value: 'one_off', label: 'One-off' },
  { value: 'irregular', label: 'Irregular (validate when received)' },
  { value: 'not_expected', label: 'Not expected' },
];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unable to load correspondent review.';
}

function pct(value: number | null): number {
  return Math.round((value ?? 0) * 100);
}

function ids(value: number[]): string {
  return value.join(', ');
}

interface ExpectationDraft {
  mode: AnalysisExpectationMode | '';
  seriesId: string;
  discriminator: string;
  frequency: 'monthly' | 'quarterly' | 'annual';
  expectedDay: string;
  delayDays: string;
  graceDays: string;
  useTitle: boolean;
  titleTemplate: string;
  titleBasis: 'period' | 'document_date';
  titleExample: string;
  allOf: string;
  anyOf: string;
  noneOf: string;
  documentTypeId: string;
  acquisitionSourceId: string;
  selectedFamily: string;
}

function initialDraft(suggestion: SeriesPolicySuggestion, existing?: DocumentExpectation): ExpectationDraft {
  const policy = existing?.metadata_policy ?? suggestion.metadata.policy;
  const title = existing?.title_convention ?? suggestion.title.convention;
  const cadence = existing?.cadence ?? suggestion.cadence;
  const matchingFamily = suggestion.metadata.required_tag_families.find(
    (family) =>
      family.child_tag_ids.length === policy.any_of.length &&
      family.child_tag_ids.every((tagId) => policy.any_of.includes(tagId)),
  );
  return {
    mode: existing?.expectation_mode ?? (suggestion.expectation_mode === 'unknown' ? '' : suggestion.expectation_mode),
    seriesId: existing?.statement_series_id ?? suggestion.statement_series_id ?? '',
    discriminator: existing?.series_discriminator ?? suggestion.series_discriminator ?? '',
    frequency: cadence?.frequency ?? 'monthly',
    expectedDay: cadence?.expected_day ? String(cadence.expected_day) : '',
    delayDays: String(cadence?.availability_delay_days ?? 0),
    graceDays: String(cadence?.grace_period_days ?? 5),
    useTitle: title !== null,
    titleTemplate: title?.template ?? '',
    titleBasis: title?.date_basis ?? 'period',
    titleExample: title?.example ?? '',
    allOf: ids(policy.all_of),
    anyOf: ids(policy.any_of),
    noneOf: ids(policy.none_of),
    documentTypeId: policy.required_document_type_id ? String(policy.required_document_type_id) : '',
    acquisitionSourceId: existing?.acquisition_source_id ?? '',
    selectedFamily: matchingFamily?.family ?? '',
  };
}

function ExpectationReviewCard({
  suggestion,
  index,
  correspondentId,
  existing,
  acquisitions,
  paperlessUrl,
  onSaved,
}: {
  suggestion: SeriesPolicySuggestion;
  index: number;
  correspondentId: number;
  existing?: DocumentExpectation;
  acquisitions: AcquisitionSource[];
  paperlessUrl: string | null;
  onSaved: (message: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(() => initialDraft(suggestion, existing));
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const seriesOptions = Array.from(
    new Set([suggestion.statement_series_id, suggestion.source_statement_series_id, existing?.statement_series_id].filter(Boolean)),
  ) as string[];
  const needsCadence = draft.mode === 'recurring' || draft.mode === 'periodic';
  const selectedFamily = suggestion.metadata.required_tag_families.find((family) => family.family === draft.selectedFamily);
  const modeValid = draft.mode !== '' && draft.mode !== 'unknown';
  const bindingValid = suggestion.kind !== 'statement' || Boolean(draft.seriesId);

  const buildPayload = (status: 'confirmed' | 'dismissed') => {
    if (!modeValid) throw new Error('Choose a persistable expectation mode first.');
    if (!bindingValid) throw new Error('Statement expectations must bind to an existing statement series.');
    const metadataAnyOf = selectedFamily ? selectedFamily.child_tag_ids : parseTagIds(draft.anyOf);
    return {
      kind: suggestion.kind,
      document_type_id: draft.documentTypeId ? Number(draft.documentTypeId) : null,
      statement_series_id: draft.seriesId || null,
      series_discriminator: draft.discriminator || null,
      expectation_mode: draft.mode,
      status,
      cadence: needsCadence
        ? {
            frequency: draft.frequency,
            expected_day: draft.expectedDay ? Number(draft.expectedDay) : null,
            availability_delay_days: Number(draft.delayDays || 0),
            grace_period_days: Number(draft.graceDays || 0),
          }
        : null,
      evidence: {
        ...suggestion.evidence,
        source: 'user',
        reason_codes: ['user_confirmed'],
      },
      title_convention:
        draft.useTitle && draft.titleTemplate
          ? {
              template: draft.titleTemplate,
              date_basis: draft.titleBasis,
              example: draft.titleExample || suggestion.title.examples[0]?.after || 'Reviewed title',
            }
          : null,
      metadata_policy: {
        all_of: parseTagIds(draft.allOf),
        any_of: metadataAnyOf,
        none_of: parseTagIds(draft.noneOf),
        required_document_type_id: draft.documentTypeId ? Number(draft.documentTypeId) : null,
      },
      acquisition_source_id: draft.acquisitionSourceId || null,
    };
  };

  const persist = async (status: 'confirmed' | 'dismissed') => {
    setLocalError(null);
    setSaving(true);
    try {
      if (status === 'dismissed') {
        if (existing) {
          await endpoints.statements.updateExpectation(existing.id, { status: 'dismissed' });
        } else {
          await endpoints.statements.correspondentProfiles.dismissSuggestion(correspondentId, {
            statement_series_id: suggestion.statement_series_id,
            source_statement_series_id: suggestion.source_statement_series_id,
            series_discriminator: suggestion.series_discriminator,
            kind: suggestion.kind,
          });
        }
        await onSaved('Suggestion dismissed.');
        return;
      }
      const payload = buildPayload(status);
      if (existing) {
        const { kind: _kind, ...update } = payload;
        await endpoints.statements.updateExpectation(existing.id, update);
      } else {
        await endpoints.statements.correspondentProfiles.createExpectation(
          correspondentId,
          payload,
        );
      }
      await onSaved('Expectation confirmed.');
    } catch (error) {
      setLocalError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <article className="expectation-card" aria-labelledby={`expectation-${index}`}>
      <div className="expectation-card-header">
        <div>
          <div className="expectation-eyebrow">
            {suggestion.candidate_series ? 'Candidate series' : existing ? 'Existing expectation' : 'Observed series'}
          </div>
          <h3 id={`expectation-${index}`}>
            {suggestion.series_discriminator || suggestion.statement_series_id || `${suggestion.kind} suggestion ${index + 1}`}
          </h3>
          <div className="correspondent-reasons">
            <Badge tone="info">{suggestion.kind}</Badge>
            <Badge tone={suggestion.expectation_mode === 'unknown' ? 'warning' : 'ok'}>
              {suggestion.expectation_mode}
            </Badge>
            {existing ? <Badge tone={existing.status === 'confirmed' ? 'ok' : 'muted'}>{existing.status}</Badge> : null}
          </div>
        </div>
        <ConfidenceBadge pct={pct(suggestion.evidence.confidence)} />
      </div>

      <div className="expectation-evidence-grid">
        <div>
          <span>Sample</span>
          <strong>{suggestion.evidence.sample_size} documents</strong>
        </div>
        <div>
          <span>Observed</span>
          <strong>{suggestion.evidence.observed_from || '—'} to {suggestion.evidence.observed_to || '—'}</strong>
        </div>
        <div>
          <span>Cadence evidence</span>
          <strong>{suggestion.cadence ? `${suggestion.cadence.frequency}, day ${suggestion.cadence.expected_day ?? 'variable'}` : 'Insufficient evidence'}</strong>
        </div>
        <div>
          <span>Acquisition evidence</span>
          <strong>{suggestion.acquisition.channel} ({pct(suggestion.acquisition.confidence)}%)</strong>
        </div>
      </div>

      <section className="expectation-section">
        <h4>Representative Paperless documents</h4>
        <div className="representative-documents">
          {suggestion.document_ids.slice(0, 3).map((documentId) => {
            const href = paperlessDocumentUrl(paperlessUrl, documentId);
            return href ? (
              <a key={documentId} href={href} target="_blank" rel="noreferrer">
                Document {documentId} ↗
              </a>
            ) : (
              <span key={documentId}>Document {documentId}</span>
            );
          })}
        </div>
      </section>

      <section className="expectation-section">
        <div className="expectation-section-heading">
          <h4>Title convention</h4>
          <div className="correspondent-reasons">
            <Badge tone={suggestion.title.coverage >= 0.8 ? 'ok' : 'warning'}>{pct(suggestion.title.coverage)}% coverage</Badge>
            <ConfidenceBadge pct={pct(suggestion.title.confidence)} />
          </div>
        </div>
        <p className="text-muted">
          {suggestion.title.exception_document_ids.length} exceptions; {suggestion.title.missing_required_fields.length} documents with missing fields.
        </p>
        {suggestion.title.examples.length > 0 ? (
          <div className="title-render-list">
            {suggestion.title.examples.map((example) => (
              <div key={example.document_id} className="title-render">
                <div><span>Before</span><code>{example.before}</code></div>
                <div><span>After</span><code>{example.after}</code></div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-muted">No deterministic render is available until required fields are present.</p>
        )}
        {suggestion.title.missing_required_fields.length > 0 ? (
          <ul className="finding-list">
            {suggestion.title.missing_required_fields.map((finding) => (
              <li key={finding.document_id}>Document {finding.document_id}: missing {finding.missing_fields.join(', ')}</li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="expectation-section">
        <div className="expectation-section-heading">
          <h4>Metadata policy suggestion</h4>
          <ConfidenceBadge pct={pct(suggestion.metadata.confidence)} />
        </div>
        <div className="metadata-policy-summary">
          <span>All tags: {ids(suggestion.metadata.policy.all_of) || 'none'}</span>
          <span>Any tag: {ids(suggestion.metadata.policy.any_of) || 'none'}</span>
          <span>Forbidden: {ids(suggestion.metadata.policy.none_of) || 'none'}</span>
          <span>Document type: {suggestion.metadata.policy.required_document_type_id ?? 'not required'}</span>
        </div>
        {suggestion.metadata.required_tag_families.length > 0 ? (
          <fieldset className="tag-family-fieldset">
            <legend>Required tag-family decision</legend>
            {suggestion.metadata.required_tag_families.length > 1 ? (
              <p className="policy-warning">
                Multiple independent families were detected. The persisted Phase 1 policy supports one <code>any_of</code> family, so choose one or leave all unselected; OWL will not flatten them together.
              </p>
            ) : null}
            {suggestion.metadata.required_tag_families.map((family) => (
              <label key={family.family} className="tag-family-option">
                <input
                  type="radio"
                  name={`family-${index}`}
                  checked={draft.selectedFamily === family.family}
                  onChange={() => setDraft((current) => ({ ...current, selectedFamily: family.family }))}
                />
                <span>
                  <strong>{family.family}</strong> ({pct(family.coverage)}% coverage)
                  <small>{family.child_tag_names.join(' · ')}</small>
                </span>
              </label>
            ))}
            <label className="tag-family-option">
              <input
                type="radio"
                name={`family-${index}`}
                checked={draft.selectedFamily === ''}
                onChange={() => setDraft((current) => ({ ...current, selectedFamily: '' }))}
              />
              <span><strong>Do not persist a family requirement</strong></span>
            </label>
          </fieldset>
        ) : null}
      </section>

      <section className="expectation-section expectation-config">
        <h4>Reviewed policy</h4>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor={`mode-${index}`}>Expectation mode</label>
            <select
              id={`mode-${index}`}
              value={draft.mode}
              onChange={(event) => setDraft((current) => ({ ...current, mode: event.target.value as AnalysisExpectationMode }))}
            >
              <option value="">Choose a mode…</option>
              {MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
            </select>
            {suggestion.expectation_mode === 'unknown' ? <small>Analysis returned unknown; choose a persistable mode before confirmation.</small> : null}
          </div>
          <div className="form-group">
            <label htmlFor={`series-${index}`}>Statement series binding</label>
            <select
              id={`series-${index}`}
              value={draft.seriesId}
              onChange={(event) => setDraft((current) => ({ ...current, seriesId: event.target.value }))}
            >
              <option value="">No existing series</option>
              {seriesOptions.map((seriesId) => <option key={seriesId} value={seriesId}>{seriesId}</option>)}
            </select>
            {suggestion.source_statement_series_id ? <small>Candidate derived from {suggestion.source_statement_series_id}.</small> : null}
          </div>
        </div>
        <div className="form-row">
          <div className="form-group">
            <label htmlFor={`discriminator-${index}`}>Series discriminator</label>
            <input id={`discriminator-${index}`} value={draft.discriminator} onChange={(event) => setDraft((current) => ({ ...current, discriminator: event.target.value }))} />
          </div>
          <div className="form-group">
            <label htmlFor={`source-${index}`}>Acquisition source</label>
            <select id={`source-${index}`} value={draft.acquisitionSourceId} onChange={(event) => setDraft((current) => ({ ...current, acquisitionSourceId: event.target.value }))}>
              <option value="">Not configured</option>
              {acquisitions.map((source) => <option key={source.id} value={source.id}>{source.channel} · {source.delivery_mode}</option>)}
            </select>
          </div>
        </div>
        {needsCadence ? (
          <div className="form-row correspondent-cadence-row">
            <div className="form-group">
              <label htmlFor={`frequency-${index}`}>Frequency</label>
              <select id={`frequency-${index}`} value={draft.frequency} onChange={(event) => setDraft((current) => ({ ...current, frequency: event.target.value as ExpectationDraft['frequency'] }))}>
                <option value="monthly">Monthly</option><option value="quarterly">Quarterly</option><option value="annual">Annual</option>
              </select>
            </div>
            <div className="form-group"><label htmlFor={`day-${index}`}>Expected day</label><input id={`day-${index}`} type="number" min="1" max="31" value={draft.expectedDay} onChange={(event) => setDraft((current) => ({ ...current, expectedDay: event.target.value }))} /></div>
            <div className="form-group"><label htmlFor={`delay-${index}`}>Availability delay</label><input id={`delay-${index}`} type="number" min="0" value={draft.delayDays} onChange={(event) => setDraft((current) => ({ ...current, delayDays: event.target.value }))} /></div>
            <div className="form-group"><label htmlFor={`grace-${index}`}>Grace period</label><input id={`grace-${index}`} type="number" min="0" value={draft.graceDays} onChange={(event) => setDraft((current) => ({ ...current, graceDays: event.target.value }))} /></div>
          </div>
        ) : null}
        <label className="correspondent-checkbox">
          <input type="checkbox" checked={draft.useTitle} onChange={(event) => setDraft((current) => ({ ...current, useTitle: event.target.checked }))} />
          Persist a title convention
        </label>
        {draft.useTitle ? (
          <div className="form-row">
            <div className="form-group">
              <label htmlFor={`template-${index}`}>Title template</label>
              <input id={`template-${index}`} value={draft.titleTemplate} onChange={(event) => setDraft((current) => ({ ...current, titleTemplate: event.target.value }))} />
            </div>
            <div className="form-group">
              <label htmlFor={`basis-${index}`}>Date basis</label>
              <select id={`basis-${index}`} value={draft.titleBasis} onChange={(event) => setDraft((current) => ({ ...current, titleBasis: event.target.value as ExpectationDraft['titleBasis'] }))}>
                <option value="period">Covered period</option><option value="document_date">Document date</option>
              </select>
            </div>
          </div>
        ) : null}
        <div className="form-row">
          <div className="form-group"><label htmlFor={`all-${index}`}>Required tag IDs (all)</label><input id={`all-${index}`} value={draft.allOf} onChange={(event) => setDraft((current) => ({ ...current, allOf: event.target.value }))} placeholder="1, 2" /></div>
          <div className="form-group"><label htmlFor={`none-${index}`}>Forbidden tag IDs</label><input id={`none-${index}`} value={draft.noneOf} onChange={(event) => setDraft((current) => ({ ...current, noneOf: event.target.value }))} placeholder="9" /></div>
        </div>
        {suggestion.metadata.required_tag_families.length === 0 ? (
          <div className="form-group"><label htmlFor={`any-${index}`}>Required tag IDs (any)</label><input id={`any-${index}`} value={draft.anyOf} onChange={(event) => setDraft((current) => ({ ...current, anyOf: event.target.value }))} placeholder="3, 4" /></div>
        ) : null}
        <div className="form-group"><label htmlFor={`type-${index}`}>Required Paperless document type ID</label><input id={`type-${index}`} type="number" min="1" value={draft.documentTypeId} onChange={(event) => setDraft((current) => ({ ...current, documentTypeId: event.target.value }))} /></div>
        {localError ? <div className="policy-error" role="alert">{localError}</div> : null}
        <div className="btn-group">
          <Button variant="success" disabled={saving || !modeValid || !bindingValid} onClick={() => void persist('confirmed')}>
            {saving ? 'Saving…' : existing?.status === 'confirmed' ? 'Save reviewed policy' : 'Confirm expectation'}
          </Button>
          {existing?.status !== 'retired' ? (
            <Button disabled={saving} onClick={() => void persist('dismissed')}>Dismiss</Button>
          ) : null}
          {existing && existing.status !== 'retired' ? (
            <Button
              variant="danger"
              disabled={saving}
              onClick={async () => {
                setSaving(true);
                try {
                  await endpoints.statements.updateExpectation(existing.id, { status: 'retired' });
                  await onSaved('Expectation retired.');
                } catch (error) {
                  setLocalError(errorMessage(error));
                } finally {
                  setSaving(false);
                }
              }}
            >
              Retire
            </Button>
          ) : null}
          {suggestion.statement_series_id ? <Link className="btn" to={`/statements/${encodeURIComponent(suggestion.statement_series_id)}`}>Open series</Link> : null}
        </div>
      </section>
    </article>
  );
}

export default function CorrespondentReview() {
  const { correspondentId } = useParams();
  const navigate = useNavigate();
  const id = Number(correspondentId);
  const [profile, setProfile] = useState<CorrespondentProfile | null>(null);
  const [analysis, setAnalysis] = useState<CorrespondentPolicyAnalysis | null>(null);
  const [expectations, setExpectations] = useState<DocumentExpectation[]>([]);
  const [acquisitions, setAcquisitions] = useState<AcquisitionSource[]>([]);
  const [allProfiles, setAllProfiles] = useState<CorrespondentProfile[]>([]);
  const [paperlessUrl, setPaperlessUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [relinkTarget, setRelinkTarget] = useState('');
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);
  const [sourceForm, setSourceForm] = useState({ channel: 'portal_manual', delivery_mode: 'pull', portal_url: '', instructions: '' });

  const load = useCallback(async () => {
    if (!Number.isInteger(id) || id <= 0) {
      setError('Invalid correspondent ID.');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [nextProfile, nextAnalysis, nextExpectations, nextSources, inventory, config] = await Promise.all([
        endpoints.statements.correspondentProfiles.get(id),
        endpoints.statements.correspondentProfiles.analysis(id),
        endpoints.statements.correspondentProfiles.expectations(id),
        endpoints.statements.acquisitionSources.list(),
        endpoints.statements.correspondentProfiles.inventory(),
        endpoints.statements.paperlessUrl(),
      ]) as [
        CorrespondentProfile,
        CorrespondentPolicyAnalysis,
        DocumentExpectation[],
        AcquisitionSource[],
        { profile: CorrespondentProfile }[],
        { paperless_url: string | null },
      ];
      setProfile(nextProfile);
      setAnalysis(nextAnalysis);
      setExpectations(nextExpectations);
      setAcquisitions(nextSources);
      setAllProfiles(inventory.map((item) => item.profile));
      setPaperlessUrl(config.paperless_url);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const expectationById = useMemo(
    () => new Map(expectations.map((expectation) => [expectation.id, expectation])),
    [expectations],
  );
  const unrepresentedExpectations = useMemo(
    () => expectations.filter((expectation) => !analysis?.suggestions.some((suggestion) => suggestion.existing_expectation_id === expectation.id)),
    [analysis, expectations],
  );

  const saved = async (message: string) => {
    await load();
    setToast({ message, tone: 'success' });
  };

  const updateProfile = async (body: unknown, message: string) => {
    setBusy(true);
    try {
      await endpoints.statements.correspondentProfiles.update(id, body);
      await saved(message);
    } catch (error) {
      setToast({ message: errorMessage(error), tone: 'error' });
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <SkeletonLoader variant="cards" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!profile || !analysis) return <EmptyState title="Correspondent review unavailable" />;

  return (
    <>
      <Breadcrumb items={[{ label: 'Correspondent review', to: '/correspondents' }, { label: profile.current_name }]} />
      <PageHeader
        title={profile.current_name}
        desc={`${analysis.document_count} Paperless documents across ${analysis.suggestions.length} independently reviewable series or expectations.`}
        actions={<Button onClick={() => navigate('/correspondents')}>Back to inventory</Button>}
      />

      <div className="profile-action-bar section">
        <div className="correspondent-reasons">
          <Badge tone={profile.review_status === 'reviewed' ? 'ok' : profile.review_status === 'ignored' ? 'muted' : 'warning'}>{profile.review_status}</Badge>
          <Badge tone={profile.lifecycle_status === 'active' ? 'ok' : 'danger'}>{profile.lifecycle_status}</Badge>
          {analysis.reason_codes.map((reason) => <Badge key={reason} tone="info">{reason.replaceAll('_', ' ')}</Badge>)}
        </div>
        <div className="btn-group">
          <Button
            variant="success"
            disabled={busy}
            onClick={() => void updateProfile({
              review_status: 'reviewed',
              last_analyzed_at: new Date().toISOString(),
              last_reviewed_at: new Date().toISOString(),
              observed_summary: {
                ...profile.observed_summary,
                document_count: analysis.document_count,
                candidate_series_count: analysis.suggestions.filter((item) => item.candidate_series).length,
              },
            }, 'Profile marked reviewed.')}
          >
            Mark reviewed
          </Button>
          <Button disabled={busy} onClick={() => void updateProfile({ review_status: 'ignored', last_reviewed_at: new Date().toISOString() }, 'Profile ignored.')}>Ignore</Button>
          {profile.lifecycle_status !== 'retired' ? <Button variant="danger" disabled={busy} onClick={() => void updateProfile({ lifecycle_status: 'retired' }, 'Profile retired.')}>Retire profile</Button> : null}
        </div>
      </div>

      {profile.lifecycle_status === 'orphaned' ? (
        <Card title="Relink orphaned profile" className="section">
          <p className="text-muted">Move this reviewed OWL policy to an active Paperless correspondent identity.</p>
          <div className="profile-relink">
            <select aria-label="Relink target" value={relinkTarget} onChange={(event) => setRelinkTarget(event.target.value)}>
              <option value="">Choose a current correspondent…</option>
              {allProfiles.filter((item) => item.lifecycle_status === 'active' && item.correspondent_id !== id).map((item) => (
                <option key={item.correspondent_id} value={item.correspondent_id}>{item.current_name} (ID {item.correspondent_id})</option>
              ))}
            </select>
            <Button
              variant="primary"
              disabled={busy || !relinkTarget}
              onClick={async () => {
                setBusy(true);
                try {
                  const relinked = await endpoints.statements.correspondentProfiles.relink(id, Number(relinkTarget)) as CorrespondentProfile;
                  setToast({ message: 'Profile relinked.', tone: 'success' });
                  navigate(`/correspondents/${relinked.correspondent_id}`, { replace: true });
                } catch (error) {
                  setToast({ message: errorMessage(error), tone: 'error' });
                } finally {
                  setBusy(false);
                }
              }}
            >
              Relink profile
            </Button>
          </div>
        </Card>
      ) : null}

      <Card title="Acquisition sources" className="section">
        <p className="text-muted">Store safe retrieval guidance only. OWL does not collect credentials or execute acquisition.</p>
        {acquisitions.length > 0 ? (
          <div className="acquisition-source-list">
            {acquisitions.map((source) => (
              <div key={source.id}>
                <strong>{source.channel} · {source.delivery_mode}</strong>
                <span>{source.instructions || 'No instructions'} {source.portal_url ? <a href={source.portal_url} target="_blank" rel="noreferrer">Open safe portal ↗</a> : null}</span>
              </div>
            ))}
          </div>
        ) : <p className="text-muted">No acquisition sources configured.</p>}
        <div className="form-row">
          <div className="form-group">
            <label htmlFor="acquisition-channel">Channel</label>
            <select id="acquisition-channel" value={sourceForm.channel} onChange={(event) => setSourceForm((current) => ({ ...current, channel: event.target.value }))}>
              <option value="paperless_mail">Paperless mail</option><option value="email_manual">Manual email</option><option value="direct_api">Direct API</option><option value="portal_manual">Manual portal</option><option value="snail_mail">Snail mail</option><option value="linked_storage">Linked storage</option>
            </select>
          </div>
          <div className="form-group">
            <label htmlFor="delivery-mode">Delivery mode</label>
            <select id="delivery-mode" value={sourceForm.delivery_mode} onChange={(event) => setSourceForm((current) => ({ ...current, delivery_mode: event.target.value }))}>
              <option value="push">Push</option><option value="pull">Pull</option><option value="physical">Physical</option>
            </select>
          </div>
        </div>
        <div className="form-row">
          <div className="form-group"><label htmlFor="portal-url">Safe portal URL</label><input id="portal-url" type="url" value={sourceForm.portal_url} onChange={(event) => setSourceForm((current) => ({ ...current, portal_url: event.target.value }))} placeholder="https://provider.example/statements" /></div>
          <div className="form-group"><label htmlFor="instructions">Retrieval instructions</label><input id="instructions" value={sourceForm.instructions} onChange={(event) => setSourceForm((current) => ({ ...current, instructions: event.target.value }))} /></div>
        </div>
        <Button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await endpoints.statements.acquisitionSources.create({
                ...sourceForm,
                portal_url: sourceForm.portal_url || null,
                instructions: sourceForm.instructions || null,
              });
              setSourceForm({ channel: 'portal_manual', delivery_mode: 'pull', portal_url: '', instructions: '' });
              await saved('Acquisition source created.');
            } catch (error) {
              setToast({ message: errorMessage(error), tone: 'error' });
            } finally {
              setBusy(false);
            }
          }}
        >
          Add acquisition source
        </Button>
      </Card>

      <div className="expectation-stack section">
        {analysis.suggestions.map((suggestion, index) => (
          <ExpectationReviewCard
            key={suggestion.existing_expectation_id ?? suggestion.statement_series_id ?? `${suggestion.kind}-${index}`}
            suggestion={suggestion}
            index={index}
            correspondentId={id}
            existing={suggestion.existing_expectation_id ? expectationById.get(suggestion.existing_expectation_id) : undefined}
            acquisitions={acquisitions}
            paperlessUrl={paperlessUrl}
            onSaved={saved}
          />
        ))}
        {analysis.suggestions.length === 0 ? (
          <EmptyState title="No policy suggestions" desc="There is not enough Paperless history to infer a series or expectation." />
        ) : null}
      </div>

      {unrepresentedExpectations.length > 0 ? (
        <Card title="Other persisted expectations" className="section">
          <div className="persisted-expectation-list">
            {unrepresentedExpectations.map((expectation) => (
              <div key={expectation.id}>
                <strong>{expectation.series_discriminator || expectation.kind}</strong>
                <span>{expectation.expectation_mode} · {expectation.status}</span>
                {expectation.statement_series_id ? <Link to={`/statements/${encodeURIComponent(expectation.statement_series_id)}`}>Open series</Link> : null}
              </div>
            ))}
          </div>
        </Card>
      ) : null}
      {analysis.unassigned_document_ids.length > 0 ? (
        <Card title="Unassigned documents" className="section">
          <div className="representative-documents">
            {analysis.unassigned_document_ids.map((documentId) => {
              const href = paperlessDocumentUrl(paperlessUrl, documentId);
              return href ? <a key={documentId} href={href} target="_blank" rel="noreferrer">Document {documentId} ↗</a> : <span key={documentId}>Document {documentId}</span>;
            })}
          </div>
        </Card>
      ) : null}
      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </>
  );
}
