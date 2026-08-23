export type ReviewStatus = 'unreviewed' | 'reviewed' | 'ignored';
export type LifecycleStatus = 'active' | 'orphaned' | 'retired';
export type ExpectationMode = 'recurring' | 'periodic' | 'one_off' | 'irregular' | 'not_expected';
export type AnalysisExpectationMode = ExpectationMode | 'unknown';
export type ExpectationStatus = 'suggested' | 'confirmed' | 'dismissed' | 'retired';
export type DocumentKind = 'statement' | 'invoice' | 'bill' | 'receipt' | 'record' | 'other';

export interface MetadataPolicy {
  all_of: number[];
  any_of: number[];
  none_of: number[];
  required_document_type_id: number | null;
}

export interface TitleConvention {
  template: string;
  date_basis: 'period' | 'document_date';
  example: string;
}

export interface Cadence {
  frequency: 'monthly' | 'quarterly' | 'annual';
  expected_day: number | null;
  availability_delay_days: number;
  grace_period_days: number;
}

export interface ExpectationEvidence {
  source: 'paperless' | 'user' | 'legacy_override';
  reason_codes: string[];
  confidence: number | null;
  sample_size: number;
  observed_from: string | null;
  observed_to: string | null;
}

export interface CorrespondentProfile {
  correspondent_id: number;
  current_name: string;
  review_status: ReviewStatus;
  lifecycle_status: LifecycleStatus;
  aliases: string[];
  notes: string | null;
  profile_defaults: {
    title_convention: TitleConvention | null;
    metadata_policy: MetadataPolicy | null;
  };
  observed_summary: {
    document_count: number;
    document_type_counts: Record<string, number>;
    title_pattern_count: number;
    tag_family_counts: Record<string, number>;
    candidate_series_count: number;
  };
  last_analyzed_at: string | null;
  last_reviewed_at: string | null;
  orphaned_at: string | null;
  relinked_from_correspondent_id: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CorrespondentInventoryItem {
  profile: CorrespondentProfile;
  expectation_count: number;
  suggested_expectation_count: number;
  statement_series_count: number;
  analysis_stale: boolean;
  priority_reasons: string[];
  metadata_inconsistency_count: number | null;
  unmatched_external_candidate_count: number | null;
}

export interface DocumentExpectation {
  id: string;
  correspondent_id: number;
  kind: DocumentKind;
  document_type_id: number | null;
  statement_series_id: string | null;
  series_discriminator: string | null;
  expectation_mode: ExpectationMode;
  status: ExpectationStatus;
  cadence: Cadence | null;
  evidence: ExpectationEvidence;
  title_convention: TitleConvention | null;
  metadata_policy: MetadataPolicy;
  acquisition_source_id: string | null;
  legacy_provider_key: string | null;
}

export interface TagFamilySuggestion {
  family: string;
  child_tag_ids: number[];
  child_tag_names: string[];
  coverage: number;
  reason_codes: string[];
}

export interface SeriesPolicySuggestion {
  statement_series_id: string | null;
  source_statement_series_id: string | null;
  series_discriminator: string | null;
  candidate_series: boolean;
  existing_expectation_id: string | null;
  kind: DocumentKind;
  expectation_mode: AnalysisExpectationMode;
  cadence: Cadence | null;
  evidence: ExpectationEvidence;
  document_ids: number[];
  title: {
    convention: TitleConvention | null;
    coverage: number;
    confidence: number;
    exception_document_ids: number[];
    examples: { document_id: number; before: string; after: string }[];
    missing_required_fields: { document_id: number; missing_fields: string[] }[];
    reason_codes: string[];
  };
  metadata: {
    policy: MetadataPolicy;
    confidence: number;
    required_tag_families: TagFamilySuggestion[];
    reason_codes: string[];
  };
  acquisition: {
    channel:
      | 'paperless_mail'
      | 'email_manual'
      | 'direct_api'
      | 'portal_manual'
      | 'snail_mail'
      | 'linked_storage'
      | 'unknown';
    delivery_mode: 'push' | 'pull' | 'physical' | null;
    confidence: number;
    reason_codes: string[];
    sample_size: number;
  };
}

export interface CorrespondentPolicyAnalysis {
  correspondent_id: number;
  correspondent_name: string;
  document_count: number;
  observed_from: string | null;
  observed_to: string | null;
  suggestions: SeriesPolicySuggestion[];
  unassigned_document_ids: number[];
  reason_codes: string[];
}

export interface AcquisitionSource {
  id: string;
  channel: SeriesPolicySuggestion['acquisition']['channel'];
  delivery_mode: 'push' | 'pull' | 'physical';
  instructions: string | null;
  portal_url: string | null;
  automation_state: 'not_applicable' | 'candidate' | 'available' | 'configured' | 'blocked';
  connector_type: string | null;
  connector_ref: string | null;
  availability_delay_days: number | null;
  last_success_at: string | null;
  browser_feasibility: 'not_assessed' | 'likely' | 'mfa_or_captcha' | 'unsupported';
}

export function parseTagIds(value: string): number[] {
  return Array.from(
    new Set(
      value
        .split(',')
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isInteger(item) && item > 0),
    ),
  ).sort((left, right) => left - right);
}

export function paperlessDocumentUrl(baseUrl: string | null, documentId: number): string | null {
  return baseUrl ? `${baseUrl.replace(/\/$/, '')}/documents/${documentId}/details` : null;
}
