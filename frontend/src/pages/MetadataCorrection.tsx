import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Badge,
  Breadcrumb,
  Button,
  Card,
  ConfidenceBar,
  EmptyState,
  ErrorState,
  Modal,
  PageHeader,
  SkeletonLoader,
  Toast,
} from '../components/ui';
import { endpoints, type DocumentSummaryModel } from '../lib/api';
import DocumentSummary from '../components/DocumentSummary';
import {
  ACCOUNT_IDENTIFIER_CLASSES,
  type AccountIdentifierClass,
} from '../lib/accountIdentifier';
import { getToastDuration } from '../lib/toast';
import '../styles/metadata-correction.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface ExtractedField {
  field_name: string;
  paperless_field: string;
  value: string | null;
  has_value: boolean;
}

interface CorrectionRecord {
  id: string;
  document_id: number;
  field_name: string;
  original_value: string | null;
  corrected_value: string | null;
  confidence: number | null;
  correction_type: string;
  source_region: unknown;
  notes: string | null;
  created_at: string | null;
  created_by: string;
}

interface MetadataResponse {
  document_id: number;
  title: string;
  document_summary: DocumentSummaryModel;
  paperless_url: string;
  extracted_fields: ExtractedField[];
  corrections: CorrectionRecord[];
  latest_corrections: Record<string, CorrectionRecord>;
  field_mapping: Record<string, string>;
  source_modified: string;
}

interface WritebackResponse {
  document_id: number;
  written_fields: string[];
  missing_fields: string[];
  update_count: number;
  source_modified?: string | null;
}

interface WriteConflict {
  field: string;
  label: string;
  loaded_value: unknown;
  paperless_value: unknown;
  owl_value: unknown;
}

interface SourceConflictDetails {
  code: 'source_revision_conflict';
  message: string;
  current_modified: string;
  conflicts: WriteConflict[];
}

interface AccountCorrectionRequest {
  field_name: string;
  corrected_value: string;
  original_value?: string;
  confidence?: number;
  identifier_class: AccountIdentifierClass;
  expected_modified: string;
  initiated_by: 'owl-metadata-correction';
}

type ToastState = { message: string; tone: 'success' | 'error' } | null;

function conflictDetails(error: unknown): SourceConflictDetails | null {
  if (!(error instanceof Error) || !('details' in error)) return null;
  const details = (error as {
    details?: {
      detail?: SourceConflictDetails;
      error?: SourceConflictDetails;
    };
  }).details;
  const conflict = details?.detail || details?.error;
  return conflict?.code === 'source_revision_conflict' ? conflict : null;
}

function displayConflictValue(value: unknown): string {
  if (value == null || value === '') return 'Not set';
  if (Array.isArray(value)) return value.join(', ') || 'None';
  return String(value);
}

// ------------------------------------------------------------------
// Constants
// ------------------------------------------------------------------

const FIELD_LABELS: Record<string, string> = {
  patient_name: 'Patient Name',
  provider_name: 'Provider Name',
  date_of_service: 'Date of Service',
  patient_responsibility: 'Patient Responsibility',
  claim_number: 'Claim Number',
  invoice_number: 'Invoice Number',
  account_identifier: 'Account Identifier',
};

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function fieldLabel(name: string): string {
  return FIELD_LABELS[name] ?? name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

type FieldStatus = 'confident' | 'low_confidence' | 'missing' | 'corrected' | 'confirmed';

function getFieldStatus(
  field: ExtractedField,
  correction?: CorrectionRecord,
): { status: FieldStatus; label: string; tone: 'ok' | 'warn' | 'err' | 'info' } {
  if (correction?.correction_type === 'corrected') {
    return { status: 'corrected', label: '✎ Corrected', tone: 'info' };
  }
  if (correction?.correction_type === 'confirmed') {
    return { status: 'confirmed', label: '✓ Confirmed', tone: 'ok' };
  }
  if (!field.has_value) {
    return { status: 'missing', label: '✗ Missing', tone: 'err' };
  }
  // Simulate confidence based on correction data or default to confident
  const conf = correction?.confidence;
  if (conf !== null && conf !== undefined && conf < 70) {
    return { status: 'low_confidence', label: `⚠ Low Confidence (${Math.round(conf)}%)`, tone: 'warn' };
  }
  return { status: 'confident', label: '✓ Confident', tone: 'ok' };
}

function formatDate(value?: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export default function MetadataCorrection() {
  const { docId } = useParams<{ docId: string }>();
  const navigate = useNavigate();

  // Data
  const [data, setData] = useState<MetadataResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Inline editing state: field_name → current edited value
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [accountIdentifierClass, setAccountIdentifierClass] =
    useState<AccountIdentifierClass>('service_account');

  // UI state
  const [toast, setToast] = useState<ToastState>(null);
  const [writingBack, setWritingBack] = useState(false);
  const [busyField, setBusyField] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [highlightedField, setHighlightedField] = useState<string | null>(null);
  const [writeConflict, setWriteConflict] = useState<{
    mode: 'writeback' | 'account';
    details: SourceConflictDetails;
    choices: Record<string, 'paperless' | 'owl'>;
    attemptedFields: string[];
    accountRequest?: AccountCorrectionRequest;
  } | null>(null);

  // ------------------------------------------------------------------
  // Data loading
  // ------------------------------------------------------------------

  const loadData = useCallback(async () => {
    if (!docId) return;
    setLoading(true);
    setError(null);
    try {
      const res = (await endpoints.metadata.get(docId)) as MetadataResponse;
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load document metadata.');
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  // Auto-clear toast
  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  // ------------------------------------------------------------------
  // Computed
  // ------------------------------------------------------------------

  const currentValues = useMemo(() => {
    if (!data) return {};
    const vals: Record<string, string> = {};
    for (const field of data.extracted_fields) {
      const correction = data.latest_corrections[field.field_name];
      if (field.field_name === 'account_identifier' && field.value != null) {
        vals[field.field_name] = String(field.value);
      } else if (correction?.corrected_value) {
        vals[field.field_name] = correction.corrected_value;
      } else if (field.value != null) {
        vals[field.field_name] = String(field.value);
      }
    }
    // Overlay any pending edits
    for (const [k, v] of Object.entries(editValues)) {
      vals[k] = v;
    }
    return vals;
  }, [data, editValues]);

  // Fields that have saved corrections (exclude unsaved edits — writeback reads from DB)
  const pendingWritebacks = useMemo(() => {
    if (!data) return [];
    return data.extracted_fields.filter((f) => {
      if (f.field_name === 'account_identifier') return false;
      const corr = data.latest_corrections[f.field_name];
      return corr?.correction_type === 'corrected' || corr?.correction_type === 'added';
    });
  }, [data]);

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  const handleCorrect = useCallback(
    async (fieldName: string) => {
      if (!docId || !data) return;
      const newValue = editValues[fieldName];
      if (newValue === undefined) return;

      const field = data.extracted_fields.find((f) => f.field_name === fieldName);
      const originalValue = field?.value ?? data.latest_corrections[fieldName]?.original_value ?? null;
      const correctionRequest = {
        field_name: fieldName,
        corrected_value: newValue,
        original_value: originalValue ?? undefined,
        confidence: data.latest_corrections[fieldName]?.confidence ?? undefined,
        identifier_class: accountIdentifierClass,
        expected_modified: data.source_modified,
        initiated_by: 'owl-metadata-correction' as const,
      };

      setBusyField(fieldName);
      try {
        await endpoints.metadata.correct(
          docId,
          fieldName === 'account_identifier'
            ? correctionRequest
            : {
                field_name: fieldName,
                corrected_value: newValue,
                original_value: originalValue ?? undefined,
                confidence: data.latest_corrections[fieldName]?.confidence ?? undefined,
                initiated_by: 'owl-metadata-correction',
              },
        );
        setEditingField(null);
        setEditValues((prev) => {
          const next = { ...prev };
          delete next[fieldName];
          return next;
        });
        setToast({
          message:
            fieldName === 'account_identifier'
              ? 'Account Identifier updated in Paperless'
              : `${fieldLabel(fieldName)} corrected`,
          tone: 'success',
        });
        await loadData();
      } catch (err) {
        const conflict = conflictDetails(err);
        if (fieldName === 'account_identifier' && conflict) {
          setWriteConflict({
            mode: 'account',
            details: conflict,
            choices: Object.fromEntries(
              conflict.conflicts.map((item) => [item.field, 'paperless' as const]),
            ),
            attemptedFields: [fieldName],
            accountRequest: correctionRequest,
          });
        } else {
          setToast({
            message:
              err instanceof Error
                ? err.message
                : `Failed to save correction for ${fieldLabel(fieldName)}`,
            tone: 'error',
          });
        }
      } finally {
        setBusyField(null);
      }
    },
    [accountIdentifierClass, docId, data, editValues, loadData],
  );

  const handleConfirm = useCallback(
    async (fieldName: string) => {
      if (!docId || !data) return;
      // Use the current displayed value (which may be a prior correction)
      const valueToConfirm = currentValues[fieldName] ?? undefined;

      setBusyField(fieldName);
      try {
        await endpoints.metadata.confirm(docId, {
          field_name: fieldName,
          current_value: valueToConfirm,
          confidence: data.latest_corrections[fieldName]?.confidence ?? undefined,
        });
        setToast({ message: `${fieldLabel(fieldName)} confirmed`, tone: 'success' });
        await loadData();
      } catch {
        setToast({ message: `Failed to confirm ${fieldLabel(fieldName)}`, tone: 'error' });
      } finally {
        setBusyField(null);
      }
    },
    [docId, data, currentValues, loadData],
  );

  const handleWriteback = useCallback(async (
    fieldNames?: string[],
    expectedModified = data?.source_modified,
  ) => {
    if (!docId || !data || !expectedModified) return;
    setWritingBack(true);
    try {
      const res = (await endpoints.metadata.writeback(docId, {
        expected_modified: expectedModified,
        base_values: Object.fromEntries(
          data.extracted_fields.map((field) => [field.field_name, field.value]),
        ),
        field_names: fieldNames,
        initiated_by: 'owl-metadata-correction',
      })) as WritebackResponse;
      setWriteConflict(null);
      setToast({
        message: `Wrote ${res.update_count} field(s) to Paperless${res.missing_fields.length ? ` (${res.missing_fields.length} skipped)` : ''}`,
        tone: 'success',
      });
      await loadData();
    } catch (err) {
      const conflict = conflictDetails(err);
      if (conflict) {
        const attemptedFields = fieldNames || pendingWritebacks.map((field) => field.field_name);
        setWriteConflict({
          mode: 'writeback',
          details: conflict,
          choices: Object.fromEntries(
            conflict.conflicts.map((item) => [item.field, 'paperless' as const]),
          ),
          attemptedFields,
        });
      } else {
        setToast({ message: err instanceof Error ? err.message : 'Writeback failed', tone: 'error' });
      }
    } finally {
      setWritingBack(false);
    }
  }, [data, docId, loadData, pendingWritebacks]);

  const resolveWriteConflict = useCallback(async () => {
    if (!writeConflict) return;
    const keepPaperless = new Set(
      writeConflict.details.conflicts
        .filter((item) => writeConflict.choices[item.field] === 'paperless')
        .map((item) => item.field),
    );
    const owlFields = writeConflict.attemptedFields.filter((field) => !keepPaperless.has(field));
    if (writeConflict.mode === 'account') {
      if (!owlFields.length || !writeConflict.accountRequest || !docId) {
        setWriteConflict(null);
        setToast({ message: 'Kept the current Paperless value.', tone: 'success' });
        await loadData();
        return;
      }
      setWritingBack(true);
      try {
        await endpoints.metadata.correct(docId, {
          ...writeConflict.accountRequest,
          expected_modified: writeConflict.details.current_modified,
        });
        setWriteConflict(null);
        setEditingField(null);
        setEditValues((current) => {
          const next = { ...current };
          delete next.account_identifier;
          return next;
        });
        setToast({ message: 'Account Identifier updated in Paperless', tone: 'success' });
        await loadData();
      } catch (error) {
        setToast({
          message: error instanceof Error ? error.message : 'Account Identifier writeback failed',
          tone: 'error',
        });
      } finally {
        setWritingBack(false);
      }
      return;
    }
    if (!owlFields.length) {
      setWriteConflict(null);
      setToast({ message: 'Kept the current Paperless values.', tone: 'success' });
      await loadData();
      return;
    }
    await handleWriteback(owlFields, writeConflict.details.current_modified);
  }, [docId, handleWriteback, loadData, writeConflict]);

  const startEditing = useCallback(
    (fieldName: string) => {
      setEditingField(fieldName);
      if (editValues[fieldName] === undefined) {
        setEditValues((prev) => ({
          ...prev,
          [fieldName]: currentValues[fieldName] ?? '',
        }));
      }
    },
    [currentValues, editValues],
  );

  const cancelEditing = useCallback(
    (fieldName: string) => {
      setEditingField(null);
      setEditValues((prev) => {
        const next = { ...prev };
        delete next[fieldName];
        return next;
      });
    },
    [],
  );

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  if (loading && !data) {
    return (
      <div className="metadata-page">
        <Breadcrumb
          items={[
            { label: 'Overview', to: '/' },
            { label: 'Metadata Correction' },
          ]}
        />
        <PageHeader title="Metadata Correction" />
        <SkeletonLoader variant="detail-panel" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="metadata-page">
        <Breadcrumb
          items={[
            { label: 'Overview', to: '/' },
            { label: 'Metadata Correction' },
          ]}
        />
        <PageHeader title="Metadata Correction" />
        <ErrorState message={error ?? 'Document not found.'} onRetry={() => void loadData()} />
      </div>
    );
  }

  return (
    <div className="metadata-page">
      <Breadcrumb
        items={[
          { label: 'Overview', to: '/' },
          { label: 'Metadata Correction' },
        ]}
      />
      <PageHeader
        title="Metadata Correction"
        desc={`Paperless #${data.document_id}`}
        actions={
          <div className="metadata-header-actions">
            <Button variant="ghost" size="sm" onClick={() => setShowHistory(true)}>
              📜 History ({data.corrections.length})
            </Button>
            <Button
              variant="success"
              onClick={() => void handleWriteback()}
              disabled={writingBack || pendingWritebacks.length === 0}
            >
              {writingBack ? '⏳ Writing…' : '💾 Save & Write to Paperless'}
            </Button>
            <Button onClick={() => navigate(-1)}>← Back</Button>
          </div>
        }
      />

      {/* Info banner */}
      <div className="meta-reason-banner">
        <strong>✏️ Review &amp; Correct Extracted Fields</strong>
        <span>
          Account Identifier edits write through immediately. Other corrections can be reviewed
          together before writing them to Paperless.
        </span>
      </div>

      {/* Split layout */}
      <div className="meta-split">
        {/* LEFT: Extracted fields */}
        <div>
          <Card title="📋 Extracted Fields" className="meta-fields-card">
            {data.extracted_fields.map((field) => {
              const correction = data.latest_corrections[field.field_name];
              const { status, label, tone } = getFieldStatus(field, correction);
              const isEditing = editingField === field.field_name;
              const displayValue = currentValues[field.field_name] ?? '';
              const isBusy = busyField === field.field_name;
              const isHighlighted = highlightedField === field.field_name;
              const confidence = correction?.confidence;

              return (
                <div
                  key={field.field_name}
                  className={`meta-field ${status === 'missing' ? 'missing-bg' : status === 'low_confidence' ? 'lowconf-bg' : ''} ${isHighlighted ? 'focused' : ''}`}
                  onClick={() => setHighlightedField(field.field_name)}
                >
                  <div className="meta-field-header">
                    <span className="meta-field-label">{fieldLabel(field.field_name)}</span>
                    <Badge tone={tone}>{label}</Badge>
                  </div>

                  <div className="meta-field-row">
                    {isEditing ? (
                      <div className="meta-edit-controls">
                        <div className="meta-edit-value-row">
                          <input
                            aria-label={fieldLabel(field.field_name)}
                            className={`meta-input ${correction?.correction_type === 'corrected' ? 'corrected' : ''}`}
                            value={editValues[field.field_name] ?? ''}
                            onChange={(e) =>
                              setEditValues((prev) => ({ ...prev, [field.field_name]: e.target.value }))
                            }
                            placeholder={`Enter ${fieldLabel(field.field_name).toLowerCase()}…`}
                            autoFocus
                            disabled={isBusy}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') void handleCorrect(field.field_name);
                              if (e.key === 'Escape') cancelEditing(field.field_name);
                            }}
                          />
                          <Button
                            size="sm"
                            variant="success"
                            onClick={() => void handleCorrect(field.field_name)}
                            disabled={isBusy || editValues[field.field_name] === undefined}
                          >
                            {isBusy ? '…' : 'Save'}
                          </Button>
                          <Button size="sm" onClick={() => cancelEditing(field.field_name)} disabled={isBusy}>
                            Cancel
                          </Button>
                        </div>
                        {field.field_name === 'account_identifier' && (
                          <div className="meta-account-classification">
                            <label htmlFor="account-identifier-class">Identifier type</label>
                            <select
                              id="account-identifier-class"
                              value={accountIdentifierClass}
                              onChange={(event) =>
                                setAccountIdentifierClass(event.target.value as AccountIdentifierClass)
                              }
                              disabled={isBusy}
                            >
                              {ACCOUNT_IDENTIFIER_CLASSES.map((option) => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                            <span>
                              The selected type controls validation and matching. The value is
                              stored exactly in Paperless.
                            </span>
                          </div>
                        )}
                      </div>
                    ) : (
                      <>
                        <span
                          className="meta-field-value"
                          onClick={(e) => {
                            e.stopPropagation();
                            startEditing(field.field_name);
                          }}
                          title="Click to edit"
                        >
                          {displayValue || <span className="text-muted">—</span>}
                        </span>
                        <div className="meta-field-actions">
                          <button
                            className="meta-edit-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              startEditing(field.field_name);
                            }}
                            title="Edit"
                          >
                            ✎
                          </button>
                          {field.field_name !== 'account_identifier'
                            && (field.has_value || status === 'corrected')
                            && status !== 'confirmed' && (
                            <button
                              className="meta-confirm-btn"
                              onClick={(e) => {
                                e.stopPropagation();
                                void handleConfirm(field.field_name);
                              }}
                              disabled={isBusy}
                              title="Confirm extraction is correct"
                            >
                              {isBusy ? '…' : '✓'}
                            </button>
                            )}
                        </div>
                      </>
                    )}
                  </div>

                  {/* Show original value for corrected fields */}
                  {correction?.correction_type === 'corrected' && correction.original_value && (
                    <div className="meta-original">
                      Original extraction: &ldquo;{correction.original_value}&rdquo;
                    </div>
                  )}

                  {/* Confidence bar */}
                  {confidence != null && confidence > 0 && (
                    <div className="meta-confidence-bar">
                      <ConfidenceBar label="Extraction" pct={Math.round(confidence)} />
                    </div>
                  )}

                  {/* Source region info */}
                  {correction?.source_region ? (
                    <div className="meta-source-region">
                      📐 Source region: {JSON.stringify(correction.source_region)}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </Card>

          {/* Writeback preview */}
          {pendingWritebacks.length > 0 && (
            <div className="meta-writeback-preview">
              <strong>📤 Paperless Writeback Preview</strong>
              <p>Saving will update these Paperless custom fields on document #{data.document_id}:</p>
              <div className="meta-writeback-fields">
                {pendingWritebacks.map((f) => (
                  <span key={f.field_name} className="meta-wb-field">
                    {f.paperless_field} → &ldquo;{currentValues[f.field_name] ?? ''}&rdquo;
                  </span>
                ))}
              </div>
              <div className="meta-writeback-hint">
                ✨ These corrections will also be used as training data to improve extraction for future documents.
              </div>
            </div>
          )}
        </div>

        {/* RIGHT: Document preview / source region info */}
        <div>
          <Card title="🔍 Source Document" className="meta-doc-card">
            <div className="meta-doc-info">
              <DocumentSummary summary={data.document_summary} density="review" />
              <div className="meta-doc-link">
                <a
                  href={`/api/documents/${data.document_id}/download`}
                  target="_blank"
                  rel="noreferrer"
                >
                  📄 View / Download Document
                </a>
              </div>
            </div>

            {/* Field mapping reference */}
            <div className="meta-field-map">
              <strong>Field Mapping (DI → Paperless)</strong>
              <table className="meta-map-table">
                <thead>
                  <tr>
                    <th>DI Field</th>
                    <th>Paperless Custom Field</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.extracted_fields.map((f) => {
                    const corr = data.latest_corrections[f.field_name];
                    const { label, tone } = getFieldStatus(f, corr);
                    return (
                      <tr key={f.field_name} className={highlightedField === f.field_name ? 'highlighted-row' : ''}>
                        <td>{fieldLabel(f.field_name)}</td>
                        <td><code>{f.paperless_field}</code></td>
                        <td><Badge tone={tone}>{label}</Badge></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          {/* How it works info box */}
          <div className="meta-info-box">
            <strong>🧠 How corrections work:</strong>
            <ul>
              <li><strong>Edit</strong> — click any field value to correct it</li>
              <li><strong>Confirm</strong> — mark a correct extraction as verified (positive training data)</li>
              <li><strong>Account identifiers</strong> — save directly to Paperless using the selected identifier type</li>
              <li><strong>Write to Paperless</strong> — push all corrections to Paperless custom fields</li>
              <li><strong>Training</strong> — confirmed & corrected fields improve future extraction accuracy</li>
            </ul>
          </div>
        </div>
      </div>

      {writeConflict && (
        <Modal
          title="Paperless metadata changed"
          onClose={() => setWriteConflict(null)}
          footer={(
            <>
              <Button
                variant="ghost"
                disabled={writingBack}
                onClick={() => setWriteConflict(null)}
              >
                Continue editing
              </Button>
              <Button
                variant="primary"
                disabled={writingBack}
                onClick={() => void resolveWriteConflict()}
              >
                {writingBack ? 'Resolving…' : 'Resolve & write'}
              </Button>
            </>
          )}
        >
          <div className="meta-conflict-dialog">
            <p>
              Choose which value to keep where Paperless and OWL both changed the same field.
              Other external edits will remain untouched.
            </p>
            {writeConflict.details.conflicts.length === 0 ? (
              <div className="meta-conflict-safe">
                The external edit does not overlap these corrections. You can safely continue.
              </div>
            ) : writeConflict.details.conflicts.map((conflict) => (
              <fieldset className="meta-conflict-field" key={conflict.field}>
                <legend>{conflict.label}</legend>
                <div className="meta-conflict-loaded">
                  <span>When opened</span>
                  <strong>{displayConflictValue(conflict.loaded_value)}</strong>
                </div>
                <label className={writeConflict.choices[conflict.field] === 'paperless' ? 'selected' : ''}>
                  <input
                    type="radio"
                    name={`metadata-conflict-${conflict.field}`}
                    checked={writeConflict.choices[conflict.field] === 'paperless'}
                    onChange={() => setWriteConflict((current) => current ? {
                      ...current,
                      choices: { ...current.choices, [conflict.field]: 'paperless' },
                    } : current)}
                  />
                  <span><strong>Keep Paperless</strong><small>Current external value</small></span>
                  <b>{displayConflictValue(conflict.paperless_value)}</b>
                </label>
                <label className={writeConflict.choices[conflict.field] === 'owl' ? 'selected' : ''}>
                  <input
                    type="radio"
                    name={`metadata-conflict-${conflict.field}`}
                    checked={writeConflict.choices[conflict.field] === 'owl'}
                    onChange={() => setWriteConflict((current) => current ? {
                      ...current,
                      choices: { ...current.choices, [conflict.field]: 'owl' },
                    } : current)}
                  />
                  <span><strong>Apply OWL</strong><small>Reviewed correction</small></span>
                  <b>{displayConflictValue(conflict.owl_value)}</b>
                </label>
              </fieldset>
            ))}
          </div>
        </Modal>
      )}

      {/* Correction History Modal */}
      {showHistory && (
        <Modal title={`Correction History — Document #${data.document_id}`} onClose={() => setShowHistory(false)}>
          {data.corrections.length === 0 ? (
            <EmptyState title="No corrections yet" desc="Corrections will appear here as fields are edited or confirmed." />
          ) : (
            <div className="meta-history-list">
              {data.corrections.map((c) => (
                <div key={c.id} className="meta-history-item">
                  <div className="meta-history-header">
                    <strong>{fieldLabel(c.field_name)}</strong>
                    <Badge
                      tone={
                        c.correction_type === 'confirmed'
                          ? 'ok'
                          : c.correction_type === 'corrected'
                            ? 'info'
                            : 'muted'
                      }
                    >
                      {c.correction_type}
                    </Badge>
                    <span className="text-muted">{formatDate(c.created_at)}</span>
                  </div>
                  {c.correction_type === 'corrected' && (
                    <div className="meta-history-diff">
                      <span className="meta-diff-old">{c.original_value ?? '(empty)'}</span>
                      <span className="meta-diff-arrow">→</span>
                      <span className="meta-diff-new">{c.corrected_value}</span>
                    </div>
                  )}
                  {c.correction_type === 'confirmed' && (
                    <div className="meta-history-confirmed">
                      Confirmed value: &ldquo;{c.corrected_value}&rdquo;
                      {c.confidence != null && ` (${Math.round(c.confidence)}% confidence)`}
                    </div>
                  )}
                  {c.notes && <div className="meta-history-notes">Note: {c.notes}</div>}
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}

      {toast && <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />}
    </div>
  );
}
