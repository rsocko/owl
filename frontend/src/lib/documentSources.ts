export type SourceDisposition =
  | 'expectations_configured'
  | 'no_series_expected'
  | 'waiting_for_history'
  | 'ignored_retired';
export type ReopenReason =
  | 'new_source'
  | 'new_document'
  | 'repeatable_pattern_detected'
  | 'external_relationship_proposed'
  | 'series_membership_changed'
  | 'metadata_changed'
  | 'correspondent_orphaned'
  | 'correspondent_relinked'
  | 'waiting_revisit_due'
  | 'manual';
export type DocumentKind =
  | 'statement'
  | 'invoice'
  | 'bill'
  | 'receipt'
  | 'tax_document'
  | 'renewal'
  | 'record'
  | 'other';
export type ExpectationMode = 'recurring' | 'periodic' | 'one_off' | 'irregular' | 'not_expected';
export type CoverageState =
  | 'upcoming'
  | 'due_soon'
  | 'missing'
  | 'fulfilled'
  | 'fulfilled_late'
  | 'unmonitored'
  | 'cancelled';

export interface DocumentSource {
  correspondent_id: number;
  current_name: string;
  review_state: 'needs_review' | 'resolved';
  disposition: SourceDisposition | null;
  lifecycle: 'active' | 'orphaned' | 'retired';
  reopen_reasons: ReopenReason[];
  waiting_revisit_date?: string | null;
  aliases: string[];
  notes?: string | null;
  last_evidence_at?: string | null;
  last_reviewed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentSeries {
  id: string;
  source_correspondent_id: number;
  name: string;
  kind: DocumentKind;
  discriminator?: string | null;
  document_ids: number[];
  membership_revision: number;
  created_at: string;
  updated_at: string;
}

export interface SeriesAnalysisSuggestion {
  suggestion_key: string;
  kind: DocumentKind;
  series_discriminator: string;
  statement_series_id?: string | null;
  expectation_mode: ExpectationMode | 'unknown';
  document_ids: number[];
  sample_document_ids: number[];
  evidence: {
    confidence?: number | null;
    reason_codes: string[];
    sample_size: number;
  };
}

export interface SeriesAnalysisResult {
  correspondent_id: number;
  correspondent_name: string;
  analyzed_at: string;
  observed_summary: {
    document_count: number;
    candidate_series_count: number;
  };
  suggestions: SeriesAnalysisSuggestion[];
}

export interface DocumentExpectation {
  id: string;
  source_correspondent_id: number;
  series_id?: string | null;
  name: string;
  kind: DocumentKind;
  expectation_mode: ExpectationMode;
  status: 'suggested' | 'confirmed' | 'dismissed' | 'retired';
  cadence?: {
    frequency: 'monthly' | 'quarterly' | 'annual';
    expected_day?: number | null;
    availability_delay_days: number;
    grace_period_days: number;
  } | null;
  calendar_rule?: {
    rule_type: string;
    occurrence_id?: string | null;
    due_start?: string | null;
    due_end?: string | null;
    occurrence_status?: string | null;
  } | null;
  importance: 'routine' | 'important' | 'critical';
  monitoring_mode: 'none' | 'presence' | 'deadline';
  grace_period_days: number;
  fulfillment_rule?: {
    match_series: boolean;
    match_kind: boolean;
    match_calendar_period: boolean;
    required_tag_ids: number[];
    required_document_type_id?: number | null;
  };
  title_convention?: {
    template: string;
    date_basis: string;
    example?: string | null;
  } | null;
  acquisition_source_id?: string | null;
  acquisition_notes?: string | null;
  evidence: {
    source: string;
    reason_codes: string[];
    confidence?: number | null;
    sample_size: number;
    observed_from?: string | null;
    observed_to?: string | null;
  };
}

export interface ExternalSignalRelationship {
  signal_id: string;
  source_correspondent_id?: number | null;
  expectation_ids: string[];
  resolution: 'unresolved' | 'linked' | 'not_expected' | 'deferred';
  safe_label?: string | null;
  revisit_date?: string | null;
  updated_at: string;
}

export interface ExternalSignal {
  id: string;
  kind: 'accountStatementCandidate' | 'recurringDocumentCandidate';
  active: boolean;
  display_hint: string;
  confidence: number;
  basis: string[];
  source_as_of: string;
  account_name?: string | null;
  institution_name?: string | null;
  account_type?: string | null;
  account_last_four?: string | null;
  relationship?: ExternalSignalRelationship | null;
}

export interface AcquisitionSource {
  id: string;
  channel: string;
  delivery_mode: string;
  instructions?: string | null;
  portal_url?: string | null;
}

export interface CoverageOccurrence {
  id: string;
  expectation_id: string;
  source_correspondent_id: number;
  source_name: string;
  expectation_name: string;
  kind: DocumentKind;
  period_key: string;
  due_start: string;
  due_end: string;
  importance: 'routine' | 'important' | 'critical';
  grace_period_days: number;
  grace_extended_until?: string | null;
  state: CoverageState;
  fulfilled_document_id?: number | null;
  fulfilled_at?: string | null;
  evaluated_at: string;
}

export interface CoverageOccurrenceDetail {
  occurrence: CoverageOccurrence;
  expectation: DocumentExpectation;
  series?: DocumentSeries | null;
  recent_document_ids: number[];
}

export interface CoverageNearMatch {
  document_id: number;
  title: string;
  document_date?: string | null;
  correspondent_id?: number | null;
  document_type?: string | null;
  already_assigned: boolean;
}

export function humanize(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatDate(value?: string | null): string {
  if (!value) return 'Not recorded';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

export function sourceDocumentCount(sourceId: number, series: DocumentSeries[]): number {
  return new Set(
    series.filter((item) => item.source_correspondent_id === sourceId).flatMap((item) => item.document_ids),
  ).size;
}
