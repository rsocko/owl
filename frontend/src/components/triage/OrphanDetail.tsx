/**
 * OrphanDetail — detail panel for orphan documents (unmatched EOBs/Bills).
 *
 * Rendered inline within the triage queue detail panel when
 * item_type === 'orphan_document'. Shows document info, age-based timeline,
 * status indicator, and orphan-specific action buttons.
 */

import { useCallback, useEffect, useState } from 'react';
import { Button, Toast } from '../ui';
import DocumentPreview from '../DocumentPreview';
import ManualMatchModal from '../ManualMatchModal';
import { endpoints, type DocumentSummaryModel } from '../../lib/api';
import DocumentSummary from '../DocumentSummary';
import { getToastDuration } from '../../lib/toast';
import '../../styles/orphan-detail.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface TriageItem {
  id: string;
  item_type: string;
  priority: number;
  status: string;
  source: string;
  target_type: string;
  target_id: string;
  reason: string | null;
  metadata: Record<string, unknown> | null;
  deferred_until: string | null;
  resolved_at: string | null;
  resolved_action: string | null;
  created_at: string | null;
  document_summary?: DocumentSummaryModel;
}

type ToastState = { message: string; tone: 'success' | 'error' } | null;

// ------------------------------------------------------------------
// Props
// ------------------------------------------------------------------

export interface OrphanDetailProps {
  triageItem: TriageItem;
  /** Called after any resolution action so the parent can refresh. */
  onResolved?: () => void;
  /** Called when the user presses Skip. */
  onSkip?: () => void;
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function formatCurrency(value?: number | null) {
  if (value === undefined || value === null) return '—';
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value);
}

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

function docTypeLabel(docType?: string | null): string {
  if (docType === 'eob') return 'EOB';
  if (docType === 'bill') return 'Bill';
  return 'Document';
}

function missingDocLabel(docType?: string | null): string {
  return docType === 'eob' ? 'bill' : 'EOB';
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export default function OrphanDetail({ triageItem, onResolved, onSkip }: OrphanDetailProps) {
  const [toast, setToast] = useState<ToastState>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [matchModalOpen, setMatchModalOpen] = useState(false);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const meta = triageItem.metadata || {};
  const documentType = (meta.document_type as string) || 'eob';
  const providerName = (meta.provider_name as string) || 'Unknown provider';
  const patientName = (meta.patient_name as string) || 'Unknown patient';
  const amount = meta.amount as number | null;
  const dateOfService = meta.date_of_service as string | null;
  const orphanStatus = (meta.orphan_status as string) || 'waiting';
  const ageDays = (meta.document_age_days as number) || 0;
  const waitingThreshold = (meta.waiting_threshold_days as number) || (documentType === 'eob' ? 30 : 14);
  const overdueThreshold = (meta.overdue_threshold_days as number) || (documentType === 'eob' ? 60 : 45);
  const insuranceCompany = meta.insurance_company as string | null;
  const invoiceNumber = meta.invoice_number as string | null;
  const documentId = meta.document_id;
  const isPending = triageItem.status === 'pending';

  // ── Actions ──

  const handleAction = useCallback(
    async (actionName: string, actionFn: () => Promise<unknown>, successMsg: string) => {
      setBusyAction(actionName);
      try {
        await actionFn();
        setToast({ message: successMsg, tone: 'success' });
        onResolved?.();
      } catch (err) {
        setToast({ message: err instanceof Error ? err.message : `${actionName} failed.`, tone: 'error' });
      } finally {
        setBusyAction(null);
      }
    },
    [onResolved],
  );

  const handleFindMatch = () => {
    setMatchModalOpen(true);
  };

  const handleMatchCreated = async () => {
    setMatchModalOpen(false);
    // Resolve the triage item as manually linked
    try {
      await endpoints.triage.resolve(triageItem.id, { action: 'manual_link', payload: { reason: 'Matched via manual match search' } });
      setToast({ message: 'Document matched and orphan resolved.', tone: 'success' });
      onResolved?.();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Match created but failed to resolve the review item.', tone: 'error' });
    }
  };

  const handleDefer = () =>
    handleAction('defer', () => endpoints.triage.orphans.defer(triageItem.id), `Review postponed for 30 days — will re-flag if still unmatched.`);

  const handleSelfPay = () =>
    handleAction('self-pay', () => endpoints.triage.orphans.selfPay(triageItem.id), 'Marked as self-pay. Document tagged in Paperless.');

  const handleAlreadyPaid = () =>
    handleAction('already-paid', () => endpoints.triage.orphans.alreadyPaid(triageItem.id), 'Marked as already paid.');

  const handleNotMedical = () =>
    handleAction('not-medical', () => endpoints.triage.orphans.notMedical(triageItem.id), 'Reclassified as non-medical. Removed from tracking.');

  // ── Timeline calculations ──

  const timelinePercent = overdueThreshold > 0 ? Math.min(100, Math.round((ageDays / overdueThreshold) * 100)) : 0;
  const waitingPercent = overdueThreshold > 0 ? Math.round((waitingThreshold / overdueThreshold) * 100) : 0;

  // ── Render ──

  return (
    <div className="orphan-detail">
      {/* Status banner */}
      <div className={`orphan-status-banner ${orphanStatus}`}>
        <span className="orphan-status-icon">{orphanStatus === 'overdue' ? '🚨' : '⏳'}</span>
        <span className="orphan-status-text">
          <strong>{orphanStatus === 'overdue' ? 'Overdue' : 'Waiting'}</strong>
          {' — '}
          {docTypeLabel(documentType)} with no matching {missingDocLabel(documentType)} for{' '}
          <strong>{ageDays} days</strong>
          {orphanStatus === 'overdue' && ` (threshold: ${overdueThreshold}d)`}
        </span>
        <span className="orphan-status-days">{ageDays}d</span>
      </div>

      {/* Reason banner */}
      {triageItem.reason && (
        <div className="orphan-reason-banner">
          <strong>Flagged:</strong> {triageItem.reason}
        </div>
      )}

      {/* Document info */}
      <div className="orphan-doc-card">
        <div className="orphan-doc-header">
          <span className="orphan-doc-icon">{documentType === 'eob' ? '📋' : '💵'}</span>
          {triageItem.document_summary && (
            <DocumentSummary summary={triageItem.document_summary} density="review" />
          )}
          <span className={`orphan-doc-badge ${documentType}`}>{docTypeLabel(documentType)}</span>
        </div>
        <div className="orphan-fields">
          <div>
            <div className="orphan-field-label">Patient</div>
            <div className="orphan-field-value">{patientName}</div>
          </div>
          <div>
            <div className="orphan-field-label">Date of Service</div>
            <div className="orphan-field-value">{formatDate(dateOfService)}</div>
          </div>
          <div>
            <div className="orphan-field-label">Amount</div>
            <div className="orphan-field-value">{formatCurrency(amount)}</div>
          </div>
          <div>
            <div className="orphan-field-label">Document ID</div>
            <div className="orphan-field-value">#{String(documentId ?? triageItem.target_id)}</div>
          </div>
          {insuranceCompany && (
            <div>
              <div className="orphan-field-label">Insurance</div>
              <div className="orphan-field-value">{insuranceCompany}</div>
            </div>
          )}
          {invoiceNumber && (
            <div>
              <div className="orphan-field-label">Invoice #</div>
              <div className="orphan-field-value">{invoiceNumber}</div>
            </div>
          )}
        </div>
      </div>

      {/* Timeline visualization */}
      <div className="orphan-timeline-card">
        <div className="orphan-timeline-title">📅 Match Timeline</div>

        {/* Horizontal bar */}
        <div className="orphan-timeline-bar">
          <div className="orphan-timeline-track">
            <div
              className={`orphan-timeline-fill ${orphanStatus}`}
              style={{ width: `${timelinePercent}%` }}
            />
            {/* Milestone: document received (0%) */}
            <div className="orphan-timeline-milestone" style={{ left: '0%' }}>
              <div className="orphan-timeline-dot past" />
              <span className="orphan-timeline-label">Received</span>
            </div>
            {/* Milestone: waiting threshold */}
            <div className="orphan-timeline-milestone" style={{ left: `${waitingPercent}%` }}>
              <div className={`orphan-timeline-dot ${ageDays >= waitingThreshold ? 'past' : 'future'}`} />
              <span className="orphan-timeline-label">{waitingThreshold}d</span>
            </div>
            {/* Milestone: overdue threshold */}
            <div className="orphan-timeline-milestone" style={{ left: '100%' }}>
              <div className={`orphan-timeline-dot ${ageDays >= overdueThreshold ? 'current overdue' : 'future'}`} />
              <span className="orphan-timeline-label">{overdueThreshold}d</span>
            </div>
            {/* Current position (if not at a milestone) */}
            {timelinePercent > 2 && timelinePercent < 98 && (
              <div className="orphan-timeline-milestone" style={{ left: `${timelinePercent}%` }}>
                <div className={`orphan-timeline-dot current ${orphanStatus}`} />
                <span className="orphan-timeline-label">Now ({ageDays}d)</span>
              </div>
            )}
          </div>
        </div>

        {/* Vertical timeline list */}
        <div className="orphan-timeline-list">
          <div className="orphan-timeline-entry">
            <div className="orphan-timeline-entry-dot past" />
            <div className="orphan-timeline-entry-text">
              <strong>Document received</strong> — {docTypeLabel(documentType)} from {providerName}
              {triageItem.created_at && ` on ${formatDate(triageItem.created_at)}`}
            </div>
          </div>
          {dateOfService && (
            <div className="orphan-timeline-entry">
              <div className="orphan-timeline-entry-dot past" />
              <div className="orphan-timeline-entry-text">
                <strong>Service date:</strong> {formatDate(dateOfService)}
              </div>
            </div>
          )}
          <div className="orphan-timeline-entry">
            <div className={`orphan-timeline-entry-dot ${ageDays >= waitingThreshold ? 'waiting' : 'future'}`} />
            <div className="orphan-timeline-entry-text">
              <strong>{waitingThreshold}-day mark:</strong>{' '}
              {ageDays >= waitingThreshold
                ? `Reached — flagged as waiting (${ageDays - waitingThreshold}d ago)`
                : `In ${waitingThreshold - ageDays} days`}
            </div>
          </div>
          <div className="orphan-timeline-entry">
            <div className={`orphan-timeline-entry-dot ${ageDays >= overdueThreshold ? 'overdue' : 'future'}`} />
            <div className="orphan-timeline-entry-text">
              <strong>{overdueThreshold}-day mark:</strong>{' '}
              {ageDays >= overdueThreshold
                ? `Reached — escalated to overdue (${ageDays - overdueThreshold}d ago)`
                : `In ${overdueThreshold - ageDays} days`}
            </div>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="orphan-actions-card">
        <div className="orphan-actions-title">Actions</div>

        <div className="orphan-action-row">
          <span className="orphan-action-icon">🔍</span>
          <div className="orphan-action-text">
            <div className="orphan-action-label">Find Match</div>
            <div className="orphan-action-desc">Search for a matching {missingDocLabel(documentType)} document</div>
          </div>
          <button
            className="orphan-action-btn primary"
            onClick={handleFindMatch}
            disabled={!isPending || busyAction !== null}
          >
            Search
          </button>
        </div>

        <div className="orphan-action-row">
          <span className="orphan-action-icon">⏳</span>
          <div className="orphan-action-text">
            <div className="orphan-action-label">Waiting for {missingDocLabel(documentType)}</div>
            <div className="orphan-action-desc">Review again in 30 days if still unmatched</div>
          </div>
          <button
            className="orphan-action-btn"
            onClick={() => void handleDefer()}
            disabled={!isPending || busyAction !== null}
          >
            {busyAction === 'defer' ? 'Scheduling…' : 'Review later'}
          </button>
        </div>

        <div className="orphan-action-row">
          <span className="orphan-action-icon">💰</span>
          <div className="orphan-action-text">
            <div className="orphan-action-label">Self-Pay / No {missingDocLabel(documentType)} Expected</div>
            <div className="orphan-action-desc">Mark resolved — tags document as no-bill-expected in Paperless</div>
          </div>
          <button
            className="orphan-action-btn"
            onClick={() => void handleSelfPay()}
            disabled={!isPending || busyAction !== null}
          >
            {busyAction === 'self-pay' ? 'Saving…' : 'Self-Pay'}
          </button>
        </div>

        <div className="orphan-action-row">
          <span className="orphan-action-icon">✅</span>
          <div className="orphan-action-text">
            <div className="orphan-action-label">Already Paid</div>
            <div className="orphan-action-desc">Payment confirmed without {missingDocLabel(documentType)} document</div>
          </div>
          <button
            className="orphan-action-btn"
            onClick={() => void handleAlreadyPaid()}
            disabled={!isPending || busyAction !== null}
          >
            {busyAction === 'already-paid' ? 'Saving…' : 'Already Paid'}
          </button>
        </div>

        <div className="orphan-action-row">
          <span className="orphan-action-icon">❌</span>
          <div className="orphan-action-text">
            <div className="orphan-action-label">Not Medical</div>
            <div className="orphan-action-desc">Misclassified document — remove from medical tracking</div>
          </div>
          <button
            className="orphan-action-btn"
            onClick={() => void handleNotMedical()}
            disabled={!isPending || busyAction !== null}
          >
            {busyAction === 'not-medical' ? 'Saving…' : 'Not Medical'}
          </button>
        </div>
      </div>

      {/* Document preview */}
      {documentId != null && !Number.isNaN(Number(documentId)) && (
        <div className="orphan-preview-section">
          <DocumentPreview
            documentId={Number(documentId)}
            label={docTypeLabel(documentType)}
          />
        </div>
      )}

      {/* Related documents placeholder */}
      <div className="orphan-related-card">
        <div className="orphan-related-title">Related Documents</div>
        <div className="orphan-related-empty">
          No partial matches or similar documents found for this {docTypeLabel(documentType).toLowerCase()}.
        </div>
      </div>

      {/* Skip / navigate buttons */}
      {onSkip && (
        <div style={{ textAlign: 'right', marginBottom: 12 }}>
          <Button onClick={onSkip} title="Skip (S)">
            Skip →
          </Button>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div style={{ position: 'fixed', bottom: 24, right: 24, zIndex: 1000 }}>
          <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />
        </div>
      )}

      {/* Manual Match Modal */}
      <ManualMatchModal
        open={matchModalOpen}
        onClose={() => setMatchModalOpen(false)}
        sourceDocId={documentId != null ? Number(documentId) : undefined}
        triageItemId={triageItem.id}
        onMatchCreated={() => void handleMatchCreated()}
      />
    </div>
  );
}
