/**
 * EobMatchDetail — embeddable EOB ↔ Bill match review detail panel.
 *
 * Can be rendered standalone (via EobMatchReview page wrapper) or inline
 * within the triage queue detail panel when item_type === 'eob_match_review'.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  ConfidenceBar,
  EmptyState,
  ErrorState,
  SkeletonLoader,
  Toast,
  confidenceTone,
} from './ui';
import { endpoints } from '../lib/api';
import { getToastDuration } from '../lib/toast';
import ManualMatchModal from './ManualMatchModal';
import ConfirmModal from './ConfirmModal';
import DocumentPreview from './DocumentPreview';
import SideBySideViewerModal from './SideBySideViewerModal';
import '../styles/eob-pages.css';

// ------------------------------------------------------------------
// Types
// ------------------------------------------------------------------

interface EobDetails {
  id?: number | null;
  document_id?: number | null;
  run_id?: number | null;
  title?: string | null;
  classification_score?: number | null;
  provider_name?: string | null;
  patient_name?: string | null;
  insurance_company?: string | null;
  policy_number?: string | null;
  date_of_service?: string | null;
  total_billed?: number | null;
  total_allowed?: number | null;
  total_plan_pays?: number | null;
  total_patient_responsibility?: number | null;
  claim_number?: string | null;
  services_json?: string | null;
  created_at?: string | null;
}

interface BillDetails {
  id?: number | null;
  document_id?: number | null;
  run_id?: number | null;
  title?: string | null;
  classification_score?: number | null;
  provider_name?: string | null;
  patient_name?: string | null;
  date_of_service?: string | null;
  due_date?: string | null;
  total_amount?: number | null;
  balance_due?: number | null;
  invoice_number?: string | null;
  payment_status?: string | null;
  services_json?: string | null;
  created_at?: string | null;
}

interface EobMatch {
  id: number;
  run_id?: number | null;
  eob_document_id?: number | null;
  bill_document_id?: number | null;
  score?: number | null;
  confidence?: string | null;
  breakdown?: {
    date?: number | null;
    provider?: number | null;
    patient?: number | null;
    amount?: number | null;
    procedures?: number | null;
  } | null;
  status?: string | null;
  payment_status?: string | null;
  paid_amount?: number | null;
  paid_date?: string | null;
  linked_in_paperless?: boolean | null;
  eob_preview_url?: string | null;
  bill_preview_url?: string | null;
  created_at?: string | null;
  confirmed_at?: string | null;
  flag_reason?: string | null;
  notes?: string | null;
  user_status?: string | null;
  reviewed_at?: string | null;
  user_notes?: string | null;
  eob_details?: EobDetails | null;
  bill_details?: BillDetails | null;
}

interface MatchHistoryEvent {
  id: number;
  event_type: string;
  actor: string;
  detail?: string | null;
  created_at?: string | null;
}

interface MatchHistoryResponse {
  match_id: number;
  events: MatchHistoryEvent[];
}

interface CandidatesResponse {
  doc_id: number;
  candidates: EobMatch[];
  count: number;
}

interface PaymentItem {
  id: number;
  amount: number;
  paid_date?: string | null;
  method?: string | null;
  notes?: string | null;
  created_at?: string | null;
}

interface MatchPaymentsResponse {
  match_id: number;
  payment_status: string;
  paid_amount: number;
  payments: PaymentItem[];
}

type ToastState = { message: string; tone: 'success' | 'error' } | null;

// ------------------------------------------------------------------
// Props
// ------------------------------------------------------------------

export interface EobMatchDetailProps {
  matchId: number;
  /** When provided, confirm/reject also resolve the triage queue item. */
  triageItemId?: string;
  /** Called after a confirm/reject action so the parent can refresh state. */
  onResolved?: () => void;
  /** Called when the user presses Skip (S). */
  onSkip?: () => void;
  /** Called when the user presses Re-link (R) — placeholder. */
  onRelink?: () => void;
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return `${Math.round(value)}%`;
}

function statusTone(status?: string | null): 'success' | 'warning' | 'danger' | 'muted' {
  switch ((status || '').toLowerCase()) {
    case 'confirmed':
      return 'success';
    case 'rejected':
      return 'danger';
    case 'candidate':
      return 'warning';
    default:
      return 'muted';
  }
}

function statusLabel(status?: string | null) {
  switch ((status || '').toLowerCase()) {
    case 'confirmed':
      return 'Confirmed';
    case 'rejected':
      return 'Rejected';
    case 'candidate':
      return 'Pending review';
    default:
      return status || 'Unknown';
  }
}

function factorNarrative(label: string, value?: number | null) {
  if (typeof value !== 'number') return `${label} data is not available from the current API response.`;
  if (value >= 90) return `${label} is a strong match signal.`;
  if (value >= 70) return `${label} is directionally aligned but worth a quick check.`;
  if (value >= 50) return `${label} is weak enough to justify manual review.`;
  return `${label} is a likely mismatch and is driving this review.`;
}

function scoreBadgeTone(score?: number | null): 'success' | 'warning' | 'danger' {
  const tone = confidenceTone(score ?? 0);
  return tone === 'high' ? 'success' : tone === 'medium' ? 'warning' : 'danger';
}

function eventDotTone(eventType: string): 'info' | 'success' | 'danger' | 'muted' {
  switch (eventType) {
    case 'auto_matched':
      return 'info';
    case 'confirmed':
    case 'payment_recorded':
      return 'success';
    case 'rejected':
      return 'danger';
    case 'flagged':
      return 'muted';
    case 'reset':
      return 'muted';
    default:
      return 'muted';
  }
}

function eventLabel(eventType: string): string {
  switch (eventType) {
    case 'auto_matched':
      return 'Auto-matched';
    case 'flagged':
      return 'Flagged for review';
    case 'confirmed':
      return 'Confirmed';
    case 'rejected':
      return 'Rejected';
    case 'reset':
      return 'Reset to candidate';
    case 'payment_recorded':
      return 'Payment recorded';
    default:
      return eventType;
  }
}

function paymentStatusLabel(status?: string | null): string {
  switch ((status || '').toLowerCase()) {
    case 'paid': return 'Paid';
    case 'partial': return 'Partially paid';
    case 'overpaid': return 'Overpaid';
    case 'unpaid': return 'Unpaid';
    default: return status || 'Unpaid';
  }
}

function paymentStatusTone(status?: string | null): 'success' | 'warning' | 'danger' | 'muted' {
  switch ((status || '').toLowerCase()) {
    case 'paid': return 'success';
    case 'partial': return 'warning';
    case 'overpaid': return 'danger';
    default: return 'muted';
  }
}

function formatCurrency(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

// ------------------------------------------------------------------
// Component
// ------------------------------------------------------------------

export default function EobMatchDetail({
  matchId,
  triageItemId,
  onResolved,
  onSkip,
  onRelink,
}: EobMatchDetailProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState>(null);
  const [notes, setNotes] = useState('');
  const [showManualMatch, setShowManualMatch] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [match, setMatch] = useState<EobMatch | null>(null);
  const [alternatives, setAlternatives] = useState<EobMatch[]>([]);
  const [historyEvents, setHistoryEvents] = useState<MatchHistoryEvent[]>([]);
  const [payments, setPayments] = useState<PaymentItem[]>([]);
  const [payAmount, setPayAmount] = useState('');
  const [payMethod, setPayMethod] = useState('');
  const [payNotes, setPayNotes] = useState('');
  const [isRecordingPayment, setIsRecordingPayment] = useState(false);
  const [pendingAction, setPendingAction] = useState<'confirm' | 'reject' | null>(null);
  const [sideBySideOpen, setSideBySideOpen] = useState(false);

  // Guard against stale requests when matchId changes rapidly
  const loadGenRef = useRef(0);

  // Reset state when matchId changes
  useEffect(() => {
    setNotes('');
    setMatch(null);
    setAlternatives([]);
    setHistoryEvents([]);
    setError(null);
  }, [matchId]);

  useEffect(() => {
    if (!toast) return undefined;
    const duration = getToastDuration(toast.tone);
    if (duration <= 0) return undefined;
    const timeout = window.setTimeout(() => setToast(null), duration);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  // ---- Data loading ----

  const loadMatch = useCallback(async () => {
    if (!matchId || Number.isNaN(matchId)) {
      setLoading(false);
      setError('The match ID is invalid.');
      return;
    }

    const gen = ++loadGenRef.current;

    setLoading(true);
    setError(null);
    try {
      // Load match details directly by ID
      const currentMatch = (await endpoints.eob.getMatch(String(matchId))) as EobMatch | null;
      if (gen !== loadGenRef.current) return; // stale request
      if (!currentMatch) {
        setError(`Match #${matchId} was not found.`);
        setMatch(null);
        setAlternatives([]);
        return;
      }
      setMatch(currentMatch);

      // Load alternative candidates from the new endpoint
      try {
        const eobId = currentMatch.eob_document_id;
        if (eobId != null) {
          const candidatesResp = (await endpoints.eob.candidates(
            String(eobId),
            'limit=10',
          )) as CandidatesResponse;
          if (gen !== loadGenRef.current) return; // stale
          setAlternatives(
            (candidatesResp.candidates ?? []).filter((c) => c.id !== currentMatch.id).slice(0, 5),
          );
        } else {
          setAlternatives([]);
        }
      } catch {
        if (gen !== loadGenRef.current) return;
        setAlternatives([]);
      }

      // Fetch match history
      try {
        const historyResponse = (await endpoints.eob.matchHistory(
          String(matchId),
        )) as MatchHistoryResponse;
        if (gen !== loadGenRef.current) return; // stale
        setHistoryEvents(historyResponse.events ?? []);
      } catch {
        if (gen !== loadGenRef.current) return;
        setHistoryEvents([]);
      }

      // Fetch payments for confirmed matches
      if ((currentMatch.status || '').toLowerCase() === 'confirmed') {
        try {
          const paymentsRes = (await endpoints.eob.matchPayments(
            String(matchId),
          )) as MatchPaymentsResponse;
          if (gen !== loadGenRef.current) return;
          setPayments(paymentsRes.payments ?? []);
        } catch {
          if (gen !== loadGenRef.current) return;
          setPayments([]);
        }
      } else {
        setPayments([]);
      }
    } catch (err) {
      if (gen !== loadGenRef.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load match details.');
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, [matchId]);

  useEffect(() => {
    void loadMatch();
  }, [loadMatch]);

  // ---- Derived state ----

  const factorRows = useMemo(() => {
    const bd = match?.breakdown;
    const round = (v?: number | null) => (typeof v === 'number' ? Math.round(v) : null);
    return [
      { key: 'date', label: 'Date of service', weight: '30%', pct: round(bd?.date) },
      { key: 'provider', label: 'Provider', weight: '25%', pct: round(bd?.provider) },
      { key: 'patient', label: 'Patient', weight: '20%', pct: round(bd?.patient) },
      { key: 'amount', label: 'Amount', weight: '15%', pct: round(bd?.amount) },
      { key: 'procedures', label: 'Procedures', weight: '10%', pct: round(bd?.procedures) },
    ];
  }, [match]);

  const weakestFactor = useMemo(() => {
    const available = factorRows.filter((row): row is typeof row & { pct: number } => row.pct !== null);
    return available.reduce<(typeof available)[0] | null>(
      (lowest, row) => (lowest === null || row.pct < lowest.pct ? row : lowest),
      null,
    );
  }, [factorRows]);

  const amountToneClass = (match?.breakdown?.amount ?? 0) >= 75 ? 'pass' : 'fail';

  /** Configurable tolerance threshold for amount validation (dollars). */
  const AMOUNT_TOLERANCE = 5;

  const amountComparison = useMemo(() => {
    const eobAmount = match?.eob_details?.total_patient_responsibility ?? null;
    const billAmount = match?.bill_details?.balance_due ?? match?.bill_details?.total_amount ?? null;
    if (eobAmount === null || billAmount === null) return null;
    const difference = billAmount - eobAmount;
    const pass = Math.abs(difference) <= AMOUNT_TOLERANCE;
    return { eobAmount, billAmount, difference, pass };
  }, [match]);

  // ---- Actions ----

  /** Execute confirm — called after modal confirmation. */
  const executeConfirm = useCallback(async () => {
    if (!match || saving) return;
    setPendingAction(null);
    setSaving('confirm');
    try {
      await endpoints.eob.confirmMatch(String(match.id), {
        notes: notes.trim() || null,
        triage_item_id: triageItemId ?? null,
      });
      setToast({ message: 'Match confirmed.', tone: 'success' });
      setNotes('');
      await loadMatch();
      onResolved?.();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Unable to confirm match.', tone: 'error' });
    } finally {
      setSaving(null);
    }
  }, [match, saving, notes, triageItemId, loadMatch, onResolved]);

  /** Execute reject — called after modal confirmation. */
  const executeReject = useCallback(async () => {
    if (!match || saving) return;
    setPendingAction(null);
    setSaving('reject');
    try {
      await endpoints.eob.rejectMatch(String(match.id), {
        reason: notes.trim() || null,
        triage_item_id: triageItemId ?? null,
      });
      setToast({ message: 'Match rejected.', tone: 'success' });
      setNotes('');
      await loadMatch();
      onResolved?.();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Unable to reject match.', tone: 'error' });
    } finally {
      setSaving(null);
    }
  }, [match, saving, notes, triageItemId, loadMatch, onResolved]);

  /** Prompt confirm modal (destructive — opens modal per UX-03). */
  const handleConfirm = useCallback(() => {
    if (!match || saving) return;
    setPendingAction('confirm');
  }, [match, saving]);

  /** Prompt reject modal (destructive — opens modal per UX-03). */
  const handleReject = useCallback(() => {
    if (!match || saving) return;
    setPendingAction('reject');
  }, [match, saving]);

  const handleRelink = useCallback(() => {
    if (onRelink) {
      onRelink();
    } else {
      setShowManualMatch(true);
    }
  }, [onRelink]);

  const handleSkip = useCallback(() => {
    onSkip?.();
  }, [onSkip]);

  const handleRecordPayment = useCallback(async () => {
    if (!matchId || isRecordingPayment) return;
    const amount = parseFloat(payAmount);
    if (Number.isNaN(amount) || amount <= 0) {
      setToast({ message: 'Please enter a valid payment amount.', tone: 'error' });
      return;
    }
    setIsRecordingPayment(true);
    try {
      await endpoints.eob.payMatch(String(matchId), {
        amount,
        method: payMethod.trim() || null,
        notes: payNotes.trim() || null,
      });
      setToast({ message: `Payment of ${formatCurrency(amount)} recorded.`, tone: 'success' });
      setPayAmount('');
      setPayMethod('');
      setPayNotes('');
      await loadMatch();
    } catch (err) {
      setToast({ message: err instanceof Error ? err.message : 'Failed to record payment.', tone: 'error' });
    } finally {
      setIsRecordingPayment(false);
    }
  }, [matchId, payAmount, payMethod, payNotes, isRecordingPayment, loadMatch]);

  // ---- Keyboard shortcuts ----

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Suppress shortcuts when any modal is open or focus is in a form field
      if (showManualMatch || pendingAction !== null) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      switch (e.key.toLowerCase()) {
        case 'y':
          e.preventDefault();
          handleConfirm();
          break;
        case 'n':
          e.preventDefault();
          handleReject();
          break;
        case 's':
          e.preventDefault();
          handleSkip();
          break;
        case 'r':
          e.preventDefault();
          handleRelink();
          break;
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleConfirm, handleReject, handleSkip, handleRelink, showManualMatch, pendingAction]);

  // ---- Render ----

  if (loading) {
    return <SkeletonLoader variant="cards" />;
  }

  if (error) {
    return <ErrorState message={error} onRetry={() => void loadMatch()} />;
  }

  if (!match) {
    return (
      <EmptyState
        title="No review record found"
        desc="The requested match is not available in the current EOB match list."
      />
    );
  }

  return (
    <div className="eob-review-shell eob-detail-embedded">
      {/* Header with match info and action buttons */}
      <div className="eob-detail-header">
        <div>
          <div className="eob-detail-title">
            Match #{match.id}: EOB #{match.eob_document_id ?? '—'} ↔ Bill #{match.bill_document_id ?? '—'}
          </div>
          <div className="eob-meta-row">
            <Badge tone={statusTone(match.status)}>{statusLabel(match.status)}</Badge>
            {(match.status || '').toLowerCase() === 'confirmed' && (
              <Badge tone={paymentStatusTone(match.payment_status)}>{paymentStatusLabel(match.payment_status)}</Badge>
            )}
            <Badge tone={scoreBadgeTone(match.score)}>
              {formatPercent(match.score)} overall · {(match.confidence || 'candidate').toUpperCase()}
            </Badge>
            <span>Created {formatDateTime(match.created_at)}</span>
          </div>
        </div>
      </div>

      {/* Sticky action bar */}
      <Card className="eob-sticky-actions">
        <div className="eob-action-strip">
          <div className="btn-group">
            <Button
              variant="success"
              onClick={() => void handleConfirm()}
              disabled={saving !== null}
              title="Confirm (Y)"
            >
              {saving === 'confirm' ? 'Confirming…' : '✓ Confirm (Y)'}
            </Button>
            <Button
              variant="danger"
              onClick={() => void handleReject()}
              disabled={saving !== null}
              title="Reject (N)"
            >
              {saving === 'reject' ? 'Rejecting…' : '✗ Reject (N)'}
            </Button>
            <Button onClick={handleRelink} disabled={saving !== null} title="Re-link (R)">
              🔗 Re-link (R)
            </Button>
            <Button onClick={handleSkip} disabled={saving !== null} title="Skip (S)">
              ⏭ Skip (S)
            </Button>
            {match.eob_preview_url && (
              <a
                className="btn btn-ghost"
                href={match.eob_preview_url}
                target="_blank"
                rel="noreferrer"
                title="View in Paperless"
              >
                📄 View in Paperless
              </a>
            )}
          </div>
        </div>
      </Card>

      {/* Reason banner */}
      <div className={`eob-reason-banner ${weakestFactor && weakestFactor.pct < 70 ? 'danger' : ''}`}>
        <strong>⚠️ Why this was flagged:</strong>{' '}
        {match.flag_reason ??
          (weakestFactor
            ? `${weakestFactor.label} scored ${weakestFactor.pct}%, so the matcher could not fully auto-confirm this pair.`
            : 'This candidate pair needs manual confirmation.')}
      </div>

      {/* Confidence breakdown + Amount validation */}
      <div className="eob-grid-2">
        <Card title="Match confidence breakdown">
          <div className="eob-card-stack">
            <div className="eob-confidence-overview">
              <div>
                <div className={`eob-confidence-score ${confidenceTone(match.score ?? 0)}`}>
                  {formatPercent(match.score)}
                </div>
                <div className="eob-table-secondary">Weighted 5-factor score</div>
              </div>
              <Badge tone={scoreBadgeTone(match.score)}>
                {(match.confidence || 'unknown').toUpperCase()}
              </Badge>
            </div>
            <div className="eob-card-stack">
              {factorRows.map((row) => (
                <div key={row.key} className="eob-factor-bar-row">
                  <ConfidenceBar label={row.label} pct={row.pct ?? 0} />
                  <span className="eob-factor-weight">{row.weight}</span>
                </div>
              ))}
            </div>
            <div className="eob-factor-grid">
              {factorRows.map((row) => (
                <div
                  key={`${row.key}-note`}
                  className={`eob-factor-card ${row.pct !== null && row.pct < 70 ? 'mismatch' : ''}`}
                >
                  <div className="eob-field-label">{row.label}</div>
                  <div className="eob-field-value">{row.pct !== null ? formatPercent(row.pct) : 'Unavailable'}</div>
                  <div className="eob-field-note">{factorNarrative(row.label, row.pct)}</div>
                </div>
              ))}
            </div>
          </div>
        </Card>

        <Card title="Amount validation">
          {amountComparison ? (
            <div className={`eob-validation ${amountComparison.pass ? 'pass' : 'fail'}`}>
              <div className="eob-validation-icon">{amountComparison.pass ? '✓' : '⚠'}</div>
              <div className="eob-card-stack">
                <div className="eob-field-value">
                  {amountComparison.pass
                    ? 'Amount Validation: PASS'
                    : 'Amount Validation: MISMATCH'}
                </div>
                <div className="eob-field-note">
                  {amountComparison.pass
                    ? 'Patient responsibility on EOB is within tolerance of the bill amount due.'
                    : 'Patient responsibility on EOB does not match bill amount due.'}
                </div>
                <div className="eob-meta-row" style={{ gap: '16px', marginTop: 4 }}>
                  <span>EOB Patient Resp: <strong>{formatCurrency(amountComparison.eobAmount)}</strong></span>
                  <span>Bill Amount Due: <strong>{formatCurrency(amountComparison.billAmount)}</strong></span>
                  <span>
                    Difference:{' '}
                    <strong style={{ color: amountComparison.pass ? undefined : 'var(--danger)' }}>
                      {amountComparison.difference >= 0 ? '+' : ''}
                      {formatCurrency(amountComparison.difference)}
                    </strong>
                  </span>
                </div>
                <div className="eob-meta-row" style={{ marginTop: 4 }}>
                  <Badge tone={amountComparison.pass ? 'success' : 'danger'}>
                    {amountComparison.pass ? 'Pass' : 'Mismatch risk'}
                  </Badge>
                  <span className="eob-field-note">
                    Tolerance: ±{formatCurrency(AMOUNT_TOLERANCE)}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className={`eob-validation ${amountToneClass}`}>
              <div className="eob-validation-icon">{amountToneClass === 'pass' ? '✓' : '⚠'}</div>
              <div className="eob-card-stack">
                <div className="eob-field-value">
                  {amountToneClass === 'pass' ? 'Amount signal is aligned' : 'Amount signal needs review'}
                </div>
                <div className="eob-field-note">
                  Amount similarity scored {formatPercent(match.breakdown?.amount)}. Dollar amounts
                  are not available for direct comparison.
                </div>
                <div className="eob-meta-row">
                  <Badge tone={amountToneClass === 'pass' ? 'success' : 'danger'}>
                    {amountToneClass === 'pass' ? 'Pass' : 'Mismatch risk'}
                  </Badge>
                  <span>Linked in Paperless: {match.linked_in_paperless ? 'Yes' : 'No'}</span>
                </div>
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* Side-by-side document comparison */}
      <Card title="Document comparison">
        <div className="eob-compare-grid">
          <div className="eob-compare-col">
            <div className="eob-compare-header">
              <span>EOB document</span>
              <Badge tone="info">#{match.eob_document_id ?? '—'}</Badge>
            </div>
            <div className="eob-compare-row">
              <div className="eob-field-label">Document ID</div>
              <div className="eob-field-value">{match.eob_document_id ?? '—'}</div>
            </div>
            <div className="eob-compare-row">
              <div className="eob-field-label">Preview</div>
              <div className="eob-field-value">
                {match.eob_preview_url ? (
                  <a className="eob-link" href={match.eob_preview_url} target="_blank" rel="noreferrer">
                    Open EOB preview →
                  </a>
                ) : (
                  'Preview unavailable'
                )}
              </div>
            </div>
            <div className={`eob-compare-row ${(match.breakdown?.date ?? 0) < 70 ? 'mismatch' : ''}`}>
              <div className="eob-field-label">Date of service signal</div>
              <div className="eob-field-value">{formatPercent(match.breakdown?.date)}</div>
              <div className="eob-field-note">{factorNarrative('Date of service', match.breakdown?.date)}</div>
            </div>
            <div className={`eob-compare-row ${(match.breakdown?.provider ?? 0) < 70 ? 'mismatch' : ''}`}>
              <div className="eob-field-label">Provider signal</div>
              <div className="eob-field-value">{formatPercent(match.breakdown?.provider)}</div>
              <div className="eob-field-note">{factorNarrative('Provider', match.breakdown?.provider)}</div>
            </div>
            <div className={`eob-compare-row ${(match.breakdown?.patient ?? 0) < 70 ? 'mismatch' : ''}`}>
              <div className="eob-field-label">Patient signal</div>
              <div className="eob-field-value">{formatPercent(match.breakdown?.patient)}</div>
              <div className="eob-field-note">{factorNarrative('Patient', match.breakdown?.patient)}</div>
            </div>
          </div>

          <div className="eob-compare-col">
            <div className="eob-compare-header">
              <span>Candidate bill</span>
              <Badge tone="info">#{match.bill_document_id ?? '—'}</Badge>
            </div>
            <div className="eob-compare-row">
              <div className="eob-field-label">Document ID</div>
              <div className="eob-field-value">{match.bill_document_id ?? '—'}</div>
            </div>
            <div className="eob-compare-row">
              <div className="eob-field-label">Preview</div>
              <div className="eob-field-value">
                {match.bill_preview_url ? (
                  <a className="eob-link" href={match.bill_preview_url} target="_blank" rel="noreferrer">
                    Open bill preview →
                  </a>
                ) : (
                  'Preview unavailable'
                )}
              </div>
            </div>
            <div className={`eob-compare-row ${(match.breakdown?.amount ?? 0) < 70 ? 'mismatch' : ''}`}>
              <div className="eob-field-label">Amount signal</div>
              <div className="eob-field-value">{formatPercent(match.breakdown?.amount)}</div>
              <div className="eob-field-note">{factorNarrative('Amount', match.breakdown?.amount)}</div>
            </div>
            <div className={`eob-compare-row ${(match.breakdown?.procedures ?? 0) < 70 ? 'mismatch' : ''}`}>
              <div className="eob-field-label">Procedure overlap</div>
              <div className="eob-field-value">{formatPercent(match.breakdown?.procedures)}</div>
              <div className="eob-field-note">{factorNarrative('Procedure overlap', match.breakdown?.procedures)}</div>
            </div>
            <div className="eob-compare-row">
              <div className="eob-field-label">Workflow state</div>
              <div className="eob-field-value">{statusLabel(match.status)}</div>
              <div className="eob-field-note">
                Run #{match.run_id ?? '—'} · Created {formatDateTime(match.created_at)}
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* Document preview thumbnails */}
      {match.eob_document_id && match.bill_document_id && (
        <div className="eob-compare-bar">
          <Button variant="ghost" onClick={() => setSideBySideOpen(true)}>
            📑 Compare side-by-side
          </Button>
        </div>
      )}
      <div className="eob-grid-2">
        <Card title="EOB document preview">
          {match.eob_document_id ? (
            <DocumentPreview
              documentId={match.eob_document_id}
              paperlessUrl={match.eob_preview_url}
              label="EOB"
            />
          ) : (
            <div className="eob-preview-placeholder">
              <div className="eob-preview-thumb muted">No EOB document linked</div>
            </div>
          )}
        </Card>
        <Card title="Bill document preview">
          {match.bill_document_id ? (
            <DocumentPreview
              documentId={match.bill_document_id}
              paperlessUrl={match.bill_preview_url}
              label="Bill"
            />
          ) : (
            <div className="eob-preview-placeholder">
              <div className="eob-preview-thumb muted">No bill document linked</div>
            </div>
          )}
        </Card>
      </div>

      {sideBySideOpen && match.eob_document_id && match.bill_document_id && (
        <SideBySideViewerModal
          heading="EOB ↔ Bill Comparison"
          left={{
            documentId: match.eob_document_id,
            title: 'EOB',
            paperlessUrl: match.eob_preview_url,
          }}
          right={{
            documentId: match.bill_document_id,
            title: 'Bill',
            paperlessUrl: match.bill_preview_url,
          }}
          onClose={() => setSideBySideOpen(false)}
        />
      )}

      {/* Payment tracking — visible only for confirmed matches */}
      {(match.status || '').toLowerCase() === 'confirmed' && (
        <Card title="Payment tracking">
          <div className="eob-card-stack">
            <div className="eob-meta-row" style={{ gap: 12 }}>
              <Badge tone={paymentStatusTone(match.payment_status)}>
                {paymentStatusLabel(match.payment_status)}
              </Badge>
              <span>Paid: {formatCurrency(match.paid_amount)}</span>
              {match.paid_date && <span>Last payment: {formatDateTime(match.paid_date)}</span>}
            </div>

            {!['paid', 'overpaid'].includes((match.payment_status || 'unpaid').toLowerCase()) && (
              <div className="eob-note-box" style={{ marginTop: 8 }}>
                <div className="eob-field-label">Record a payment</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
                  <input
                    type="number"
                    className="eob-notes-input"
                    style={{ width: 120, padding: '6px 8px' }}
                    placeholder="Amount"
                    value={payAmount}
                    onChange={(e) => setPayAmount(e.target.value)}
                    min="0.01"
                    step="0.01"
                  />
                  <select
                    className="eob-notes-input"
                    style={{ width: 140, padding: '6px 8px' }}
                    value={payMethod}
                    onChange={(e) => setPayMethod(e.target.value)}
                  >
                    <option value="">Method…</option>
                    <option value="check">Check</option>
                    <option value="online">Online</option>
                    <option value="insurance">Insurance</option>
                    <option value="cash">Cash</option>
                    <option value="other">Other</option>
                  </select>
                  <input
                    className="eob-notes-input"
                    style={{ flex: 1, minWidth: 120, padding: '6px 8px' }}
                    placeholder="Notes (optional)"
                    value={payNotes}
                    onChange={(e) => setPayNotes(e.target.value)}
                  />
                  <Button
                    variant="success"
                    onClick={() => void handleRecordPayment()}
                    disabled={isRecordingPayment || !payAmount}
                  >
                    {isRecordingPayment ? 'Saving…' : '✓ Record payment'}
                  </Button>
                </div>
              </div>
            )}

            {payments.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="eob-field-label">Payment history</div>
                <div className="eob-card-stack" style={{ marginTop: 4 }}>
                  {payments.map((p) => (
                    <div key={p.id} className="eob-compare-row">
                      <div className="eob-field-value">{formatCurrency(p.amount)}</div>
                      <div className="eob-field-note">
                        {p.method ? `via ${p.method}` : ''} {p.paid_date ? `on ${formatDateTime(p.paid_date)}` : ''}
                        {p.notes ? ` — ${p.notes}` : ''}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Alternative candidates + Match history */}
      <div className="eob-grid-2">
        <Card title="Alternative candidates">
          {alternatives.length ? (
            <div className="eob-card-stack">
              {alternatives.map((item) => {
                const altScore = Math.round(item.score ?? 0);
                const altTone = altScore >= 75 ? 'success' : altScore >= 50 ? 'warning' : 'danger';
                return (
                  <div key={item.id} className="eob-alt-card">
                    <div className="eob-alt-card-header">
                      <div>
                        <div className="eob-field-value">
                          Match #{item.id}: EOB #{item.eob_document_id ?? '—'} ↔ Bill #
                          {item.bill_document_id ?? '—'}
                        </div>
                        <div className="eob-field-note">
                          Status {statusLabel(item.status)} · Run #{item.run_id ?? '—'}
                        </div>
                      </div>
                      <Badge tone={altTone}>{formatPercent(item.score)}</Badge>
                    </div>
                    {item.breakdown && (
                      <div className="eob-alt-factors">
                        {(['date', 'provider', 'patient', 'amount'] as const).map((factor) => {
                          const val = Math.round(item.breakdown?.[factor] ?? 0);
                          return (
                            <div key={factor} className="eob-alt-factor">
                              <span className="eob-alt-factor-label">{factor}</span>
                              <ConfidenceBar label="" pct={val} />
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState
              title="No alternate candidates"
              desc="No other documents scored above 50% for this EOB."
            />
          )}
        </Card>

        <Card title="Match history & notes">
          <div className="eob-card-stack">
            <div className="eob-timeline">
              {historyEvents.length > 0 ? (
                historyEvents.map((event) => (
                  <div key={event.id} className="eob-timeline-item">
                    <div className="eob-timeline-track">
                      <span className={`eob-history-dot ${eventDotTone(event.event_type)}`} />
                      <span className="eob-timeline-line" />
                    </div>
                    <div className="eob-timeline-content">
                      <div className="eob-field-value">{eventLabel(event.event_type)}</div>
                      {event.detail && <div className="eob-field-note">{event.detail}</div>}
                      <div className="eob-field-note">
                        {event.actor === 'user' ? '👤 Reviewer' : '⚙️ System'} ·{' '}
                        {formatDateTime(event.created_at)}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="eob-history-list">
                  <div className="eob-history-item">
                    <span className="eob-history-dot info" />
                    <div>
                      <div className="eob-field-value">Candidate created</div>
                      <div className="eob-field-note">{formatDateTime(match.created_at)}</div>
                    </div>
                  </div>
                  <div className="eob-history-item">
                    <span
                      className={`eob-history-dot ${match.status === 'confirmed' ? 'success' : match.status === 'rejected' ? 'danger' : 'muted'}`}
                    />
                    <div>
                      <div className="eob-field-value">{statusLabel(match.status)}</div>
                      <div className="eob-field-note">
                        {match.confirmed_at
                          ? formatDateTime(match.confirmed_at)
                          : 'Awaiting reviewer action'}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="eob-note-box">
              <div className="eob-field-label">Reviewer notes</div>
              <textarea
                className="eob-notes-input"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Add context about this match decision (e.g., 'Bill includes $6 lab draw fee not on EOB — confirmed correct match')"
              />
              <div className="eob-field-note">Notes are saved with your confirm / reject action.</div>
            </div>
          </div>
        </Card>
      </div>

      {toast ? <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} /> : null}

      {/* Confirm/Reject modals — destructive actions require explicit confirmation (UX-03) */}
      <ConfirmModal
        open={pendingAction === 'confirm'}
        title="Confirm this match?"
        description={
          <>
            This will mark <strong>EOB #{match?.eob_document_id ?? '—'} ↔ Bill #{match?.bill_document_id ?? '—'}</strong> as
            confirmed and link the documents in Paperless. This action cannot be easily undone.
          </>
        }
        confirmLabel="Confirm match"
        confirmVariant="success"
        busy={saving === 'confirm'}
        onConfirm={() => void executeConfirm()}
        onCancel={() => setPendingAction(null)}
      />
      <ConfirmModal
        open={pendingAction === 'reject'}
        title="Reject this match?"
        description={
          <>
            This will reject <strong>EOB #{match?.eob_document_id ?? '—'} ↔ Bill #{match?.bill_document_id ?? '—'}</strong>.
            The documents will need to be re-matched manually if this rejection is incorrect.
          </>
        }
        confirmLabel="Reject match"
        confirmVariant="danger"
        busy={saving === 'reject'}
        onConfirm={() => void executeReject()}
        onCancel={() => setPendingAction(null)}
      />

      <ManualMatchModal
        open={showManualMatch}
        onClose={() => setShowManualMatch(false)}
        sourceDocId={match?.eob_document_id ?? undefined}
        matchId={match?.id ?? undefined}
        triageItemId={triageItemId}
        onMatchCreated={() => {
          setShowManualMatch(false);
          setToast({ message: 'Manual match created successfully.', tone: 'success' });
          void loadMatch();
          onResolved?.();
        }}
      />
    </div>
  );
}
