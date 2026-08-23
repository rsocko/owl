import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Badge, Button, Card, ConfidenceBadge, EmptyState, ErrorState, PageHeader, SkeletonLoader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/correspondent-review.css';

type ReviewStatus = 'unreviewed' | 'reviewed' | 'ignored';
type LifecycleStatus = 'active' | 'orphaned' | 'retired';
type ExpectationMode = 'recurring' | 'periodic' | 'one_off' | 'irregular' | 'not_expected';
type ExpectationStatus = 'suggested' | 'confirmed' | 'dismissed' | 'retired';

interface ObservedSummary {
  document_count: number;
  document_type_counts: Record<string, number>;
  title_pattern_count: number;
  tag_family_counts: Record<string, number>;
  candidate_series_count: number;
}

interface CorrespondentProfile {
  correspondent_id: number;
  current_name: string;
  review_status: ReviewStatus;
  lifecycle_status: LifecycleStatus;
  aliases: string[];
  notes?: string | null;
  observed_summary: ObservedSummary;
  last_analyzed_at?: string | null;
  last_reviewed_at?: string | null;
}

interface Cadence {
  frequency: 'monthly' | 'quarterly' | 'annual';
  expected_day?: number | null;
  availability_delay_days: number;
  grace_period_days: number;
}

interface Evidence {
  source: string;
  reason_codes: string[];
  confidence?: number | null;
  sample_size: number;
  observed_from?: string | null;
  observed_to?: string | null;
}

interface TitleConvention {
  template: string;
  date_basis: 'period' | 'document_date';
  example: string;
}

interface MetadataPolicy {
  all_of: number[];
  any_of: number[];
  none_of: number[];
  required_document_type_id?: number | null;
}

interface Suggestion {
  suggestion_key: string;
  kind: string;
  series_discriminator: string;
  statement_series_id?: string | null;
  expectation_mode: ExpectationMode | 'unknown';
  cadence?: Cadence | null;
  evidence: Evidence;
  title: {
    convention?: TitleConvention | null;
    coverage: number;
    exception_document_ids: number[];
    examples: Array<{ document_id: number; before: string; after?: string | null; missing_fields: string[] }>;
    reason_codes: string[];
  };
  metadata: {
    policy: MetadataPolicy;
    tag_names: Record<string, string>;
    required_document_type_name?: string | null;
    confidence?: number | null;
    reason_codes: string[];
  };
  acquisition: {
    channel: string;
    confidence?: number | null;
    reason_codes: string[];
  };
  sample_document_ids: number[];
}

interface AnalysisResult {
  correspondent_id: number;
  correspondent_name: string;
  analyzed_at: string;
  observed_summary: ObservedSummary;
  suggestions: Suggestion[];
}

interface DocumentExpectation {
  id: string;
  correspondent_id: number;
  kind: string;
  statement_series_id?: string | null;
  series_discriminator?: string | null;
  expectation_mode: ExpectationMode;
  status: ExpectationStatus;
  cadence?: Cadence | null;
  evidence: Evidence;
  title_convention?: TitleConvention | null;
  metadata_policy: MetadataPolicy;
  acquisition_source_id?: string | null;
}

interface AcquisitionSource {
  id: string;
  channel: string;
  delivery_mode: string;
  instructions?: string | null;
  portal_url?: string | null;
}

interface ExternalCandidate {
  id: string;
  kind: 'accountStatementCandidate' | 'recurringDocumentCandidate';
  active: boolean;
  display_hint: string;
  confidence: number;
  basis: string[];
  outcome: 'unreviewed' | 'mapped' | 'suggested' | 'ambiguous' | 'not_applicable';
  expectation_id?: string | null;
  correspondent_id?: number | null;
  likely_multiple_statement_series: boolean;
  recurrence_evidence: 'high' | 'none';
  review_finding?: string | null;
}

interface SuggestionDraft {
  mode: ExpectationMode | '';
  seriesDiscriminator: string;
  titleTemplate: string;
  acquisitionSourceId: string;
  allOf: string;
  anyOf: string;
  noneOf: string;
  documentTypeId: string;
}

const MODE_OPTIONS: Array<{ value: ExpectationMode; label: string }> = [
  { value: 'recurring', label: 'Recurring' },
  { value: 'periodic', label: 'Periodic' },
  { value: 'one_off', label: 'One-off' },
  { value: 'irregular', label: 'Irregular' },
  { value: 'not_expected', label: 'Not expected' },
];

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The correspondent workspace could not complete the request.';
}

function formatDate(value?: string | null): string {
  if (!value) return 'Never';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function humanize(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function redactSensitiveNumbers(value: string): string {
  return value.replace(/\b\d{3,}\b/g, (match) => {
    const numeric = Number(match);
    return match.length === 4 && numeric >= 1900 && numeric <= 2099 ? match : '[redacted]';
  });
}

function profilePriority(
  profile: CorrespondentProfile,
  expectationCount: number,
): { rank: number; label: string; tone: 'danger' | 'warning' | 'info' | 'muted' | 'ok' } {
  if (profile.lifecycle_status === 'retired') return { rank: 7, label: 'Retired', tone: 'muted' };
  if (profile.review_status === 'ignored') return { rank: 6, label: 'Ignored', tone: 'muted' };
  if (profile.lifecycle_status === 'orphaned') return { rank: 0, label: 'Needs relink', tone: 'danger' };
  if (profile.review_status === 'unreviewed') return { rank: 1, label: 'Unreviewed', tone: 'warning' };
  if (!profile.last_analyzed_at) return { rank: 2, label: 'Not analyzed', tone: 'warning' };
  if (profile.observed_summary.candidate_series_count > expectationCount) {
    return { rank: 3, label: 'Unmatched candidates', tone: 'info' };
  }
  return { rank: 4, label: 'Reviewed', tone: 'ok' };
}

function parseTagIds(value: string): number[] {
  return Array.from(new Set(
    value
      .split(',')
      .map((part) => Number(part.trim()))
      .filter((tagId) => Number.isInteger(tagId) && tagId > 0),
  )).sort((left, right) => left - right);
}

function isValidTagList(value: string): boolean {
  return value.trim() === '' || value.split(',').every((part) => /^[1-9]\d*$/.test(part.trim()));
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function renderDraftTitle(
  original: TitleConvention,
  draftTemplate: string,
  originalRendered?: string | null,
): { rendered: string | null; missingFields: string[] } {
  if (!originalRendered) return { rendered: null, missingFields: ['example'] };
  const fields: string[] = [];
  let cursor = 0;
  let pattern = '^';
  for (const match of original.template.matchAll(/\{([a-z_]+)\}/g)) {
    pattern += escapeRegex(original.template.slice(cursor, match.index));
    pattern += '(.+?)';
    fields.push(match[1]);
    cursor = (match.index ?? 0) + match[0].length;
  }
  pattern += `${escapeRegex(original.template.slice(cursor))}$`;
  const values = originalRendered.match(new RegExp(pattern));
  if (!values) return { rendered: null, missingFields: ['example'] };
  const byField = Object.fromEntries(fields.map((field, index) => [field, values[index + 1]]));
  const requestedFields = Array.from(draftTemplate.matchAll(/\{([a-z_]+)\}/g), (match) => match[1]);
  const missingFields = requestedFields.filter((field) => !byField[field]);
  if (missingFields.length > 0 || requestedFields.length === 0) {
    return { rendered: null, missingFields: missingFields.length > 0 ? missingFields : ['template_field'] };
  }
  const rendered = draftTemplate.replace(/\{([a-z_]+)\}/g, (_, field: string) => byField[field] ?? '');
  return rendered.length <= 128
    ? { rendered, missingFields: [] }
    : { rendered: null, missingFields: ['title_over_128_characters'] };
}

function buildExpectationBody(
  suggestion: Suggestion,
  draft: SuggestionDraft,
  status: 'confirmed' | 'dismissed',
) {
  const cadence = draft.mode === 'recurring' || draft.mode === 'periodic' ? suggestion.cadence : null;
  const titleExample = suggestion.title.convention
    ? suggestion.title.examples
      .map((example) => renderDraftTitle(suggestion.title.convention!, draft.titleTemplate, example.after))
      .find((preview) => preview.rendered)?.rendered
    : null;
  return {
    kind: suggestion.kind,
    document_type_id: suggestion.metadata.policy.required_document_type_id ?? null,
    statement_series_id: suggestion.statement_series_id ?? null,
    series_discriminator: draft.seriesDiscriminator || null,
    expectation_mode: draft.mode,
    status,
    cadence,
    evidence: {
      ...suggestion.evidence,
      reason_codes: [...suggestion.evidence.reason_codes, `user_${status}`],
    },
    title_convention: suggestion.title.convention
      ? { ...suggestion.title.convention, template: draft.titleTemplate, example: titleExample }
      : null,
    metadata_policy: {
      all_of: parseTagIds(draft.allOf),
      any_of: parseTagIds(draft.anyOf),
      none_of: parseTagIds(draft.noneOf),
      required_document_type_id: draft.documentTypeId ? Number(draft.documentTypeId) : null,
    },
    acquisition_source_id: draft.acquisitionSourceId || null,
  };
}

function SuggestionCard({
  suggestion,
  acquisitionSources,
  paperlessUrl,
  busy,
  onDecision,
}: {
  suggestion: Suggestion;
  acquisitionSources: AcquisitionSource[];
  paperlessUrl: string;
  busy: boolean;
  onDecision: (suggestion: Suggestion, draft: SuggestionDraft, status: 'confirmed' | 'dismissed') => void;
}) {
  const [draft, setDraft] = useState<SuggestionDraft>({
    mode: suggestion.expectation_mode === 'unknown' ? '' : suggestion.expectation_mode,
    seriesDiscriminator: redactSensitiveNumbers(suggestion.series_discriminator),
    titleTemplate: suggestion.title.convention?.template ?? '',
    acquisitionSourceId: '',
    allOf: suggestion.metadata.policy.all_of.join(', '),
    anyOf: suggestion.metadata.policy.any_of.join(', '),
    noneOf: suggestion.metadata.policy.none_of.join(', '),
    documentTypeId: suggestion.metadata.policy.required_document_type_id?.toString() ?? '',
  });
  const needsSeries = suggestion.kind === 'statement' && !suggestion.statement_series_id;
  const cadenceMissing =
    (draft.mode === 'recurring' || draft.mode === 'periodic') && !suggestion.cadence;
  const tagListsValid = [draft.allOf, draft.anyOf, draft.noneOf].every(isValidTagList);
  const titlePreviews = suggestion.title.convention
    ? suggestion.title.examples.map((example) =>
      renderDraftTitle(suggestion.title.convention!, draft.titleTemplate, example.after))
    : [];
  const titleValid = !suggestion.title.convention
    || (titlePreviews.length > 0 && titlePreviews.every((preview) => preview.rendered));
  const valid = Boolean(draft.mode) && !needsSeries && !cadenceMissing && tagListsValid && titleValid;
  const formatTags = (tagIds: number[]) =>
    tagIds.map((tagId) =>
      redactSensitiveNumbers(suggestion.metadata.tag_names[String(tagId)] ?? `Tag #${tagId}`)).join(', ') || 'None';

  return (
    <article className="correspondent-suggestion">
      <div className="correspondent-suggestion-header">
        <div>
          <div className="correspondent-suggestion-title">{redactSensitiveNumbers(suggestion.series_discriminator)}</div>
          <div className="correspondent-muted">{humanize(suggestion.kind)} · {suggestion.evidence.sample_size} documents</div>
        </div>
        <div className="correspondent-badges">
          <Badge tone={suggestion.expectation_mode === 'unknown' ? 'warning' : 'info'}>
            {humanize(suggestion.expectation_mode)}
          </Badge>
          {suggestion.evidence.confidence != null ? (
            <ConfidenceBadge pct={Math.round(suggestion.evidence.confidence * 100)} />
          ) : null}
        </div>
      </div>

      <div className="correspondent-evidence-grid">
        <div>
          <span className="correspondent-label">Observed cadence</span>
          <strong>{suggestion.cadence ? `${humanize(suggestion.cadence.frequency)} · day ${suggestion.cadence.expected_day ?? 'variable'}` : 'Not established'}</strong>
          <span>{suggestion.evidence.observed_from ?? '—'} to {suggestion.evidence.observed_to ?? '—'}</span>
        </div>
        <div>
          <span className="correspondent-label">Metadata pattern</span>
          <strong>{suggestion.metadata.required_document_type_name ?? 'Mixed document types'}</strong>
          <span>{suggestion.metadata.policy.all_of.length} required tags · {suggestion.metadata.policy.any_of.length} family choices</span>
        </div>
        <div>
          <span className="correspondent-label">Acquisition evidence</span>
          <strong>{humanize(suggestion.acquisition.channel)}</strong>
          <span>{suggestion.acquisition.reason_codes.map(humanize).join(', ')}</span>
        </div>
      </div>

      <div className="correspondent-form-grid">
        <label>
          <span>Expectation mode</span>
          <select
            aria-label={`Expectation mode for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
            value={draft.mode}
            onChange={(event) => setDraft({ ...draft, mode: event.target.value as ExpectationMode | '' })}
          >
            <option value="">Select after review</option>
            {MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>
          <span>Series name</span>
          <input
            value={draft.seriesDiscriminator}
            onChange={(event) => setDraft({ ...draft, seriesDiscriminator: event.target.value })}
          />
        </label>
        <label>
          <span>Acquisition source</span>
          <select
            value={draft.acquisitionSourceId}
            onChange={(event) => setDraft({ ...draft, acquisitionSourceId: event.target.value })}
          >
            <option value="">Not configured</option>
            {acquisitionSources.map((source) => (
              <option key={source.id} value={source.id}>{humanize(source.channel)} · {humanize(source.delivery_mode)}</option>
            ))}
          </select>
        </label>
        <label className="correspondent-wide-field">
          <span>Title convention</span>
          <input
            value={draft.titleTemplate}
            disabled={!suggestion.title.convention}
            onChange={(event) => setDraft({ ...draft, titleTemplate: event.target.value })}
          />
        </label>
        <label>
          <span>All required tag IDs</span>
          <input
            aria-label={`All required tags for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
            value={draft.allOf}
            onChange={(event) => setDraft({ ...draft, allOf: event.target.value })}
          />
          <small>{formatTags(parseTagIds(draft.allOf))}</small>
        </label>
        <label>
          <span>Any required tag IDs</span>
          <input
            aria-label={`Any required tags for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
            value={draft.anyOf}
            onChange={(event) => setDraft({ ...draft, anyOf: event.target.value })}
          />
          <small>{formatTags(parseTagIds(draft.anyOf))}</small>
        </label>
        <label>
          <span>Forbidden tag IDs</span>
          <input
            aria-label={`Forbidden tags for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
            value={draft.noneOf}
            onChange={(event) => setDraft({ ...draft, noneOf: event.target.value })}
          />
          <small>{formatTags(parseTagIds(draft.noneOf))}</small>
        </label>
        <label>
          <span>Required document type ID</span>
          <input
            type="number"
            min="1"
            value={draft.documentTypeId}
            onChange={(event) => setDraft({ ...draft, documentTypeId: event.target.value })}
          />
          <small>{suggestion.metadata.required_document_type_name ?? 'No required type'}</small>
        </label>
      </div>

      {needsSeries ? (
        <div className="correspondent-callout warning">
          This statement candidate is ambiguous. Group it into a statement series before confirming or dismissing policy.
        </div>
      ) : null}
      {cadenceMissing ? (
        <div className="correspondent-callout warning">Recurring and periodic expectations require established cadence evidence.</div>
      ) : null}
      {!tagListsValid ? (
        <div className="correspondent-callout warning">Tag rules must contain only positive Paperless tag IDs separated by commas.</div>
      ) : null}
      {!titleValid ? (
        <div className="correspondent-callout warning">The title template must use available fields and render to 128 characters or fewer.</div>
      ) : null}

      <div className="correspondent-title-preview">
        <div className="correspondent-preview-header">
          <strong>Title preview</strong>
          <span>{Math.round(suggestion.title.coverage * 100)}% coverage · {suggestion.title.exception_document_ids.length} exceptions</span>
        </div>
        {suggestion.title.examples.map((example, index) => {
          const draftPreview = titlePreviews[index];
          return (
            <div className="correspondent-preview-row" key={example.document_id}>
            <div>
              <span className="correspondent-label">Current</span>
              <span>{redactSensitiveNumbers(example.before)}</span>
            </div>
            <div>
              <span className="correspondent-label">Proposed</span>
              <span>
                {draftPreview?.rendered
                  ? redactSensitiveNumbers(draftPreview.rendered)
                  : `Missing ${draftPreview?.missingFields.join(', ') || example.missing_fields.join(', ')}`}
              </span>
            </div>
            {paperlessUrl ? (
              <a href={`${paperlessUrl}/documents/${example.document_id}/details`} target="_blank" rel="noreferrer">
                Open in Paperless
              </a>
            ) : null}
            </div>
          );
        })}
      </div>

      <div className="correspondent-actions">
        <Button variant="primary" disabled={!valid || busy} onClick={() => onDecision(suggestion, draft, 'confirmed')}>
          Confirm expectation
        </Button>
        <Button disabled={!valid || busy} onClick={() => onDecision(suggestion, draft, 'dismissed')}>
          Dismiss suggestion
        </Button>
      </div>
    </article>
  );
}

export default function CorrespondentReview() {
  const navigate = useNavigate();
  const { correspondentId } = useParams();
  const selectedId = correspondentId ? Number(correspondentId) : null;
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const [profiles, setProfiles] = useState<CorrespondentProfile[]>([]);
  const [expectationsByProfile, setExpectationsByProfile] = useState<Record<number, DocumentExpectation[]>>({});
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [acquisitionSources, setAcquisitionSources] = useState<AcquisitionSource[]>([]);
  const [externalCandidates, setExternalCandidates] = useState<ExternalCandidate[]>([]);
  const [candidateExpectation, setCandidateExpectation] = useState<Record<string, string>>({});
  const [paperlessUrl, setPaperlessUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);
  const [relinkTarget, setRelinkTarget] = useState('');
  const [sourceDraft, setSourceDraft] = useState({
    channel: 'portal_manual',
    delivery_mode: 'pull',
    portal_url: '',
  });

  const selectedProfile = profiles.find((profile) => profile.correspondent_id === selectedId) ?? null;
  const selectedProfileTerminal = selectedProfile?.lifecycle_status === 'retired'
    || selectedProfile?.review_status === 'ignored';
  const selectedExpectations = useMemo(
    () => selectedId ? expectationsByProfile[selectedId] ?? [] : [],
    [expectationsByProfile, selectedId],
  );
  const selectedExternalCandidates = useMemo(
    () => externalCandidates.filter((candidate) =>
      candidate.correspondent_id === selectedId
      || (candidate.correspondent_id == null && candidate.active && candidate.outcome === 'unreviewed')),
    [externalCandidates, selectedId],
  );

  const loadWorkspace = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [profilePayload, sourcePayload, paperlessPayload, candidatePayload] = await Promise.all([
        endpoints.statements.correspondentProfiles() as Promise<CorrespondentProfile[]>,
        endpoints.statements.acquisitionSources() as Promise<AcquisitionSource[]>,
        endpoints.statements.paperlessUrl() as Promise<{ paperless_url?: string | null }>,
        endpoints.statements.externalCandidates() as Promise<ExternalCandidate[]>,
      ]);
      const expectationPairs = await Promise.all(
        profilePayload.map(async (profile) => [
          profile.correspondent_id,
          await endpoints.statements.correspondentExpectations(profile.correspondent_id) as DocumentExpectation[],
        ] as const),
      );
      setProfiles(profilePayload);
      setAcquisitionSources(sourcePayload);
      setPaperlessUrl((paperlessPayload.paperless_url ?? '').replace(/\/$/, ''));
      setExternalCandidates(candidatePayload);
      setExpectationsByProfile(Object.fromEntries(expectationPairs));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    setAnalysis(null);
    setRelinkTarget('');
  }, [selectedId]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), getToastDuration(toast.tone));
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const sortedProfiles = useMemo(
    () => [...profiles].sort((left, right) => {
      const leftPriority = profilePriority(left, expectationsByProfile[left.correspondent_id]?.length ?? 0);
      const rightPriority = profilePriority(right, expectationsByProfile[right.correspondent_id]?.length ?? 0);
      return leftPriority.rank - rightPriority.rank || left.current_name.localeCompare(right.current_name);
    }),
    [profiles, expectationsByProfile],
  );

  const runAction = useCallback(async (action: () => Promise<unknown>, successMessage: string) => {
    setBusy(true);
    try {
      await action();
      setToast({ message: successMessage, tone: 'success' });
      await loadWorkspace();
    } catch (requestError) {
      setToast({ message: getErrorMessage(requestError), tone: 'error' });
    } finally {
      setBusy(false);
    }
  }, [loadWorkspace]);

  const analyzeSelected = useCallback(async () => {
    if (!selectedId) return;
    setDetailLoading(true);
    try {
      const result = await endpoints.statements.analyzeCorrespondentProfile(selectedId) as AnalysisResult;
      if (selectedIdRef.current !== result.correspondent_id) return;
      setAnalysis(result);
      setToast({ message: `Analyzed ${result.suggestions.length} candidate series.`, tone: 'success' });
      await loadWorkspace();
    } catch (requestError) {
      setToast({ message: getErrorMessage(requestError), tone: 'error' });
    } finally {
      setDetailLoading(false);
    }
  }, [loadWorkspace, selectedId]);

  const decideSuggestion = useCallback(async (
    suggestion: Suggestion,
    draft: SuggestionDraft,
    status: 'confirmed' | 'dismissed',
  ) => {
    if (!selectedId) return;
    setBusy(true);
    try {
      const existing = selectedExpectations.find((expectation) =>
        expectation.status !== 'retired'
        && expectation.kind === suggestion.kind
        && (
          suggestion.statement_series_id
            ? expectation.statement_series_id === suggestion.statement_series_id
            : expectation.series_discriminator === draft.seriesDiscriminator
        ));
      const expectationBody = buildExpectationBody(suggestion, draft, status);
      if (!existing) {
        await endpoints.statements.createCorrespondentExpectation(
          selectedId,
          expectationBody,
        );
      } else {
        const { kind: _kind, statement_series_id: _seriesId, ...updateBody } = expectationBody;
        await endpoints.statements.updateDocumentExpectation(existing.id, updateBody);
      }
      try {
        await endpoints.statements.updateCorrespondentProfile(selectedId, {
          review_status: 'reviewed',
          last_reviewed_at: new Date().toISOString(),
        });
        setToast({
          message: status === 'confirmed' ? 'Expectation confirmed.' : 'Suggestion dismissed.',
          tone: 'success',
        });
      } catch (profileError) {
        setToast({
          message: `${status === 'confirmed' ? 'Expectation confirmed' : 'Suggestion dismissed'}, but the profile review status was not updated: ${getErrorMessage(profileError)}`,
          tone: 'error',
        });
      }
      await loadWorkspace();
    } catch (requestError) {
      setToast({ message: getErrorMessage(requestError), tone: 'error' });
    } finally {
      setBusy(false);
    }
  }, [loadWorkspace, selectedExpectations, selectedId]);

  const createSource = useCallback(async () => {
    await runAction(
      async () => {
        await endpoints.statements.createAcquisitionSource({
          ...sourceDraft,
          portal_url: sourceDraft.portal_url || null,
          instructions: null,
          automation_state: 'not_applicable',
        });
        setSourceDraft({ channel: 'portal_manual', delivery_mode: 'pull', portal_url: '' });
      },
      'Acquisition source added.',
    );
  }, [runAction, sourceDraft]);

  const reviewCandidate = useCallback(async (
    candidate: ExternalCandidate,
    outcome: 'mapped' | 'suggested' | 'ambiguous' | 'not_applicable',
  ) => {
    if (!selectedId) return;
    const body: Record<string, unknown> = { outcome };
    if (outcome === 'mapped') body.expectation_id = candidateExpectation[candidate.id];
    if (outcome === 'suggested' || outcome === 'ambiguous') body.correspondent_id = selectedId;
    await runAction(
      () => endpoints.statements.reviewExternalCandidate(candidate.id, body),
      `Candidate marked ${humanize(outcome).toLowerCase()}.`,
    );
  }, [candidateExpectation, runAction, selectedId]);

  return (
    <>
      <PageHeader
        title="Correspondent Review"
        desc="Review Paperless history, confirm document expectations, and keep document editing in Paperless."
        actions={
          <div className="correspondent-actions">
            <Button
              disabled={busy}
              onClick={() => void runAction(
                () => endpoints.statements.syncCorrespondentProfiles(),
                'Paperless correspondents synchronized.',
              )}
            >
              Sync correspondents
            </Button>
            <Button
              variant="primary"
              disabled={busy || profiles.length === 0}
              onClick={() => void runAction(
                () => endpoints.statements.analyzeCorrespondentProfiles(),
                'All active correspondents analyzed.',
              )}
            >
              Analyze all
            </Button>
          </div>
        }
      />

      {error ? <ErrorState message={error} onRetry={() => void loadWorkspace()} /> : null}
      {loading ? <SkeletonLoader variant="detail-panel" /> : null}
      {!loading && !error && profiles.length === 0 ? (
        <EmptyState
          icon="📬"
          title="No correspondent profiles"
          desc="Synchronize Paperless correspondents to create the review inventory."
          action="Sync correspondents"
          onAction={() => void runAction(
            () => endpoints.statements.syncCorrespondentProfiles(),
            'Paperless correspondents synchronized.',
          )}
        />
      ) : null}

      {!loading && !error && profiles.length > 0 ? (
        <div className="correspondent-workspace">
          <aside className="correspondent-inventory" aria-label="Correspondent inventory">
            <div className="correspondent-inventory-header">
              <strong>Review queue</strong>
              <span>{profiles.filter((profile) => profile.review_status === 'unreviewed').length} unreviewed</span>
            </div>
            {sortedProfiles.map((profile) => {
              const expectations = expectationsByProfile[profile.correspondent_id]?.length ?? 0;
              const priority = profilePriority(profile, expectations);
              return (
                <button
                  key={profile.correspondent_id}
                  type="button"
                  className={`correspondent-inventory-item ${selectedId === profile.correspondent_id ? 'active' : ''}`}
                  onClick={() => navigate(`/correspondents/${profile.correspondent_id}`)}
                >
                  <div>
                    <strong>{redactSensitiveNumbers(profile.current_name)}</strong>
                    <span>{profile.observed_summary.document_count} docs · {expectations} expectations</span>
                  </div>
                  <Badge tone={priority.tone}>{priority.label}</Badge>
                </button>
              );
            })}
          </aside>

          <section className="correspondent-detail">
            {!selectedProfile ? (
              <Card>
                <EmptyState icon="👈" title="Select a correspondent" desc="Choose an item from the prioritized queue to inspect its evidence and policy." />
              </Card>
            ) : (
              <>
                <Card
                  title={
                    <div>
                      <div>{redactSensitiveNumbers(selectedProfile.current_name)}</div>
                      <div className="correspondent-muted">Paperless correspondent #{selectedProfile.correspondent_id}</div>
                    </div>
                  }
                  actions={
                    <div className="correspondent-actions">
                      <Badge tone={selectedProfile.lifecycle_status === 'orphaned' ? 'danger' : 'ok'}>
                        {humanize(selectedProfile.lifecycle_status)}
                      </Badge>
                      <Button
                        variant="primary"
                        disabled={detailLoading || busy || selectedProfileTerminal}
                        onClick={() => void analyzeSelected()}
                      >
                        {detailLoading ? 'Analyzing…' : 'Analyze history'}
                      </Button>
                    </div>
                  }
                >
                  <div className="correspondent-summary-grid">
                    <div><span>Documents</span><strong>{selectedProfile.observed_summary.document_count}</strong></div>
                    <div><span>Candidate series</span><strong>{selectedProfile.observed_summary.candidate_series_count}</strong></div>
                    <div><span>Title patterns</span><strong>{selectedProfile.observed_summary.title_pattern_count}</strong></div>
                    <div><span>Last analyzed</span><strong>{formatDate(selectedProfile.last_analyzed_at)}</strong></div>
                  </div>
                  <div className="correspondent-actions correspondent-profile-actions">
                    <Button
                      variant="success"
                      disabled={busy || selectedProfile.review_status === 'reviewed' || selectedProfileTerminal}
                      onClick={() => void runAction(
                        () => endpoints.statements.updateCorrespondentProfile(selectedProfile.correspondent_id, {
                          review_status: 'reviewed',
                          last_reviewed_at: new Date().toISOString(),
                        }),
                        'Correspondent marked reviewed.',
                      )}
                    >
                      Mark reviewed
                    </Button>
                    <Button
                      disabled={busy || selectedProfile.lifecycle_status === 'retired'}
                      onClick={() => void runAction(
                        () => endpoints.statements.updateCorrespondentProfile(selectedProfile.correspondent_id, {
                          review_status: selectedProfile.review_status === 'ignored' ? 'unreviewed' : 'ignored',
                          last_reviewed_at: new Date().toISOString(),
                        }),
                        selectedProfile.review_status === 'ignored' ? 'Correspondent returned to review.' : 'Correspondent ignored.',
                      )}
                    >
                      {selectedProfile.review_status === 'ignored' ? 'Return to review' : 'Ignore'}
                    </Button>
                    <Button
                      variant="danger"
                      disabled={busy || selectedProfile.lifecycle_status === 'retired'}
                      onClick={() => void runAction(
                        () => endpoints.statements.updateCorrespondentProfile(selectedProfile.correspondent_id, {
                          lifecycle_status: 'retired',
                        }),
                        'Correspondent profile retired.',
                      )}
                    >
                      Retire profile
                    </Button>
                    {selectedProfile.lifecycle_status === 'orphaned' ? (
                      <>
                        <select
                          aria-label="Relink correspondent"
                          value={relinkTarget}
                          onChange={(event) => setRelinkTarget(event.target.value)}
                        >
                          <option value="">Select relink target</option>
                          {profiles
                            .filter((profile) => profile.lifecycle_status === 'active' && profile.correspondent_id !== selectedProfile.correspondent_id)
                            .map((profile) => (
                              <option key={profile.correspondent_id} value={profile.correspondent_id}>
                                {redactSensitiveNumbers(profile.current_name)}
                              </option>
                            ))}
                        </select>
                        <Button
                          disabled={!relinkTarget || busy}
                          onClick={() => void runAction(
                            () => endpoints.statements.relinkCorrespondentProfile(
                              selectedProfile.correspondent_id,
                              { correspondent_id: Number(relinkTarget) },
                            ),
                            'Correspondent profile relinked.',
                          )}
                        >
                          Relink
                        </Button>
                      </>
                    ) : null}
                  </div>
                </Card>

                <Card title={`Expectations (${selectedExpectations.length})`}>
                  {selectedExpectations.length === 0 ? (
                    <div className="correspondent-muted">No reviewed expectations yet.</div>
                  ) : (
                    <div className="correspondent-expectation-list">
                      {selectedExpectations.map((expectation) => (
                        <div className="correspondent-expectation" key={expectation.id}>
                          <div>
                            <strong>{redactSensitiveNumbers(expectation.series_discriminator ?? humanize(expectation.kind))}</strong>
                            <span>{humanize(expectation.expectation_mode)} · {expectation.cadence?.frequency ?? 'no cadence'}</span>
                          </div>
                          <div className="correspondent-actions">
                            <Badge tone={expectation.status === 'confirmed' ? 'ok' : expectation.status === 'dismissed' ? 'muted' : 'warning'}>
                              {humanize(expectation.status)}
                            </Badge>
                            {expectation.status !== 'retired' ? (
                              <Button
                                size="sm"
                                disabled={busy || selectedProfileTerminal}
                                onClick={() => void runAction(
                                  () => endpoints.statements.updateDocumentExpectation(expectation.id, { status: 'retired' }),
                                  'Expectation retired.',
                                )}
                              >
                                Retire
                              </Button>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>

                <Card title={`External candidates (${selectedExternalCandidates.length})`}>
                  <div className="correspondent-callout">
                    Account candidates are recurrence evidence only. They do not establish cadence
                    or prove that a statement exists. Recurring obligations do not create invoice
                    or receipt requirements.
                  </div>
                  {selectedExternalCandidates.length === 0 ? (
                    <div className="correspondent-muted">No external candidates need review.</div>
                  ) : (
                    <div className="correspondent-expectation-list">
                      {selectedExternalCandidates.map((candidate) => (
                        <div className="correspondent-expectation" key={candidate.id}>
                          <div>
                            <strong>{redactSensitiveNumbers(candidate.display_hint)}</strong>
                            <span>
                              {candidate.kind === 'accountStatementCandidate'
                                ? 'Account statement candidate'
                                : 'Recurring document candidate'}
                              {' · '}{Math.round(candidate.confidence * 100)}% confidence
                            </span>
                            {candidate.review_finding ? (
                              <span>{humanize(candidate.review_finding)}</span>
                            ) : null}
                          </div>
                          <div className="correspondent-actions">
                            {!candidate.active ? <Badge tone="warning">Inactive source</Badge> : null}
                            {candidate.likely_multiple_statement_series ? (
                              <Badge tone="info">Multiple statement series likely</Badge>
                            ) : null}
                            <Badge tone={candidate.outcome === 'mapped' ? 'ok' : 'warning'}>
                              {humanize(candidate.outcome)}
                            </Badge>
                            <select
                              aria-label={`Expectation for ${candidate.display_hint}`}
                              value={candidateExpectation[candidate.id] ?? candidate.expectation_id ?? ''}
                              onChange={(event) => setCandidateExpectation({
                                ...candidateExpectation,
                                [candidate.id]: event.target.value,
                              })}
                            >
                              <option value="">Map to expectation</option>
                              {selectedExpectations
                                .filter((expectation) => expectation.status !== 'retired')
                                .map((expectation) => (
                                  <option key={expectation.id} value={expectation.id}>
                                    {redactSensitiveNumbers(
                                      expectation.series_discriminator ?? humanize(expectation.kind),
                                    )}
                                  </option>
                                ))}
                            </select>
                            <Button
                              size="sm"
                              disabled={
                                busy
                                || selectedProfileTerminal
                                || !(candidateExpectation[candidate.id] ?? candidate.expectation_id)
                              }
                              onClick={() => void reviewCandidate(candidate, 'mapped')}
                            >
                              Map
                            </Button>
                            <Button
                              size="sm"
                              disabled={busy || selectedProfileTerminal}
                              onClick={() => void reviewCandidate(candidate, 'suggested')}
                            >
                              Create suggestion
                            </Button>
                            <Button
                              size="sm"
                              disabled={busy || selectedProfileTerminal}
                              onClick={() => void reviewCandidate(candidate, 'ambiguous')}
                            >
                              Leave ambiguous
                            </Button>
                            <Button
                              size="sm"
                              disabled={busy || selectedProfileTerminal}
                              onClick={() => void reviewCandidate(candidate, 'not_applicable')}
                            >
                              Not applicable
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>

                {detailLoading ? <SkeletonLoader variant="cards" /> : null}
                {analysis?.correspondent_id === selectedId && !selectedProfileTerminal ? (
                  <Card title={`Candidate expectations (${analysis.suggestions.length})`}>
                    {analysis.suggestions.length === 0 ? (
                      <EmptyState icon="✓" title="No policy candidates" desc="Paperless history did not contain enough bounded evidence for a suggestion." />
                    ) : (
                      <div className="correspondent-suggestions">
                        {analysis.suggestions.map((suggestion) => (
                          <SuggestionCard
                            key={suggestion.suggestion_key}
                            suggestion={suggestion}
                            acquisitionSources={acquisitionSources}
                            paperlessUrl={paperlessUrl}
                            busy={busy}
                            onDecision={(candidate, draft, status) => void decideSuggestion(candidate, draft, status)}
                          />
                        ))}
                      </div>
                    )}
                  </Card>
                ) : null}

                <Card title="Add acquisition source">
                  <div className="correspondent-form-grid">
                    <label>
                      <span>Channel</span>
                      <select value={sourceDraft.channel} onChange={(event) => setSourceDraft({ ...sourceDraft, channel: event.target.value })}>
                        {['paperless_mail', 'email_manual', 'direct_api', 'portal_manual', 'snail_mail', 'linked_storage', 'unknown'].map((channel) => (
                          <option value={channel} key={channel}>{humanize(channel)}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>Delivery mode</span>
                      <select value={sourceDraft.delivery_mode} onChange={(event) => setSourceDraft({ ...sourceDraft, delivery_mode: event.target.value })}>
                        <option value="push">Push</option>
                        <option value="pull">Pull</option>
                        <option value="physical">Physical</option>
                      </select>
                    </label>
                    <label className="correspondent-wide-field">
                      <span>Credential-free portal URL</span>
                      <input
                        type="url"
                        value={sourceDraft.portal_url}
                        placeholder="https://provider.example/statements"
                        onChange={(event) => setSourceDraft({ ...sourceDraft, portal_url: event.target.value })}
                      />
                    </label>
                  </div>
                  <div className="correspondent-callout">Only credential-free portal landing pages are stored. Credentials and account details remain outside OWL.</div>
                  <Button disabled={busy} onClick={() => void createSource()}>Add source</Button>
                </Card>
              </>
            )}
          </section>
        </div>
      ) : null}

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </>
  );
}
