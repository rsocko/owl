import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { MetadataMultiTypeahead, MetadataTypeahead, type MetadataOption } from '../components/MetadataTypeahead';
import { Badge, Button, Card, ConfidenceBadge, EmptyState, ErrorState, PageHeader, SkeletonLoader, Toast } from '../components/ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import '../styles/correspondent-review.css';

type ReviewStatus = 'unreviewed' | 'reviewed' | 'ignored';
type LifecycleStatus = 'active' | 'orphaned' | 'retired';
type ExpectationMode = 'recurring' | 'periodic' | 'one_off' | 'irregular' | 'not_expected';
type ExpectationStatus = 'suggested' | 'confirmed' | 'dismissed' | 'retired';
type Importance = 'routine' | 'important' | 'critical';
type MonitoringMode = 'none' | 'presence' | 'deadline';
type CalendarRuleType = 'annual_month' | 'quarter' | 'tax_year';

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

interface AnalysisSummary {
  correspondent_id: number;
  snapshot_version: number;
  algorithm_version: string;
  reason: string;
  analyzed_at: string;
  suggestion_count: number;
  unresolved_suggestion_count: number;
  freshness: 'fresh' | 'stale';
  stale_reason?: string | null;
}

interface AnalysisSnapshot {
  id: string;
  correspondent_id: number;
  snapshot_version: number;
  algorithm_version: string;
  reason: string;
  source_fingerprint: string;
  analyzed_at: string;
  result: AnalysisResult;
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
  document_ids: number[];
  sample_document_ids: number[];
}

interface AnalysisResult {
  correspondent_id: number;
  correspondent_name: string;
  analyzed_at: string;
  observed_summary: ObservedSummary;
  account_identifiers: {
    extraction_requested: boolean;
    stored_document_count: number;
    extracted_document_count: number;
    unresolved_document_count: number;
    extraction_failed_document_count: number;
  };
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
  importance: Importance;
  monitoring_mode: MonitoringMode;
  calendar_rule?: {
    rule_type: CalendarRuleType | 'one_off';
    expected_month?: number | null;
    expected_quarter?: number | null;
    tax_year_offset?: number | null;
    occurrence_id?: string | null;
    due_start?: string | null;
    due_end?: string | null;
    occurrence_status?: 'pending' | 'fulfilled' | 'overdue' | 'cancelled' | null;
    fulfilled_at?: string | null;
  } | null;
  grace_period_days: number;
  evidence: Evidence;
  title_convention?: TitleConvention | null;
  metadata_policy: MetadataPolicy;
  acquisition_source_id?: string | null;
}

interface DocumentMetadataSnapshot {
  title: string;
  tag_ids: number[];
  tag_names: string[];
  document_type_id?: number | null;
  document_type_name?: string | null;
}

interface PolicyViolationFinding {
  preview_id: string;
  operation: {
    expectation_id: string;
    document_id: number;
    expected: DocumentMetadataSnapshot;
    patch: {
      title?: string;
      tags?: number[];
      document_type?: number;
    };
  };
  proposed: DocumentMetadataSnapshot;
  violations: string[];
  unresolved_violations: string[];
  missing_title_fields: string[];
}

interface ExpectationPolicyPreview {
  expectation_id: string;
  correspondent_id: number;
  matched_document_count: number;
  compliant_document_count: number;
  findings: PolicyViolationFinding[];
}

interface PolicyOperationResult {
  preview_id: string;
  document_id: number;
  status: 'succeeded' | 'failed';
  audit_event_id?: string | null;
  error_code?: string | null;
  message: string;
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
  account_name?: string | null;
  institution_name?: string | null;
  account_type?: 'checking' | 'savings' | 'credit' | 'cash' | 'loan' | 'investment' | 'other' | null;
  account_last_four?: string | null;
  source_as_of: string;
  outcome: 'unreviewed' | 'mapped' | 'suggested' | 'ambiguous' | 'not_applicable';
  expectation_id?: string | null;
  expectation_ids: string[];
  identifier_match_expectation_ids: string[];
  correspondent_id?: number | null;
  likely_multiple_statement_series: boolean;
  recurrence_evidence: 'high' | 'none';
  review_finding?: string | null;
}

interface ExternalSignalConnection {
  configured: boolean;
  last_source_generation?: string | null;
  last_source_as_of?: string | null;
  last_synced_at?: string | null;
}

interface SuggestionDraft {
  mode: ExpectationMode | '';
  importance: Importance;
  monitoringMode: MonitoringMode;
  frequency: Cadence['frequency'];
  calendarRuleType: CalendarRuleType | '';
  expectedMonth: string;
  expectedQuarter: string;
  taxYearOffset: string;
  occurrenceId: string;
  dueStart: string;
  dueEnd: string;
  availabilityEarliestDays: string;
  availabilityLatestDays: string;
  gracePeriodDays: string;
  reminderDays: string;
  overdueReminderIntervalDays: string;
  maxOverdueReminders: string;
  matchCalendarPeriod: boolean;
  seriesDiscriminator: string;
  titleTemplate: string;
  acquisitionSourceId: string;
  allOf: number[];
  anyOf: number[];
  noneOf: number[];
  documentTypeId: string;
}

const MODE_OPTIONS: Array<{ value: ExpectationMode; label: string; consequence: string }> = [
  { value: 'recurring', label: 'Recurring', consequence: 'Regular expectation; presence or deadline monitoring can alert.' },
  { value: 'periodic', label: 'Periodic', consequence: 'Calendar-specific quarterly or annual expectation; monitoring can alert.' },
  { value: 'one_off', label: 'One-off', consequence: 'One named occurrence and due window; it cannot recur after fulfillment or cancellation.' },
  { value: 'irregular', label: 'Irregular', consequence: 'Keeps classification and metadata policy; absence never alerts.' },
  { value: 'not_expected', label: 'Not expected', consequence: 'Durable negative policy for this candidate; absence never alerts.' },
];

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The correspondent workspace could not complete the request.';
}

function formatDate(value?: string | null): string {
  if (!value) return 'Never';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function formatSnapshotAge(value?: string | null): string {
  if (!value) return 'No saved analysis';
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return formatDate(value);
  const days = Math.max(0, Math.floor((Date.now() - timestamp) / 86_400_000));
  if (days === 0) return 'Analyzed today';
  if (days === 1) return 'Analyzed yesterday';
  return `Analyzed ${days} days ago`;
}

function humanize(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function redactSensitiveNumbers(value: string): string {
  return value.replace(/\b\d{5,}\b/g, '[redacted]');
}

function formatMetadataList(values: string[]): string {
  return values.length > 0 ? values.join(', ') : 'None';
}

function scrollToSection(sectionId: string): void {
  document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
  const mode = draft.mode || 'irregular';
  const activePolicy = status === 'confirmed';
  const cadence = activePolicy && (mode === 'recurring' || mode === 'periodic')
    ? {
      frequency: draft.frequency,
      expected_day: suggestion.cadence?.expected_day ?? null,
      availability_delay_days: suggestion.cadence?.availability_delay_days ?? 0,
      grace_period_days: Number(draft.gracePeriodDays) || 0,
    }
    : null;
  const calendarRule = !activePolicy
    ? null
    : mode === 'one_off'
      ? {
        rule_type: 'one_off',
        occurrence_id: draft.occurrenceId,
        due_start: draft.dueStart,
        due_end: draft.dueEnd,
        occurrence_status: 'pending',
      }
      : draft.calendarRuleType === 'annual_month'
        ? { rule_type: 'annual_month', expected_month: Number(draft.expectedMonth) }
        : draft.calendarRuleType === 'quarter'
          ? { rule_type: 'quarter', expected_quarter: Number(draft.expectedQuarter) }
          : draft.calendarRuleType === 'tax_year'
            ? {
              rule_type: 'tax_year',
              expected_month: Number(draft.expectedMonth),
              tax_year_offset: Number(draft.taxYearOffset),
            }
            : null;
  const reminderDays = [...new Set(
    draft.reminderDays
      .split(',')
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value) && value >= 0 && value <= 365),
  )].sort((left, right) => right - left);
  const titleExample = suggestion.title.convention
    ? suggestion.title.examples
      .map((example) => renderDraftTitle(suggestion.title.convention!, draft.titleTemplate, example.after))
      .find((preview) => preview.rendered)?.rendered
    : null;
  return {
    kind: suggestion.kind,
    document_type_id: draft.documentTypeId ? Number(draft.documentTypeId) : null,
    statement_series_id: suggestion.statement_series_id ?? null,
    document_ids: suggestion.document_ids,
    series_discriminator: draft.seriesDiscriminator || null,
    expectation_mode: mode,
    status,
    cadence,
    importance: draft.importance,
    monitoring_mode: activePolicy ? draft.monitoringMode : 'none',
    calendar_rule: calendarRule,
    availability_window: activePolicy ? {
      earliest_days: Number(draft.availabilityEarliestDays) || 0,
      latest_days: Number(draft.availabilityLatestDays) || 0,
    } : null,
    grace_period_days: activePolicy ? Number(draft.gracePeriodDays) || 0 : 0,
    escalation_policy: {
      reminder_days_before_due: reminderDays,
      overdue_reminder_interval_days: Number(draft.maxOverdueReminders) > 0
        ? Number(draft.overdueReminderIntervalDays)
        : null,
      max_overdue_reminders: Number(draft.maxOverdueReminders) || 0,
    },
    fulfillment_rule: {
      match_series: true,
      match_kind: true,
      match_calendar_period: mode === 'irregular' || mode === 'not_expected'
        ? false
        : draft.matchCalendarPeriod,
      required_tag_ids: [...draft.allOf, ...draft.anyOf],
      required_document_type_id: draft.documentTypeId ? Number(draft.documentTypeId) : null,
    },
    evidence: {
      ...suggestion.evidence,
      reason_codes: [...suggestion.evidence.reason_codes, `user_${status}`],
    },
    title_convention: suggestion.title.convention
      ? { ...suggestion.title.convention, template: draft.titleTemplate, example: titleExample }
      : null,
    metadata_policy: {
      all_of: draft.allOf,
      any_of: draft.anyOf,
      none_of: draft.noneOf,
      required_document_type_id: draft.documentTypeId ? Number(draft.documentTypeId) : null,
    },
    acquisition_source_id: draft.acquisitionSourceId || null,
  };
}

function SuggestionCard({
  suggestion,
  acquisitionSources,
  tags,
  documentTypes,
  paperlessUrl,
  busy,
  onDecision,
}: {
  suggestion: Suggestion;
  acquisitionSources: AcquisitionSource[];
  tags: MetadataOption[];
  documentTypes: MetadataOption[];
  paperlessUrl: string;
  busy: boolean;
  onDecision: (suggestion: Suggestion, draft: SuggestionDraft, status: 'confirmed' | 'dismissed') => void;
}) {
  const [draft, setDraft] = useState<SuggestionDraft>({
    mode: suggestion.expectation_mode === 'unknown' ? '' : suggestion.expectation_mode,
    importance: 'routine',
    monitoringMode: suggestion.expectation_mode === 'irregular'
      || suggestion.expectation_mode === 'not_expected'
      || suggestion.expectation_mode === 'unknown'
      ? 'none'
      : suggestion.expectation_mode === 'one_off' ? 'deadline' : 'presence',
    frequency: suggestion.cadence?.frequency ?? 'monthly',
    calendarRuleType: '',
    expectedMonth: '',
    expectedQuarter: '',
    taxYearOffset: '-1',
    occurrenceId: '',
    dueStart: '',
    dueEnd: '',
    availabilityEarliestDays: '0',
    availabilityLatestDays: String(suggestion.cadence?.availability_delay_days ?? 0),
    gracePeriodDays: String(suggestion.cadence?.grace_period_days ?? 5),
    reminderDays: '',
    overdueReminderIntervalDays: '7',
    maxOverdueReminders: '0',
    matchCalendarPeriod: true,
    seriesDiscriminator: redactSensitiveNumbers(suggestion.series_discriminator),
    titleTemplate: suggestion.title.convention?.template ?? '',
    acquisitionSourceId: '',
    allOf: suggestion.metadata.policy.all_of,
    anyOf: suggestion.metadata.policy.any_of,
    noneOf: suggestion.metadata.policy.none_of,
    documentTypeId: suggestion.metadata.policy.required_document_type_id?.toString() ?? '',
  });
  const tagOptions = useMemo(() => {
    const byId = new Map(tags.map((tag) => [tag.value, tag]));
    Object.entries(suggestion.metadata.tag_names).forEach(([id, name]) => {
      byId.set(id, { value: id, label: redactSensitiveNumbers(name) });
    });
    return [...byId.values()].sort((left, right) => left.label.localeCompare(right.label));
  }, [suggestion.metadata.tag_names, tags]);
  const documentTypeOptions = useMemo(() => {
    const byId = new Map(documentTypes.map((documentType) => [documentType.value, documentType]));
    const requiredId = suggestion.metadata.policy.required_document_type_id;
    if (requiredId && suggestion.metadata.required_document_type_name) {
      byId.set(String(requiredId), {
        value: String(requiredId),
        label: suggestion.metadata.required_document_type_name,
      });
    }
    return [...byId.values()].sort((left, right) => left.label.localeCompare(right.label));
  }, [
    documentTypes,
    suggestion.metadata.policy.required_document_type_id,
    suggestion.metadata.required_document_type_name,
  ]);
  const needsSeries = suggestion.kind === 'statement' && !suggestion.statement_series_id;
  const calendarMissing = draft.monitoringMode === 'deadline'
    && draft.mode !== 'one_off'
    && (
      !draft.calendarRuleType
      || (draft.calendarRuleType === 'annual_month' && !draft.expectedMonth)
      || (draft.calendarRuleType === 'quarter' && !draft.expectedQuarter)
      || (draft.calendarRuleType === 'tax_year' && (!draft.expectedMonth || !draft.taxYearOffset))
    );
  const occurrenceMissing = draft.mode === 'one_off'
    && (!draft.occurrenceId.trim() || !draft.dueStart || !draft.dueEnd || draft.dueStart > draft.dueEnd);
  const monitoringPolicyInvalid = draft.monitoringMode !== 'none' && (
    Number(draft.availabilityEarliestDays) > Number(draft.availabilityLatestDays)
    || Number(draft.gracePeriodDays) < 0
    || Number(draft.maxOverdueReminders) < 0
    || (
      Number(draft.maxOverdueReminders) > 0
      && Number(draft.overdueReminderIntervalDays) < 1
    )
  );
  const titlePreviews = suggestion.title.convention
    ? suggestion.title.examples.map((example) =>
      renderDraftTitle(suggestion.title.convention!, draft.titleTemplate, example.after))
    : [];
  const titleValid = !suggestion.title.convention
    || (titlePreviews.length > 0 && titlePreviews.every((preview) => preview.rendered));
  const valid = Boolean(draft.mode)
    && !needsSeries
    && !calendarMissing
    && !occurrenceMissing
    && !monitoringPolicyInvalid
    && titleValid;
  const selectedMode = MODE_OPTIONS.find((option) => option.value === draft.mode);

  return (
    <details className="correspondent-suggestion">
      <summary className="correspondent-suggestion-header">
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
      </summary>

      <div className="correspondent-suggestion-detail">
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
            onChange={(event) => {
              const mode = event.target.value as ExpectationMode | '';
              setDraft({
                ...draft,
                mode,
                monitoringMode: mode === 'irregular' || mode === 'not_expected' || !mode
                  ? 'none'
                  : mode === 'one_off' ? 'deadline' : draft.monitoringMode === 'none'
                    ? 'presence'
                    : draft.monitoringMode,
              });
            }}
          >
            <option value="">Select after review</option>
            {MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <small>{selectedMode?.consequence ?? 'Choose a durable expectation policy after reviewing the evidence.'}</small>
        </label>
        <label>
          <span>Importance</span>
          <select
            aria-label={`Importance for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
            value={draft.importance}
            onChange={(event) => {
              const importance = event.target.value as Importance;
              setDraft({
                ...draft,
                importance,
                reminderDays: importance === 'critical' ? '14, 7, 1' : importance === 'important' ? '7, 1' : '',
                maxOverdueReminders: importance === 'critical' ? '6' : importance === 'important' ? '3' : '0',
              });
            }}
          >
            <option value="routine">Routine</option>
            <option value="important">Important</option>
            <option value="critical">Critical</option>
          </select>
          <small>Changes priority and reminder intensity. It never creates an expectation by itself.</small>
        </label>
        <label>
          <span>Monitoring</span>
          <select
            aria-label={`Monitoring for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
            value={draft.monitoringMode}
            disabled={draft.mode === 'irregular' || draft.mode === 'not_expected' || draft.mode === 'one_off'}
            onChange={(event) => setDraft({ ...draft, monitoringMode: event.target.value as MonitoringMode })}
          >
            <option value="none">None — never alert on absence</option>
            <option value="presence">Presence — alert after the expected window</option>
            <option value="deadline">Deadline — remind and escalate against a date</option>
          </select>
          <small>Only confirmed expectations with applicable monitoring feed missing-document analysis.</small>
        </label>
        {(draft.mode === 'recurring' || draft.mode === 'periodic') ? (
          <label>
            <span>Frequency</span>
            <select
              aria-label={`Frequency for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
              value={draft.frequency}
              onChange={(event) => setDraft({ ...draft, frequency: event.target.value as Cadence['frequency'] })}
            >
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
              <option value="annual">Annual</option>
            </select>
            <small>User-defined schedules are allowed even when history is limited.</small>
          </label>
        ) : null}
        {draft.monitoringMode === 'deadline' && draft.mode !== 'one_off' ? (
          <>
            <label>
              <span>Calendar rule</span>
              <select
                aria-label={`Calendar rule for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                value={draft.calendarRuleType}
                onChange={(event) => setDraft({
                  ...draft,
                  calendarRuleType: event.target.value as CalendarRuleType | '',
                })}
              >
                <option value="">Select a calendar anchor</option>
                <option value="annual_month">Expected month</option>
                <option value="quarter">Expected quarter</option>
                <option value="tax_year">Tax-year document</option>
              </select>
            </label>
            {draft.calendarRuleType === 'quarter' ? (
              <label>
                <span>Expected quarter</span>
                <select
                  aria-label={`Expected quarter for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                  value={draft.expectedQuarter}
                  onChange={(event) => setDraft({ ...draft, expectedQuarter: event.target.value })}
                >
                  <option value="">Select quarter</option>
                  <option value="1">Q1</option>
                  <option value="2">Q2</option>
                  <option value="3">Q3</option>
                  <option value="4">Q4</option>
                </select>
              </label>
            ) : draft.calendarRuleType ? (
              <label>
                <span>Expected month</span>
                <select
                  aria-label={`Expected month for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                  value={draft.expectedMonth}
                  onChange={(event) => setDraft({ ...draft, expectedMonth: event.target.value })}
                >
                  <option value="">Select month</option>
                  {Array.from({ length: 12 }, (_, index) => (
                    <option key={index + 1} value={index + 1}>
                      {new Date(2026, index, 1).toLocaleString(undefined, { month: 'long' })}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {draft.calendarRuleType === 'tax_year' ? (
              <label>
                <span>Tax year relative to arrival</span>
                <select
                  aria-label={`Tax year offset for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                  value={draft.taxYearOffset}
                  onChange={(event) => setDraft({ ...draft, taxYearOffset: event.target.value })}
                >
                  <option value="-1">Previous tax year</option>
                  <option value="0">Current tax year</option>
                </select>
              </label>
            ) : null}
          </>
        ) : null}
        {draft.mode === 'one_off' ? (
          <>
            <label>
              <span>Occurrence name</span>
              <input
                aria-label={`Occurrence name for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                value={draft.occurrenceId}
                placeholder="e.g. 2026 renewal notice"
                onChange={(event) => setDraft({ ...draft, occurrenceId: event.target.value })}
              />
            </label>
            <label>
              <span>Available from</span>
              <input
                aria-label={`Due window start for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                type="date"
                value={draft.dueStart}
                onChange={(event) => setDraft({ ...draft, dueStart: event.target.value })}
              />
            </label>
            <label>
              <span>Due by</span>
              <input
                aria-label={`Due window end for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                type="date"
                value={draft.dueEnd}
                onChange={(event) => setDraft({ ...draft, dueEnd: event.target.value })}
              />
              <small>Saving creates one pending occurrence. Fulfilled or cancelled occurrences do not recur.</small>
            </label>
          </>
        ) : null}
        {draft.monitoringMode !== 'none' ? (
          <>
            <label>
              <span>Earliest arrival offset</span>
              <input
                aria-label={`Earliest arrival offset for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                type="number"
                min="-365"
                max="365"
                value={draft.availabilityEarliestDays}
                onChange={(event) => setDraft({ ...draft, availabilityEarliestDays: event.target.value })}
              />
              <small>Days relative to the covered period when availability can begin.</small>
            </label>
            <label>
              <span>Latest arrival offset</span>
              <input
                aria-label={`Latest arrival offset for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                type="number"
                min="-365"
                max="365"
                value={draft.availabilityLatestDays}
                onChange={(event) => setDraft({ ...draft, availabilityLatestDays: event.target.value })}
              />
            </label>
            <label>
              <span>Grace period</span>
              <input
                aria-label={`Grace period for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                type="number"
                min="0"
                max="365"
                value={draft.gracePeriodDays}
                onChange={(event) => setDraft({ ...draft, gracePeriodDays: event.target.value })}
              />
              <small>Days after the expected window before the document is considered missing.</small>
            </label>
            <label>
              <span>Reminder days before due</span>
              <input
                aria-label={`Reminder days for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                value={draft.reminderDays}
                placeholder="e.g. 14, 7, 1"
                onChange={(event) => setDraft({ ...draft, reminderDays: event.target.value })}
              />
            </label>
            <label>
              <span>Overdue reminder interval</span>
              <input
                aria-label={`Overdue reminder interval for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                type="number"
                min="1"
                max="365"
                disabled={Number(draft.maxOverdueReminders) === 0}
                value={draft.overdueReminderIntervalDays}
                onChange={(event) => setDraft({ ...draft, overdueReminderIntervalDays: event.target.value })}
              />
            </label>
            <label>
              <span>Maximum overdue reminders</span>
              <input
                aria-label={`Maximum overdue reminders for ${redactSensitiveNumbers(suggestion.series_discriminator)}`}
                type="number"
                min="0"
                max="100"
                value={draft.maxOverdueReminders}
                onChange={(event) => setDraft({ ...draft, maxOverdueReminders: event.target.value })}
              />
            </label>
          </>
        ) : null}
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
        <div className="correspondent-metadata-picker">
          <span>All required tags</span>
          <MetadataMultiTypeahead
          ariaLabel="All required tags"
          values={draft.allOf.map(String)}
          options={tagOptions}
          onChange={(allOf) => setDraft({ ...draft, allOf: allOf.map(Number) })}
          />
        </div>
        <label className="correspondent-checkbox-field">
          <input
            type="checkbox"
            checked={draft.matchCalendarPeriod}
            disabled={draft.mode === 'irregular' || draft.mode === 'not_expected'}
            onChange={(event) => setDraft({ ...draft, matchCalendarPeriod: event.target.checked })}
          />
          <span>Require the document to match this calendar period</span>
        </label>
        <div className="correspondent-metadata-picker">
          <span>Any required tags</span>
          <MetadataMultiTypeahead
          ariaLabel="Any required tags"
          values={draft.anyOf.map(String)}
          options={tagOptions}
          onChange={(anyOf) => setDraft({ ...draft, anyOf: anyOf.map(Number) })}
          />
        </div>
        <div className="correspondent-metadata-picker">
          <span>Forbidden tags</span>
          <MetadataMultiTypeahead
          ariaLabel="Forbidden tags"
          values={draft.noneOf.map(String)}
          options={tagOptions}
          onChange={(noneOf) => setDraft({ ...draft, noneOf: noneOf.map(Number) })}
          />
        </div>
        <label>
          <span>Required document type</span>
          <MetadataTypeahead
            ariaLabel="Required document type"
            value={draft.documentTypeId}
            options={documentTypeOptions}
            placeholder="Search document types…"
            onChange={(documentTypeId) => setDraft({ ...draft, documentTypeId })}
          />
        </label>
      </div>

      {needsSeries ? (
        <div className="correspondent-callout warning">
          This statement candidate is ambiguous. Group it into a statement series before confirming or dismissing policy.
        </div>
      ) : null}
      {calendarMissing ? (
        <div className="correspondent-callout warning">Deadline monitoring requires a complete calendar rule.</div>
      ) : null}
      {occurrenceMissing ? (
        <div className="correspondent-callout warning">A one-off expectation needs a unique occurrence name and valid due window.</div>
      ) : null}
      {monitoringPolicyInvalid ? (
        <div className="correspondent-callout warning">The availability and escalation windows must be valid before confirmation.</div>
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
        <Button disabled={busy} onClick={() => onDecision(suggestion, draft, 'dismissed')}>
          Dismiss suggestion
        </Button>
        <span className="correspondent-action-note">
          Dismissal records this review decision only; it creates no active expectation or missing alert.
        </span>
      </div>
      </div>
    </details>
  );
}

function ExternalCandidateReconciliation({
  candidates,
  profiles,
  expectationsByProfile,
  connection,
  syncError,
  paperlessUrl,
  busy,
  correspondentSelections,
  expectationSelections,
  onCorrespondentChange,
  onExpectationChange,
  onReview,
  onSync,
  onOpenCorrespondent,
}: {
  candidates: ExternalCandidate[];
  profiles: CorrespondentProfile[];
  expectationsByProfile: Record<number, DocumentExpectation[]>;
  connection: ExternalSignalConnection;
  syncError: string | null;
  paperlessUrl: string;
  busy: boolean;
  correspondentSelections: Record<string, string>;
  expectationSelections: Record<string, string[]>;
  onCorrespondentChange: (candidateId: string, correspondentId: string) => void;
  onExpectationChange: (candidateId: string, expectationIds: string[]) => void;
  onReview: (
    candidate: ExternalCandidate,
    outcome: 'mapped' | 'ambiguous' | 'not_applicable',
  ) => Promise<boolean>;
  onSync: () => void;
  onOpenCorrespondent: (correspondentId: number) => void;
}) {
  const activeProfiles = profiles.filter((profile) => profile.lifecycle_status === 'active');
  const [mode, setMode] = useState<'work' | 'audit'>('work');
  const [search, setSearch] = useState('');
  const [reasonFilter, setReasonFilter] = useState('');
  const [sortBy, setSortBy] = useState<'priority' | 'name' | 'newest'>('priority');
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [manualReviewIds, setManualReviewIds] = useState<Set<string>>(new Set());

  const candidateNeedsAction = useCallback((candidate: ExternalCandidate) => (
    candidate.outcome === 'unreviewed'
    || candidate.outcome === 'ambiguous'
    || candidate.outcome === 'suggested'
    || (candidate.outcome !== 'not_applicable' && Boolean(candidate.review_finding))
  ), []);
  const candidateReason = useCallback((candidate: ExternalCandidate) => {
    if (candidate.review_finding || !candidate.active) {
      return { key: 'changed', label: 'Existing mappings changed', rank: 0 };
    }
    if (candidate.outcome === 'ambiguous') {
      return { key: 'ambiguous', label: 'Unresolved', rank: 1 };
    }
    if (candidate.correspondent_id == null) {
      return { key: 'new', label: 'New and unassigned', rank: 2 };
    }
    if (candidate.expectation_ids.length === 0) {
      return { key: 'missing_series', label: 'Missing document series', rank: 3 };
    }
    return { key: 'confirm', label: 'Ready to confirm', rank: 4 };
  }, []);
  const candidateLabel = (candidate: ExternalCandidate) =>
    redactSensitiveNumbers(candidate.account_name ?? candidate.display_hint);
  const candidateDetails = (candidate: ExternalCandidate) => (
    candidate.kind === 'accountStatementCandidate'
      ? [
        candidate.institution_name,
        candidate.account_type ? humanize(candidate.account_type) : null,
        candidate.account_last_four ? `Account ending in ${candidate.account_last_four}` : null,
      ].filter(Boolean).join(' · ') || 'Account inventory signal'
      : 'Recurring transaction signal'
  );
  const workCandidates = useMemo(
    () => candidates.filter((candidate) =>
      candidateNeedsAction(candidate) || manualReviewIds.has(candidate.id)),
    [candidateNeedsAction, candidates, manualReviewIds],
  );
  const reviewedCandidates = useMemo(
    () => candidates.filter((candidate) => !candidateNeedsAction(candidate)),
    [candidateNeedsAction, candidates],
  );
  const filteredCandidates = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return workCandidates
      .filter((candidate) => {
        const searchable = [
          candidate.account_name,
          candidate.display_hint,
          candidate.institution_name,
          candidate.account_type,
          candidate.account_last_four,
        ].filter(Boolean).join(' ').toLowerCase();
        const reason = candidateReason(candidate);
        return (!normalizedSearch || searchable.includes(normalizedSearch))
          && (!reasonFilter || reason.key === reasonFilter);
      })
      .sort((left, right) => {
        if (sortBy === 'name') return candidateLabel(left).localeCompare(candidateLabel(right));
        if (sortBy === 'newest') {
          return new Date(right.source_as_of).getTime() - new Date(left.source_as_of).getTime();
        }
        return candidateReason(left).rank - candidateReason(right).rank
          || candidateLabel(left).localeCompare(candidateLabel(right));
      });
  }, [candidateReason, reasonFilter, search, sortBy, workCandidates]);
  const selectedCandidate = candidates.find((candidate) => candidate.id === selectedCandidateId)
    ?? filteredCandidates[0]
    ?? null;

  useEffect(() => {
    if (
      mode === 'work'
      && filteredCandidates.length > 0
      && !filteredCandidates.some((candidate) => candidate.id === selectedCandidateId)
    ) {
      setSelectedCandidateId(filteredCandidates[0].id);
    }
  }, [filteredCandidates, mode, selectedCandidateId]);

  const selectedCorrespondent = selectedCandidate
    ? correspondentSelections[selectedCandidate.id]
      ?? selectedCandidate.correspondent_id?.toString()
      ?? ''
    : '';
  const correspondentId = selectedCorrespondent ? Number(selectedCorrespondent) : null;
  const availableExpectations = correspondentId
    ? (expectationsByProfile[correspondentId] ?? [])
      .filter((expectation) => expectation.status !== 'retired')
    : [];
  const selectedExpectationIds = selectedCandidate
    ? (expectationSelections[selectedCandidate.id]
      ?? selectedCandidate.expectation_ids
      ?? []).filter((expectationId) =>
      availableExpectations.some((expectation) => expectation.id === expectationId))
    : [];
  const groupedCandidates = filteredCandidates.reduce<Array<{
    key: string;
    label: string;
    candidates: ExternalCandidate[];
  }>>((groups, candidate) => {
    const reason = candidateReason(candidate);
    const existing = groups.find((group) => group.key === reason.key);
    if (existing) existing.candidates.push(candidate);
    else groups.push({ key: reason.key, label: reason.label, candidates: [candidate] });
    return groups;
  }, []);
  const profileName = (correspondentIdValue?: number | null) =>
    profiles.find((profile) => profile.correspondent_id === correspondentIdValue)?.current_name
    ?? 'Not mapped';
  const expectationNames = (candidate: ExternalCandidate) => {
    const profileExpectations = candidate.correspondent_id
      ? expectationsByProfile[candidate.correspondent_id] ?? []
      : [];
    return candidate.expectation_ids.map((expectationId) => {
      const expectation = profileExpectations.find((item) => item.id === expectationId);
      return expectation
        ? redactSensitiveNumbers(expectation.series_discriminator ?? humanize(expectation.kind))
        : 'Unavailable expectation';
    });
  };

  const reviewAndAdvance = async (
    candidate: ExternalCandidate,
    outcome: 'mapped' | 'ambiguous' | 'not_applicable',
  ) => {
    const currentIndex = filteredCandidates.findIndex((item) => item.id === candidate.id);
    const nextCandidate = filteredCandidates[currentIndex + 1] ?? filteredCandidates[currentIndex - 1];
    const succeeded = await onReview(candidate, outcome);
    if (succeeded) {
      setManualReviewIds((current) => {
        const next = new Set(current);
        next.delete(candidate.id);
        return next;
      });
      setSelectedCandidateId(nextCandidate?.id ?? null);
    }
  };

  return (
    <Card>
      <div className="tyrion-reconciliation-header">
        <div>
          <h2>TYRION account reconciliation</h2>
          <p>Resolve new or changed account signals. Reviewed mappings stay in the audit view.</p>
        </div>
        {connection.configured ? (
          <div className="correspondent-actions">
            {connection.last_synced_at ? (
              <span className="correspondent-muted">
                Last synced {formatDate(connection.last_synced_at)}
              </span>
            ) : null}
            <Button variant="primary" disabled={busy} onClick={onSync}>
              {busy ? 'Syncing TYRION…' : 'Sync TYRION accounts'}
            </Button>
          </div>
        ) : null}
      </div>
      {!connection.configured ? (
        <div className="correspondent-callout">
          TYRION is not connected. Configure it in Settings before synchronizing accounts.
        </div>
      ) : null}
      {syncError ? <div className="correspondent-callout warning" role="alert">{syncError}</div> : null}

      <div className="tyrion-view-tabs" role="tablist" aria-label="TYRION reconciliation views">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'work'}
          className={mode === 'work' ? 'active' : ''}
          onClick={() => setMode('work')}
        >
          Needs action <span>{workCandidates.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'audit'}
          className={mode === 'audit' ? 'active' : ''}
          onClick={() => setMode('audit')}
        >
          Mapping audit <span>{reviewedCandidates.length}</span>
        </button>
      </div>

      {mode === 'work' ? (
        <>
          <div className="tyrion-toolbar">
            <input
              aria-label="Search TYRION work queue"
              value={search}
              placeholder="Search account, institution, or last four"
              onChange={(event) => setSearch(event.target.value)}
            />
            <select
              aria-label="Filter TYRION work reason"
              value={reasonFilter}
              onChange={(event) => setReasonFilter(event.target.value)}
            >
              <option value="">All reasons</option>
              <option value="changed">Source changed</option>
              <option value="ambiguous">Unresolved</option>
              <option value="new">New and unassigned</option>
              <option value="missing_series">Missing document series</option>
              <option value="confirm">Ready to confirm</option>
            </select>
            <select
              aria-label="Sort TYRION work queue"
              value={sortBy}
              onChange={(event) => setSortBy(event.target.value as typeof sortBy)}
            >
              <option value="priority">Priority</option>
              <option value="name">Account name</option>
              <option value="newest">Newest source data</option>
            </select>
          </div>

          {workCandidates.length === 0 ? (
            <div className="tyrion-empty">
              <strong>All caught up</strong>
              <span>No new, changed, or unresolved TYRION accounts need review.</span>
            </div>
          ) : filteredCandidates.length === 0 ? (
            <div className="tyrion-empty">
              <strong>No matching work</strong>
              <span>Clear the search or reason filter to see the remaining accounts.</span>
            </div>
          ) : (
            <div className="tyrion-workbench">
              <aside className="tyrion-queue" aria-label="TYRION work queue">
                <div className="tyrion-queue-summary">
                  <strong>Work queue</strong>
                  <span>{filteredCandidates.length} account{filteredCandidates.length === 1 ? '' : 's'}</span>
                </div>
                <div className="tyrion-queue-scroll">
                  {groupedCandidates.map((group) => (
                    <section key={group.key}>
                      <div className="tyrion-group-heading">
                        <span>{group.label}</span><span>{group.candidates.length}</span>
                      </div>
                      {group.candidates.map((candidate) => {
                        const reason = candidateReason(candidate);
                        return (
                          <button
                            type="button"
                            key={candidate.id}
                            className={`tyrion-queue-item ${selectedCandidate?.id === candidate.id ? 'active' : ''}`}
                            aria-pressed={selectedCandidate?.id === candidate.id}
                            onClick={() => setSelectedCandidateId(candidate.id)}
                          >
                            <span>
                              <strong>{candidateLabel(candidate)}</strong>
                              <small>{candidateDetails(candidate)}</small>
                              <small>{reason.label}</small>
                            </span>
                            <span className="tyrion-queue-item-status">
                              {candidate.identifier_match_expectation_ids.length > 0 ? (
                                <Badge tone="info">Identifier hint</Badge>
                              ) : null}
                              <Badge tone={candidate.review_finding ? 'warning' : 'muted'}>
                                {candidate.review_finding ? 'Changed' : humanize(candidate.outcome)}
                              </Badge>
                            </span>
                          </button>
                        );
                      })}
                    </section>
                  ))}
                </div>
              </aside>

              {selectedCandidate ? (
                <section className="tyrion-editor" aria-label={`Review ${selectedCandidate.display_hint}`}>
                  <div className="tyrion-editor-header">
                    <div>
                      <h3>{candidateLabel(selectedCandidate)}</h3>
                      <span>{candidateDetails(selectedCandidate)}</span>
                      <span>Source data as of {formatDate(selectedCandidate.source_as_of)}</span>
                    </div>
                    <div className="correspondent-badges">
                      {!selectedCandidate.active ? <Badge tone="warning">Inactive source</Badge> : null}
                      <Badge tone={selectedCandidate.outcome === 'mapped' ? 'ok' : 'warning'}>
                        {humanize(selectedCandidate.outcome)}
                      </Badge>
                    </div>
                  </div>

                  <div className="tyrion-review-progress" aria-label="Mapping progress">
                    <span className="complete">1. Review account</span>
                    <span className={selectedCorrespondent ? 'complete' : 'active'}>
                      2. Map correspondent
                    </span>
                    <span className={selectedCorrespondent ? 'active' : ''}>
                      3. Link document series
                    </span>
                  </div>

                  <div className="tyrion-editor-body">
                    <div className="correspondent-reconciliation-step">
                      <label>
                        <span>Paperless correspondent</span>
                        <select
                          aria-label={`Paperless correspondent for ${selectedCandidate.display_hint}`}
                          value={selectedCorrespondent}
                          disabled={busy || selectedCandidate.outcome === 'not_applicable'}
                          onChange={(event) =>
                            onCorrespondentChange(selectedCandidate.id, event.target.value)}
                        >
                          <option value="">Not mapped</option>
                          {activeProfiles.map((profile) => (
                            <option key={profile.correspondent_id} value={profile.correspondent_id}>
                              {redactSensitiveNumbers(profile.current_name)}
                            </option>
                          ))}
                        </select>
                      </label>
                      {!selectedCorrespondent && paperlessUrl ? (
                        <a href={`${paperlessUrl}/correspondents`} target="_blank" rel="noreferrer">
                          Create correspondent in Paperless
                        </a>
                      ) : null}
                    </div>

                    {correspondentId ? (
                      <div className="correspondent-reconciliation-step">
                        <div className="correspondent-reconciliation-step-heading">
                          <span>Related document series (select all that apply)</span>
                          <Button size="sm" onClick={() => onOpenCorrespondent(correspondentId)}>
                            Review / identify documents
                          </Button>
                        </div>
                        {availableExpectations.length === 0 ? (
                          <div className="tyrion-empty compact">
                            <strong>No document series found</strong>
                            <span>
                              Analyze this correspondent’s Paperless history, create the required
                              series, then return here to link them.
                            </span>
                          </div>
                        ) : (
                          <div className="correspondent-series-options">
                            {availableExpectations.map((expectation) => {
                              const identifierMatch = selectedCandidate
                                .identifier_match_expectation_ids.includes(expectation.id);
                              return (
                                <label key={expectation.id}>
                                  <input
                                    type="checkbox"
                                    checked={selectedExpectationIds.includes(expectation.id)}
                                    disabled={busy || selectedCandidate.outcome === 'not_applicable'}
                                    onChange={(event) => onExpectationChange(
                                      selectedCandidate.id,
                                      event.target.checked
                                        ? [...selectedExpectationIds, expectation.id]
                                        : selectedExpectationIds.filter((id) => id !== expectation.id),
                                    )}
                                  />
                                  <span>
                                    <strong>
                                      {redactSensitiveNumbers(
                                        expectation.series_discriminator ?? humanize(expectation.kind),
                                      )}
                                    </strong>
                                    {' · '}{humanize(expectation.kind)}
                                    {' · '}{humanize(expectation.expectation_mode)}
                                  </span>
                                  {identifierMatch ? (
                                    <Badge tone="info">Account suffix match</Badge>
                                  ) : null}
                                </label>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    ) : null}

                    <div className="correspondent-callout">
                      Account suffix matches are advisory. TYRION does not establish document
                      existence or cadence. Use stored Account Identifier metadata first, or run
                      scoped account OCR from the correspondent review.
                    </div>
                  </div>

                  <div className="tyrion-editor-footer">
                    <div className="correspondent-actions">
                      <Button
                        disabled={busy || selectedCandidate.outcome === 'not_applicable'}
                        onClick={() => void reviewAndAdvance(selectedCandidate, 'ambiguous')}
                      >
                        Leave unresolved
                      </Button>
                      <Button
                        disabled={
                          busy || !selectedCorrespondent
                          || selectedCandidate.outcome === 'not_applicable'
                        }
                        onClick={() => void reviewAndAdvance(selectedCandidate, 'not_applicable')}
                      >
                        No documents expected
                      </Button>
                    </div>
                    <Button
                      variant="primary"
                      disabled={
                        busy || !selectedCorrespondent
                        || selectedCandidate.outcome === 'not_applicable'
                      }
                      onClick={() => void reviewAndAdvance(selectedCandidate, 'mapped')}
                    >
                      Save and next
                    </Button>
                  </div>
                </section>
              ) : null}
            </div>
          )}
        </>
      ) : (
        <div className="tyrion-audit">
          <div className="tyrion-audit-summary">
            <div>
              <strong>Reviewed mappings</strong>
              <span>Audit established correspondent and document-series relationships.</span>
            </div>
            <span>{reviewedCandidates.length} reviewed</span>
          </div>
          {reviewedCandidates.length === 0 ? (
            <div className="tyrion-empty">
              <strong>No reviewed mappings yet</strong>
              <span>Completed mappings and no-document decisions will appear here.</span>
            </div>
          ) : (
            <div className="tyrion-audit-table-wrap">
              <table className="tyrion-audit-table">
                <thead>
                  <tr>
                    <th>TYRION account</th>
                    <th>Paperless correspondent</th>
                    <th>Related document series</th>
                    <th>Status</th>
                    <th><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {reviewedCandidates.map((candidate) => {
                    const names = expectationNames(candidate);
                    return (
                      <tr key={candidate.id}>
                        <td>
                          <strong>{candidateLabel(candidate)}</strong>
                          <span>{candidateDetails(candidate)}</span>
                        </td>
                        <td>{redactSensitiveNumbers(profileName(candidate.correspondent_id))}</td>
                        <td>
                          {candidate.outcome === 'not_applicable' ? (
                            <span className="tyrion-series-chip muted">No documents expected</span>
                          ) : names.length > 0 ? names.map((name) => (
                            <span className="tyrion-series-chip" key={name}>{name}</span>
                          )) : <span className="correspondent-muted">No series linked</span>}
                        </td>
                        <td>
                          <Badge tone={candidate.outcome === 'mapped' ? 'ok' : 'muted'}>
                            {humanize(candidate.outcome)}
                          </Badge>
                        </td>
                        <td>
                          {candidate.outcome === 'not_applicable' ? (
                            <span className="correspondent-muted">
                              Retire policy to change
                            </span>
                          ) : (
                            <Button
                              size="sm"
                              onClick={() => {
                                setManualReviewIds((current) =>
                                  new Set([...current, candidate.id]));
                                setSearch('');
                                setReasonFilter('');
                                setSelectedCandidateId(candidate.id);
                                setMode('work');
                              }}
                            >
                              Edit mapping
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
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
  const [analysisSummaries, setAnalysisSummaries] = useState<Record<number, AnalysisSummary>>({});
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [policyPreviews, setPolicyPreviews] = useState<Record<string, ExpectationPolicyPreview>>({});
  const [selectedPolicyOperations, setSelectedPolicyOperations] = useState<Record<string, string[]>>({});
  const [policyReasons, setPolicyReasons] = useState<Record<string, string>>({});
  const [policyResults, setPolicyResults] = useState<Record<string, PolicyOperationResult>>({});
  const [undonePolicyOperations, setUndonePolicyOperations] = useState<string[]>([]);
  const [acquisitionSources, setAcquisitionSources] = useState<AcquisitionSource[]>([]);
  const [externalCandidates, setExternalCandidates] = useState<ExternalCandidate[]>([]);
  const [externalConnection, setExternalConnection] = useState<ExternalSignalConnection>({ configured: false });
  const [candidateCorrespondents, setCandidateCorrespondents] = useState<Record<string, string>>({});
  const [candidateExpectations, setCandidateExpectations] = useState<Record<string, string[]>>({});
  const [tags, setTags] = useState<MetadataOption[]>([]);
  const [documentTypes, setDocumentTypes] = useState<MetadataOption[]>([]);
  const [paperlessUrl, setPaperlessUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [externalSyncError, setExternalSyncError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; tone: 'success' | 'error' } | null>(null);
  const [relinkTarget, setRelinkTarget] = useState('');
  const [profileSearch, setProfileSearch] = useState('');
  const [reviewFilter, setReviewFilter] = useState('');
  const [lifecycleFilter, setLifecycleFilter] = useState('');
  const [freshnessFilter, setFreshnessFilter] = useState('');
  const [candidateFilter, setCandidateFilter] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [importanceFilter, setImportanceFilter] = useState('');
  const [inventoryLimit, setInventoryLimit] = useState(100);
  const [candidatePage, setCandidatePage] = useState(0);
  const [acknowledgeUnresolved, setAcknowledgeUnresolved] = useState(false);
  const inventoryButtonRefs = useRef(new Map<number, HTMLButtonElement>());
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
  const loadWorkspace = useCallback(async (showSkeleton = true) => {
    if (showSkeleton) setLoading(true);
    setError(null);
    try {
      const [
        workspacePayload,
        sourcePayload,
        paperlessPayload,
        candidatePayload,
        connectionPayload,
        tagsPayload,
        documentTypesPayload,
      ] = await Promise.all([
        endpoints.statements.correspondentReviewWorkspace() as Promise<{
          profiles: CorrespondentProfile[];
          expectations: DocumentExpectation[];
          analysis_summaries: AnalysisSummary[];
        }>,
        endpoints.statements.acquisitionSources() as Promise<AcquisitionSource[]>,
        endpoints.statements.paperlessUrl() as Promise<{ paperless_url?: string | null }>,
        endpoints.statements.externalCandidates() as Promise<ExternalCandidate[]>,
        endpoints.statements.externalCandidateConnection() as Promise<ExternalSignalConnection>,
        endpoints.actionQueue.metadataTags() as Promise<{ tags?: Array<{ id: number; name: string }> }>,
        endpoints.actionQueue.metadataDocumentTypes() as Promise<{ document_types?: Array<{ id: number; name: string }> }>,
      ]);
      setProfiles(workspacePayload.profiles);
      setAcquisitionSources(sourcePayload);
      setTags((tagsPayload.tags ?? []).map((tag) => ({ value: String(tag.id), label: tag.name })));
      setDocumentTypes((documentTypesPayload.document_types ?? []).map((documentType) => ({
        value: String(documentType.id),
        label: documentType.name,
      })));
      setPaperlessUrl((paperlessPayload.paperless_url ?? '').replace(/\/$/, ''));
      setExternalCandidates(candidatePayload);
      setExternalConnection(connectionPayload);
      setExpectationsByProfile(workspacePayload.expectations.reduce<Record<number, DocumentExpectation[]>>(
        (groups, expectation) => {
          (groups[expectation.correspondent_id] ??= []).push(expectation);
          return groups;
        },
        {},
      ));
      setAnalysisSummaries(Object.fromEntries(
        workspacePayload.analysis_summaries.map((summary) => [summary.correspondent_id, summary]),
      ));
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
    setPolicyPreviews({});
    setSelectedPolicyOperations({});
    setPolicyReasons({});
    setPolicyResults({});
    setUndonePolicyOperations([]);
    setRelinkTarget('');
    setAcknowledgeUnresolved(false);
    setCandidatePage(0);
    if (!selectedId) {
      setAnalysis(null);
      return;
    }
    setDetailLoading(true);
    void (async () => {
      try {
        const snapshot = await endpoints.statements.correspondentAnalysisSnapshot(
          selectedId,
        ) as AnalysisSnapshot | null;
        if (selectedIdRef.current === selectedId) {
          setAnalysis(snapshot?.result ?? null);
        }
      } catch (requestError) {
        if (selectedIdRef.current === selectedId) {
          setToast({ message: getErrorMessage(requestError), tone: 'error' });
        }
      } finally {
        if (selectedIdRef.current === selectedId) setDetailLoading(false);
      }
    })();
  }, [selectedId]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), getToastDuration(toast.tone));
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const filteredProfiles = useMemo(() => {
    const query = profileSearch.trim().toLocaleLowerCase();
    return profiles.filter((profile) => {
      const expectations = expectationsByProfile[profile.correspondent_id] ?? [];
      const summary = analysisSummaries[profile.correspondent_id];
      return (!query || profile.current_name.toLocaleLowerCase().includes(query))
        && (!reviewFilter || profile.review_status === reviewFilter)
        && (!lifecycleFilter || profile.lifecycle_status === lifecycleFilter)
        && (!freshnessFilter || (
          freshnessFilter === 'missing'
            ? !summary
            : summary?.freshness === freshnessFilter
        ))
        && (!candidateFilter || (
          candidateFilter === 'unresolved'
            ? (summary?.unresolved_suggestion_count ?? 0) > 0
            : (summary?.unresolved_suggestion_count ?? 0) === 0
        ))
        && (!modeFilter || expectations.some((expectation) =>
          expectation.expectation_mode === modeFilter))
        && (!importanceFilter || expectations.some((expectation) =>
          expectation.importance === importanceFilter));
    });
  }, [
    analysisSummaries,
    candidateFilter,
    expectationsByProfile,
    freshnessFilter,
    importanceFilter,
    lifecycleFilter,
    modeFilter,
    profileSearch,
    profiles,
    reviewFilter,
  ]);
  const sortedProfiles = useMemo(
    () => [...filteredProfiles].sort((left, right) => {
      const leftPriority = profilePriority(left, expectationsByProfile[left.correspondent_id]?.length ?? 0);
      const rightPriority = profilePriority(right, expectationsByProfile[right.correspondent_id]?.length ?? 0);
      return leftPriority.rank - rightPriority.rank || left.current_name.localeCompare(right.current_name);
    }),
    [filteredProfiles, expectationsByProfile],
  );
  const profileGroups = useMemo(
    () => sortedProfiles.slice(0, inventoryLimit).reduce<Array<{
      rank: number;
      label: string;
      profiles: CorrespondentProfile[];
    }>>((groups, profile) => {
      const priority = profilePriority(
        profile,
        expectationsByProfile[profile.correspondent_id]?.length ?? 0,
      );
      const currentGroup = groups.at(-1);
      if (currentGroup?.rank === priority.rank) {
        currentGroup.profiles.push(profile);
      } else {
        groups.push({ rank: priority.rank, label: priority.label, profiles: [profile] });
      }
      return groups;
    }, []),
    [expectationsByProfile, inventoryLimit, sortedProfiles],
  );
  const selectedSummary = selectedId ? analysisSummaries[selectedId] : undefined;
  const unresolvedSuggestionCount = selectedSummary?.unresolved_suggestion_count
    ?? (analysis?.suggestions.length ?? 0);
  const importanceOptions: Importance[] = ['routine', 'important', 'critical'];
  const currentFilteredIndex = sortedProfiles.findIndex(
    (profile) => profile.correspondent_id === selectedId,
  );
  const candidatePageSize = 20;
  const candidatePageCount = analysis
    ? Math.max(1, Math.ceil(analysis.suggestions.length / candidatePageSize))
    : 1;
  const candidatePageSuggestions = analysis?.suggestions.slice(
    candidatePage * candidatePageSize,
    (candidatePage + 1) * candidatePageSize,
  ) ?? [];
  const navigateRelative = (offset: number) => {
    const profile = sortedProfiles[currentFilteredIndex + offset];
    if (profile) navigate(`/correspondents/${profile.correspondent_id}`);
  };
  const focusInventorySibling = (profileId: number, offset: number) => {
    const index = sortedProfiles.findIndex((profile) => profile.correspondent_id === profileId);
    const target = sortedProfiles[index + offset];
    if (!target) return;
    if (index + offset >= inventoryLimit) setInventoryLimit((current) => current + 100);
    navigate(`/correspondents/${target.correspondent_id}`);
    window.setTimeout(() => inventoryButtonRefs.current.get(target.correspondent_id)?.focus(), 0);
  };
  const runAction = useCallback(async (action: () => Promise<unknown>, successMessage: string) => {
    setBusy(true);
    try {
      await action();
      setToast({ message: successMessage, tone: 'success' });
      await loadWorkspace(false);
      return true;
    } catch (requestError) {
      setToast({ message: getErrorMessage(requestError), tone: 'error' });
      return false;
    } finally {
      setBusy(false);
    }
  }, [loadWorkspace]);

  const analyzeSelected = useCallback(async (extractMissingAccountIdentifiers = false) => {
    if (!selectedId) return;
    setDetailLoading(true);
    try {
      const result = await endpoints.statements.analyzeCorrespondentProfile(
        selectedId,
        extractMissingAccountIdentifiers,
      ) as AnalysisResult;
      if (selectedIdRef.current !== result.correspondent_id) return;
      setAnalysis(result);
      const extractionDetail = extractMissingAccountIdentifiers
        ? ` ${result.account_identifiers.extracted_document_count} identifiers extracted; ${result.account_identifiers.unresolved_document_count} documents remain unresolved.`
        : '';
      setToast({
        message: `${extractMissingAccountIdentifiers ? 'OCR-enhanced analysis found' : 'Analyzed'} ${result.suggestions.length} candidate series.${extractionDetail}`,
        tone: 'success',
      });
      await loadWorkspace(false);
      const snapshot = await endpoints.statements.correspondentAnalysisSnapshot(
        selectedId,
      ) as AnalysisSnapshot | null;
      if (selectedIdRef.current === selectedId && snapshot) setAnalysis(snapshot.result);
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
      setToast({
        message: status === 'confirmed'
          ? 'Expectation confirmed. Continue reviewing the remaining suggestions.'
          : 'Suggestion dismissed. Continue reviewing the remaining suggestions.',
        tone: 'success',
      });
      await loadWorkspace(false);
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
    outcome: 'mapped' | 'ambiguous' | 'not_applicable',
  ) => {
    const body: Record<string, unknown> = { outcome };
    const correspondentId = Number(
      candidateCorrespondents[candidate.id] ?? candidate.correspondent_id ?? 0,
    );
    if (outcome === 'mapped') {
      body.correspondent_id = correspondentId;
      body.expectation_ids = candidateExpectations[candidate.id] ?? candidate.expectation_ids ?? [];
    }
    if (outcome === 'not_applicable') {
      body.correspondent_id = correspondentId;
    }
    const succeeded = await runAction(
      () => endpoints.statements.reviewExternalCandidate(candidate.id, body),
      outcome === 'not_applicable'
        ? 'Not-expected policy recorded.'
        : outcome === 'ambiguous'
          ? 'Account left unresolved.'
          : 'Account mapping saved.',
    );
    if (succeeded) {
      setCandidateCorrespondents((current) => {
        const next = { ...current };
        delete next[candidate.id];
        return next;
      });
      setCandidateExpectations((current) => {
        const next = { ...current };
        delete next[candidate.id];
        return next;
      });
    }
    return succeeded;
  }, [candidateCorrespondents, candidateExpectations, runAction]);

  const syncExternalCandidates = useCallback(async () => {
    setBusy(true);
    setExternalSyncError(null);
    try {
      const result = await endpoints.statements.syncExternalCandidates() as {
        active_candidates: number;
        deactivated_candidates: number;
        idempotent: boolean;
      };
      setToast({
        message: result.idempotent
          ? 'Tyrion is already up to date.'
          : `Synchronized ${result.active_candidates} active candidate${result.active_candidates === 1 ? '' : 's'}${result.deactivated_candidates ? `; ${result.deactivated_candidates} deactivated` : ''}.`,
        tone: 'success',
      });
      await loadWorkspace(false);
    } catch (requestError) {
      const message = getErrorMessage(requestError);
      setExternalSyncError(message);
      setToast({ message, tone: 'error' });
    } finally {
      setBusy(false);
    }
  }, [loadWorkspace]);

  const previewExpectation = useCallback(async (expectationId: string) => {
    setBusy(true);
    try {
      const preview = await endpoints.statements.previewDocumentExpectationPolicy(
        expectationId,
      ) as ExpectationPolicyPreview;
      setPolicyPreviews((current) => ({ ...current, [expectationId]: preview }));
      setSelectedPolicyOperations((current) => ({ ...current, [expectationId]: [] }));
      setPolicyResults((current) => {
        const next = { ...current };
        preview.findings.forEach((finding) => delete next[finding.preview_id]);
        return next;
      });
      setToast({
        message: preview.findings.length === 0
          ? 'All matched documents satisfy this policy.'
          : `Found ${preview.findings.length} policy violation previews.`,
        tone: 'success',
      });
    } catch (requestError) {
      setToast({ message: getErrorMessage(requestError), tone: 'error' });
    } finally {
      setBusy(false);
    }
  }, []);

  const applySelectedPolicy = useCallback(async (expectationId: string) => {
    const preview = policyPreviews[expectationId];
    const selected = new Set(selectedPolicyOperations[expectationId] ?? []);
    const reason = policyReasons[expectationId]?.trim() ?? '';
    if (!preview || selected.size === 0 || !reason) return;
    setBusy(true);
    try {
      const response = await endpoints.statements.applyDocumentExpectationPolicy(
        expectationId,
        {
          actor: 'user',
          reason,
          operations: preview.findings
            .filter((finding) => selected.has(finding.preview_id))
            .map((finding) => ({
              preview_id: finding.preview_id,
              operation: finding.operation,
            })),
        },
      ) as { results: PolicyOperationResult[] };
      setPolicyResults((current) => ({
        ...current,
        ...Object.fromEntries(response.results.map((result) => [result.preview_id, result])),
      }));
      const succeeded = response.results.filter((result) => result.status === 'succeeded').length;
      const failed = response.results.length - succeeded;
      setToast({
        message: `${succeeded} correction${succeeded === 1 ? '' : 's'} applied${failed ? `; ${failed} failed` : ''}.`,
        tone: failed ? 'error' : 'success',
      });
    } catch (requestError) {
      setToast({ message: getErrorMessage(requestError), tone: 'error' });
    } finally {
      setBusy(false);
    }
  }, [policyPreviews, policyReasons, selectedPolicyOperations]);

  const undoPolicyCorrection = useCallback(async (
    finding: PolicyViolationFinding,
    result: PolicyOperationResult,
  ) => {
    if (!result.audit_event_id) return;
    setBusy(true);
    try {
      const undoResult = await endpoints.statements.undoDocumentExpectationPolicy(
        result.audit_event_id,
        {
          actor: 'user',
          reason: `Undo: ${result.message}`,
          preview_id: finding.preview_id,
          operation: finding.operation,
        },
      ) as PolicyOperationResult;
      if (undoResult.status === 'succeeded') {
        setUndonePolicyOperations((current) => [...current, finding.preview_id]);
      }
      setToast({ message: undoResult.message, tone: undoResult.status === 'succeeded' ? 'success' : 'error' });
    } catch (requestError) {
      setToast({ message: getErrorMessage(requestError), tone: 'error' });
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div className="correspondent-page">
      <div className="correspondent-page-header">
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
                Reanalyze all
              </Button>
            </div>
          }
        />
      </div>

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
        <>
        <div className="correspondent-reconciliation">
          <ExternalCandidateReconciliation
            candidates={externalCandidates}
            profiles={profiles}
            expectationsByProfile={expectationsByProfile}
            connection={externalConnection}
            syncError={externalSyncError}
            paperlessUrl={paperlessUrl}
            busy={busy}
            correspondentSelections={candidateCorrespondents}
            expectationSelections={candidateExpectations}
            onCorrespondentChange={(candidateId, correspondentId) => {
              setCandidateCorrespondents((current) => ({
                ...current,
                [candidateId]: correspondentId,
              }));
              setCandidateExpectations((current) => ({ ...current, [candidateId]: [] }));
            }}
            onExpectationChange={(candidateId, expectationIds) =>
              setCandidateExpectations((current) => ({
                ...current,
                [candidateId]: expectationIds,
              }))}
            onReview={reviewCandidate}
            onSync={() => void syncExternalCandidates()}
            onOpenCorrespondent={(correspondentId) => navigate(`/correspondents/${correspondentId}`)}
          />
        </div>
        <div className="correspondent-workspace">
          <aside className="correspondent-inventory" aria-label="Correspondent inventory">
            <div className="correspondent-inventory-header">
              <div>
                <strong>Review queue</strong>
                <span>{profiles.filter((profile) => profile.review_status === 'unreviewed').length} unreviewed</span>
              </div>
              <span>{sortedProfiles.length} shown</span>
            </div>
            <div className="correspondent-inventory-filters">
              <label className="correspondent-search">
                <span className="sr-only">Search correspondents</span>
                <input
                  value={profileSearch}
                  placeholder="Search correspondents"
                  onChange={(event) => {
                    setProfileSearch(event.target.value);
                    setInventoryLimit(100);
                  }}
                />
              </label>
              <select
                aria-label="Filter by review status"
                value={reviewFilter}
                onChange={(event) => setReviewFilter(event.target.value)}
              >
                <option value="">All review states</option>
                <option value="unreviewed">Unreviewed</option>
                <option value="reviewed">Reviewed</option>
                <option value="ignored">Ignored</option>
              </select>
              <select
                aria-label="Filter by lifecycle"
                value={lifecycleFilter}
                onChange={(event) => setLifecycleFilter(event.target.value)}
              >
                <option value="">All lifecycle states</option>
                <option value="active">Active</option>
                <option value="orphaned">Orphaned</option>
                <option value="retired">Retired</option>
              </select>
              <select
                aria-label="Filter by analysis freshness"
                value={freshnessFilter}
                onChange={(event) => setFreshnessFilter(event.target.value)}
              >
                <option value="">Any analysis age</option>
                <option value="fresh">Fresh</option>
                <option value="stale">Stale</option>
                <option value="missing">Not analyzed</option>
              </select>
              <select
                aria-label="Filter by candidate state"
                value={candidateFilter}
                onChange={(event) => setCandidateFilter(event.target.value)}
              >
                <option value="">Any candidate state</option>
                <option value="unresolved">Has unresolved candidates</option>
                <option value="resolved">Candidates resolved</option>
              </select>
              <select
                aria-label="Filter by expectation mode"
                value={modeFilter}
                onChange={(event) => setModeFilter(event.target.value)}
              >
                <option value="">Any expectation mode</option>
                {MODE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
              <select
                aria-label="Filter by importance"
                value={importanceFilter}
                onChange={(event) => setImportanceFilter(event.target.value)}
              >
                <option value="">Any importance</option>
                {importanceOptions.map((importance) => (
                  <option key={importance} value={importance}>{humanize(importance)}</option>
                ))}
              </select>
            </div>
            <div className="correspondent-inventory-list">
              {sortedProfiles.length === 0 ? (
                <div className="tyrion-empty compact">
                  <strong>No matching correspondents</strong>
                  <span>Clear one or more filters to restore the queue.</span>
                </div>
              ) : null}
              {profileGroups.map((group) => (
                <section className="correspondent-inventory-group" key={group.rank}>
                  <div className="correspondent-inventory-group-header">
                    <span>{group.label}</span>
                    <span>{group.profiles.length}</span>
                  </div>
                  {group.profiles.map((profile) => {
                    const expectations = expectationsByProfile[profile.correspondent_id]?.length ?? 0;
                    const priority = profilePriority(profile, expectations);
                    return (
                      <button
                        key={profile.correspondent_id}
                        type="button"
                        className={`correspondent-inventory-item ${selectedId === profile.correspondent_id ? 'active' : ''}`}
                        onClick={() => navigate(`/correspondents/${profile.correspondent_id}`)}
                        onKeyDown={(event) => {
                          if (event.key === 'ArrowDown') {
                            event.preventDefault();
                            focusInventorySibling(profile.correspondent_id, 1);
                          }
                          if (event.key === 'ArrowUp') {
                            event.preventDefault();
                            focusInventorySibling(profile.correspondent_id, -1);
                          }
                        }}
                        ref={(node) => {
                          if (node) inventoryButtonRefs.current.set(profile.correspondent_id, node);
                          else inventoryButtonRefs.current.delete(profile.correspondent_id);
                        }}
                        aria-current={selectedId === profile.correspondent_id ? 'true' : undefined}
                      >
                        <div>
                          <strong>{redactSensitiveNumbers(profile.current_name)}</strong>
                          <span>
                            {profile.observed_summary.document_count} docs · {expectations} expectations
                            {(analysisSummaries[profile.correspondent_id]?.unresolved_suggestion_count ?? 0) > 0
                              ? ` · ${analysisSummaries[profile.correspondent_id].unresolved_suggestion_count} unresolved`
                              : ''}
                          </span>
                        </div>
                        <Badge tone={priority.tone}>{priority.label}</Badge>
                      </button>
                    );
                  })}
                </section>
              ))}
              {inventoryLimit < sortedProfiles.length ? (
                <div className="correspondent-load-more">
                  <Button onClick={() => setInventoryLimit((current) => current + 100)}>
                    Load 100 more
                  </Button>
                </div>
              ) : null}
            </div>
          </aside>

          <section className="correspondent-detail" aria-busy={detailLoading}>
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
                        disabled={detailLoading || busy || selectedProfileTerminal}
                        title="Extract masked account identifiers from OCR for this analysis only"
                        onClick={() => void analyzeSelected(true)}
                      >
                        Reanalyze with account OCR
                      </Button>
                      <Button
                        variant="primary"
                        disabled={detailLoading || busy || selectedProfileTerminal}
                        onClick={() => void analyzeSelected(false)}
                      >
                        {detailLoading ? 'Reanalyzing…' : 'Reanalyze'}
                      </Button>
                    </div>
                  }
                >
                  <div className="correspondent-analysis-status" role="status" aria-live="polite">
                    <div>
                      <strong>{formatSnapshotAge(selectedSummary?.analyzed_at ?? selectedProfile.last_analyzed_at)}</strong>
                      <span>
                        {selectedSummary?.stale_reason
                          ?? selectedSummary?.reason
                          ?? 'Reanalyze after Paperless history or grouping changes.'}
                      </span>
                    </div>
                    {detailLoading ? <span className="correspondent-inline-loading">Updating analysis…</span> : null}
                  </div>
                  <div className="correspondent-summary-grid">
                    <div><span>Documents</span><strong>{selectedProfile.observed_summary.document_count}</strong></div>
                    <div><span>Candidate series</span><strong>{selectedProfile.observed_summary.candidate_series_count}</strong></div>
                    <div><span>Title patterns</span><strong>{selectedProfile.observed_summary.title_pattern_count}</strong></div>
                    <div><span>Last analyzed</span><strong>{formatDate(selectedProfile.last_analyzed_at)}</strong></div>
                  </div>
                  <div className="correspondent-review-navigation">
                    <div>
                      <strong>
                        {profiles.filter((profile) => profile.review_status === 'reviewed').length}
                        {' of '}
                        {profiles.length} profiles reviewed
                      </strong>
                      <span>{unresolvedSuggestionCount} current suggestions unresolved</span>
                    </div>
                    <div className="correspondent-actions">
                      <Button
                        size="sm"
                        disabled={currentFilteredIndex <= 0}
                        onClick={() => navigateRelative(-1)}
                      >
                        Previous
                      </Button>
                      <Button
                        size="sm"
                        disabled={currentFilteredIndex < 0 || currentFilteredIndex >= sortedProfiles.length - 1}
                        onClick={() => navigateRelative(1)}
                      >
                        Next in queue
                      </Button>
                    </div>
                  </div>
                  <div className="correspondent-actions correspondent-profile-actions">
                    {unresolvedSuggestionCount > 0 ? (
                      <label className="correspondent-acknowledgement">
                        <input
                          type="checkbox"
                          checked={acknowledgeUnresolved}
                          onChange={(event) => setAcknowledgeUnresolved(event.target.checked)}
                        />
                        Mark reviewed with {unresolvedSuggestionCount} unresolved suggestion
                        {unresolvedSuggestionCount === 1 ? '' : 's'}
                      </label>
                    ) : null}
                    <Button
                      variant="success"
                      disabled={
                        busy
                        || selectedProfile.review_status === 'reviewed'
                        || selectedProfileTerminal
                        || (unresolvedSuggestionCount > 0 && !acknowledgeUnresolved)
                      }
                      onClick={() => void runAction(
                        () => endpoints.statements.updateCorrespondentProfile(selectedProfile.correspondent_id, {
                          review_status: 'reviewed',
                          last_reviewed_at: new Date().toISOString(),
                          acknowledge_unresolved_suggestions: acknowledgeUnresolved,
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

                <nav className="correspondent-section-nav" aria-label="Correspondent sections">
                  <button type="button" onClick={() => scrollToSection('correspondent-expectations')}>
                    Expectations <span>{selectedExpectations.length}</span>
                  </button>
                  {analysis?.correspondent_id === selectedId ? (
                    <button type="button" onClick={() => scrollToSection('correspondent-candidate-expectations')}>
                      Candidate expectations <span>{analysis.suggestions.length}</span>
                    </button>
                  ) : null}
                  <button type="button" onClick={() => scrollToSection('correspondent-acquisition')}>
                    Acquisition
                  </button>
                </nav>

                <div id="correspondent-expectations" className="correspondent-section-anchor">
                <Card title={`Expectations (${selectedExpectations.length})`}>
                  {selectedExpectations.length === 0 ? (
                    <div className="correspondent-muted">No reviewed expectations yet.</div>
                  ) : (
                    <div className="correspondent-expectation-list">
                      {selectedExpectations.map((expectation) => {
                        const preview = policyPreviews[expectation.id];
                        return (
                          <div className="correspondent-expectation-item" key={expectation.id}>
                            <div className="correspondent-expectation">
                              <div>
                                <strong>{redactSensitiveNumbers(expectation.series_discriminator ?? humanize(expectation.kind))}</strong>
                                <span>
                                  {humanize(expectation.expectation_mode)}
                                  {' · '}{humanize(expectation.importance ?? 'routine')}
                                  {' · '}{humanize(expectation.monitoring_mode ?? 'none')} monitoring
                                </span>
                                {expectation.calendar_rule ? (
                                  <span>
                                    {expectation.calendar_rule.rule_type === 'one_off'
                                      ? `${expectation.calendar_rule.occurrence_id} · ${humanize(expectation.calendar_rule.occurrence_status ?? 'pending')}`
                                      : expectation.calendar_rule.rule_type === 'quarter'
                                        ? `Expected Q${expectation.calendar_rule.expected_quarter}`
                                        : `Expected month ${expectation.calendar_rule.expected_month}`}
                                  </span>
                                ) : (
                                  <span>{expectation.cadence?.frequency ?? 'No calendar rule'}</span>
                                )}
                              </div>
                              <div className="correspondent-actions">
                                <Badge tone={expectation.status === 'confirmed' ? 'ok' : expectation.status === 'dismissed' ? 'muted' : 'warning'}>
                                  {humanize(expectation.status)}
                                </Badge>
                                {expectation.status === 'confirmed' && expectation.expectation_mode !== 'not_expected' ? (
                                  <Button
                                    size="sm"
                                    disabled={busy || selectedProfileTerminal}
                                    onClick={() => void previewExpectation(expectation.id)}
                                  >
                                    Preview violations
                                  </Button>
                                ) : null}
                                {expectation.status === 'confirmed'
                                  && expectation.calendar_rule?.rule_type === 'one_off'
                                  && expectation.calendar_rule.occurrence_status !== 'fulfilled'
                                  && expectation.calendar_rule.occurrence_status !== 'cancelled' ? (
                                    <>
                                      <Button
                                        size="sm"
                                        disabled={busy || selectedProfileTerminal}
                                        onClick={() => void runAction(
                                          () => endpoints.statements.updateDocumentExpectation(
                                            expectation.id,
                                            {
                                              calendar_rule: {
                                                ...expectation.calendar_rule,
                                                occurrence_status: 'fulfilled',
                                                fulfilled_at: new Date().toISOString(),
                                              },
                                            },
                                          ),
                                          'One-off expectation fulfilled.',
                                        )}
                                      >
                                        Mark fulfilled
                                      </Button>
                                      {expectation.calendar_rule.occurrence_status === 'pending' ? (
                                        <Button
                                          size="sm"
                                          disabled={busy || selectedProfileTerminal}
                                          onClick={() => void runAction(
                                            () => endpoints.statements.updateDocumentExpectation(
                                              expectation.id,
                                              {
                                                calendar_rule: {
                                                  ...expectation.calendar_rule,
                                                  occurrence_status: 'overdue',
                                                },
                                              },
                                            ),
                                            'One-off expectation marked overdue.',
                                          )}
                                        >
                                          Mark overdue
                                        </Button>
                                      ) : null}
                                      <Button
                                        size="sm"
                                        disabled={busy || selectedProfileTerminal}
                                        onClick={() => void runAction(
                                          () => endpoints.statements.updateDocumentExpectation(
                                            expectation.id,
                                            {
                                              calendar_rule: {
                                                ...expectation.calendar_rule,
                                                occurrence_status: 'cancelled',
                                              },
                                            },
                                          ),
                                          'One-off expectation cancelled.',
                                        )}
                                      >
                                        Cancel occurrence
                                      </Button>
                                    </>
                                  ) : null}
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
                            {preview ? (
                              <div className="correspondent-policy-preview">
                                <div className="correspondent-preview-header">
                                  <strong>{preview.findings.length} violations</strong>
                                  <span>{preview.compliant_document_count} of {preview.matched_document_count} compliant</span>
                                </div>
                                {preview.findings.map((finding) => {
                                  const actionable = Object.keys(finding.operation.patch).length > 0;
                                  const selected = (selectedPolicyOperations[expectation.id] ?? []).includes(finding.preview_id);
                                  const result = policyResults[finding.preview_id];
                                  return (
                                  <div className="correspondent-policy-finding" key={finding.preview_id}>
                                    <label className="correspondent-policy-select">
                                      <input
                                        type="checkbox"
                                        checked={selected}
                                        disabled={busy || !actionable || result?.status === 'succeeded'}
                                        onChange={(event) => setSelectedPolicyOperations((current) => ({
                                          ...current,
                                          [expectation.id]: event.target.checked
                                            ? [...(current[expectation.id] ?? []), finding.preview_id]
                                            : (current[expectation.id] ?? []).filter((id) => id !== finding.preview_id),
                                        }))}
                                      />
                                      Select document {finding.operation.document_id}
                                    </label>
                                    <div className="correspondent-badges">
                                      {finding.violations.map((violation) => (
                                        <Badge
                                          key={violation}
                                          tone={finding.unresolved_violations.includes(violation) ? 'warning' : 'info'}
                                        >
                                          {humanize(violation)}
                                        </Badge>
                                      ))}
                                    </div>
                                    <div className="correspondent-policy-change">
                                      <span>Title</span>
                                      <del>{finding.operation.expected.title}</del>
                                      <strong>{finding.proposed.title}</strong>
                                    </div>
                                    <div className="correspondent-policy-change">
                                      <span>Tags</span>
                                      <del>{formatMetadataList(finding.operation.expected.tag_names)}</del>
                                      <strong>{formatMetadataList(finding.proposed.tag_names)}</strong>
                                    </div>
                                    <div className="correspondent-policy-change">
                                      <span>Document type</span>
                                      <del>{finding.operation.expected.document_type_name ?? 'None'}</del>
                                      <strong>{finding.proposed.document_type_name ?? 'None'}</strong>
                                    </div>
                                    {finding.missing_title_fields.length > 0 ? (
                                      <div className="correspondent-callout warning">
                                        Missing title fields: {finding.missing_title_fields.join(', ')}
                                      </div>
                                    ) : null}
                                    {paperlessUrl ? (
                                      <a
                                        href={`${paperlessUrl}/documents/${finding.operation.document_id}/details`}
                                        target="_blank"
                                        rel="noreferrer"
                                      >
                                        Open document in Paperless
                                      </a>
                                    ) : null}
                                    {result ? (
                                      <div className={`correspondent-policy-result ${result.status}`}>
                                        <span>{result.message}</span>
                                        {result.audit_event_id
                                          && (result.status === 'succeeded' || result.error_code === 'audit_finalize_failed')
                                          && !undonePolicyOperations.includes(finding.preview_id) ? (
                                          <Button
                                            size="sm"
                                            disabled={busy}
                                            onClick={() => void undoPolicyCorrection(finding, result)}
                                          >
                                            Undo correction
                                          </Button>
                                        ) : null}
                                      </div>
                                    ) : null}
                                  </div>
                                  );
                                })}
                                <div className="correspondent-policy-apply">
                                  <label>
                                    <span>Reason for applying selected corrections</span>
                                    <input
                                      aria-label={`Reason for ${expectation.series_discriminator ?? expectation.kind}`}
                                      value={policyReasons[expectation.id] ?? ''}
                                      maxLength={200}
                                      onChange={(event) => setPolicyReasons((current) => ({
                                        ...current,
                                        [expectation.id]: event.target.value,
                                      }))}
                                    />
                                  </label>
                                  <Button
                                    variant="primary"
                                    disabled={
                                      busy
                                      || !(policyReasons[expectation.id]?.trim())
                                      || (selectedPolicyOperations[expectation.id]?.length ?? 0) === 0
                                    }
                                    onClick={() => void applySelectedPolicy(expectation.id)}
                                  >
                                    Apply selected
                                  </Button>
                                </div>
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Card>
                </div>

                {analysis?.correspondent_id === selectedId && !selectedProfileTerminal ? (
                  <div id="correspondent-candidate-expectations" className="correspondent-section-anchor">
                  <Card
                    title={`Candidate expectations (${analysis.suggestions.length})`}
                    actions={analysis.suggestions.length > candidatePageSize ? (
                      <div className="correspondent-candidate-pagination" aria-label="Candidate pages">
                        <Button
                          size="sm"
                          disabled={candidatePage === 0}
                          onClick={() => setCandidatePage((page) => Math.max(0, page - 1))}
                        >
                          Previous candidates
                        </Button>
                        <span>Page {candidatePage + 1} of {candidatePageCount}</span>
                        <Button
                          size="sm"
                          disabled={candidatePage >= candidatePageCount - 1}
                          onClick={() => setCandidatePage((page) =>
                            Math.min(candidatePageCount - 1, page + 1))}
                        >
                          Next candidates
                        </Button>
                      </div>
                    ) : null}
                  >
                    {analysis.suggestions.length === 0 ? (
                      <EmptyState icon="✓" title="No policy candidates" desc="Paperless history did not contain enough bounded evidence for a suggestion." />
                    ) : (
                      <div className="correspondent-suggestions">
                        {candidatePageSuggestions.map((suggestion) => (
                          <SuggestionCard
                            key={suggestion.suggestion_key}
                            suggestion={suggestion}
                            acquisitionSources={acquisitionSources}
                            tags={tags}
                            documentTypes={documentTypes}
                            paperlessUrl={paperlessUrl}
                            busy={busy}
                            onDecision={(candidate, draft, status) => void decideSuggestion(candidate, draft, status)}
                          />
                        ))}
                      </div>
                    )}
                  </Card>
                  </div>
                ) : null}

                <div id="correspondent-acquisition" className="correspondent-section-anchor">
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
                </div>
              </>
            )}
          </section>
        </div>
        </>
      ) : null}

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}
    </div>
  );
}
