import * as Popover from '@radix-ui/react-popover';
import { ChevronDown, ExternalLink, History, MoreHorizontal, Plus, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { endpoints } from '../lib/api';
import {
  DOCUMENT_ACTION_FIELD_SCHEMA,
  DOCUMENT_ACTION_TYPES,
  documentActionDraft,
  normalizeDocumentActionType,
  type DocumentAction,
  type DocumentActionDraft,
  type DocumentActionStatus,
  type DocumentActionType,
} from '../lib/documentActions';
import { Button, EmptyState, ErrorState } from './ui';

interface PaymentCandidate {
  id: string;
  payee?: string | null;
  amount?: number | null;
  currency?: string | null;
  transaction_date?: string | null;
  status?: 'posted' | 'pending' | null;
  account_display_name?: string | null;
  account_last_four?: string | null;
  confidence?: string | null;
  score?: number | null;
  rank?: number | null;
  score_gap_basis_points?: number | null;
  match_reasons?: string[];
}

interface PaymentSearchContext {
  window_start?: string | null;
  window_end?: string | null;
  amount_tolerance?: number | null;
  date_tolerance_days?: number | null;
  eligible_account_count?: number | null;
  candidate_pool_size?: number | null;
}

type PaymentCheckState =
  | 'unchecked'
  | 'checking'
  | 'posted_match'
  | 'pending_match'
  | 'multiple_matches'
  | 'no_match'
  | 'partial_payment'
  | 'source_unavailable'
  | 'stale'
  | 'conflict'
  | 'possible_double_payment'
  | 'confirmed';

interface PaymentCheck {
  state: PaymentCheckState;
  message?: string | null;
  revision: string;
  candidates: PaymentCandidate[];
  search_context?: PaymentSearchContext | null;
}

type PaymentCandidateDecision = 'confirm' | 'reject' | 'reject_all';

interface PendingPaymentDecision {
  action: PaymentCandidateDecision;
  candidate?: PaymentCandidate;
}

const RESOLVED_STATUSES = new Set(['completed', 'dismissed', 'not_an_action']);

function formatDate(value?: string | null) {
  if (!value) return 'No due date';
  const parsed = new Date(value.includes('T') ? value : `${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatCurrency(value?: number | null, currency = 'USD') {
  return value == null ? '—' : value.toLocaleString(undefined, { style: 'currency', currency });
}

function formatMatchReason(reason: string) {
  const [kind, ...strengthParts] = reason.split('_');
  const labels: Record<string, string> = {
    amount: 'Amount',
    date: 'Date',
    payee: 'Vendor',
    account: 'Account',
  };
  const strengths: Record<string, string> = {
    exact: 'exact',
    strong: 'strong',
    partial: 'partial',
    none: 'no match',
    notprovided: 'not used',
  };
  const strength = strengthParts.join('');
  return `${labels[kind] || kind}: ${strengths[strength] || strengthParts.join(' ')}`;
}

function nextMondayAtNine() {
  const date = new Date();
  const days = ((8 - date.getDay()) % 7) || 7;
  date.setDate(date.getDate() + days);
  date.setHours(9, 0, 0, 0);
  return date.toISOString();
}

export default function DocumentActionsPanel({
  actions,
  selectedActionId,
  onSelectAction,
  onSave,
  onCreate,
  onStatus,
  onRefresh,
  busy = false,
  error = '',
  loading = false,
  primaryAction,
  onDirtyChange,
}: {
  actions: DocumentAction[];
  selectedActionId: number | null;
  onSelectAction: (actionId: number) => void;
  onSave: (action: DocumentAction, draft: DocumentActionDraft) => Promise<void>;
  onCreate: (draft: DocumentActionDraft) => Promise<void>;
  onStatus: (action: DocumentAction, status: DocumentActionStatus, snoozedUntil?: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  busy?: boolean;
  error?: string;
  loading?: boolean;
  primaryAction?: ReactNode;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const selectedAction = useMemo(
    () => actions.find((action) => action.id === selectedActionId) ?? actions[0] ?? null,
    [actions, selectedActionId],
  );
  const [draft, setDraft] = useState<DocumentActionDraft>(() => documentActionDraft(selectedAction));
  const [newActionOpen, setNewActionOpen] = useState(false);
  const [newAction, setNewAction] = useState<DocumentActionDraft>(() => documentActionDraft());
  const [confirmNotAction, setConfirmNotAction] = useState<DocumentAction | null>(null);
  const [paymentCheck, setPaymentCheck] = useState<PaymentCheck | null>(null);
  const [paymentLoading, setPaymentLoading] = useState(false);
  const [paymentError, setPaymentError] = useState('');
  const [paymentBusy, setPaymentBusy] = useState(false);
  const [confirmPayment, setConfirmPayment] = useState(false);
  const [selectedPaymentCandidateId, setSelectedPaymentCandidateId] = useState<string | null>(null);
  const [pendingPaymentDecision, setPendingPaymentDecision] = useState<PendingPaymentDecision | null>(null);
  const [selectionWarning, setSelectionWarning] = useState('');

  useEffect(() => {
    setDraft(documentActionDraft(selectedAction));
  }, [selectedAction]);

  useEffect(() => {
    setPaymentCheck(null);
    setPaymentError('');
    setSelectedPaymentCandidateId(null);
    setPendingPaymentDecision(null);
    if (!selectedAction || normalizeDocumentActionType(selectedAction.action_type) !== 'PAY' || !selectedAction.obligation_id) return;
    let cancelled = false;
    setPaymentLoading(true);
    void endpoints.actionQueue.paymentCheck(String(selectedAction.id))
      .then((response) => {
        if (!cancelled) setPaymentCheck((response as { payment_check?: PaymentCheck }).payment_check ?? null);
      })
      .catch((requestError) => {
        if (!cancelled) setPaymentError(requestError instanceof Error ? requestError.message : 'Payment status is unavailable.');
      })
      .finally(() => {
        if (!cancelled) setPaymentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedAction]);

  const dirty = Boolean(selectedAction && JSON.stringify(draft) !== JSON.stringify(documentActionDraft(selectedAction)));
  const valid = Boolean(
    draft.title.trim()
    && (draft.action_type !== 'PAY' || (draft.amount.trim() && Number.isFinite(Number(draft.amount)) && Number(draft.amount) >= 0)),
  );

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (!selectedAction || paymentCheck?.state !== 'checking') return;
    let cancelled = false;
    const poll = window.setInterval(() => {
      void endpoints.actionQueue.paymentCheck(String(selectedAction.id))
        .then((response) => {
          if (!cancelled) {
            setPaymentCheck((response as { payment_check?: PaymentCheck }).payment_check ?? null);
            setPaymentError('');
          }
        })
        .catch((requestError) => {
          if (!cancelled) setPaymentError(requestError instanceof Error ? requestError.message : 'Could not refresh payment status.');
        });
    }, 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(poll);
    };
  }, [paymentCheck?.state, selectedAction]);

  const checkForPayment = async () => {
    if (!selectedAction) return;
    setPaymentBusy(true);
    setPaymentError('');
    try {
      const current = paymentCheck ?? (await endpoints.actionQueue.paymentCheck(String(selectedAction.id)) as { payment_check: PaymentCheck }).payment_check;
      const response = await endpoints.actionQueue.checkPayment(String(selectedAction.id), {
        revision: current.revision,
        idempotency_key: `owl-check-payment:${selectedAction.id}:${current.revision}`,
      }) as { payment: PaymentCheck };
      setPaymentCheck(response.payment);
      setSelectedPaymentCandidateId(null);
    } catch (requestError) {
      setPaymentError(requestError instanceof Error ? requestError.message : 'Could not check for payment.');
    } finally {
      setPaymentBusy(false);
    }
  };

  const reviewPaymentCandidate = async (decision: PendingPaymentDecision) => {
    if (!selectedAction || !paymentCheck) return;
    const evidenceId = decision.candidate?.id;
    setPaymentBusy(true);
    setPaymentError('');
    try {
      const response = await endpoints.actionQueue.reviewPaymentCandidate(
        String(selectedAction.id),
        {
          action: decision.action,
          revision: paymentCheck.revision,
          idempotency_key: [
            'owl-review-payment',
            decision.action,
            selectedAction.id,
            paymentCheck.revision,
            evidenceId || 'all',
          ].join(':'),
          ...(evidenceId ? { evidence_id: evidenceId } : {}),
        },
      ) as { payment_check: PaymentCheck };
      setPaymentCheck(response.payment_check);
      setSelectedPaymentCandidateId(null);
      setPendingPaymentDecision(null);
      if (decision.action === 'confirm' && decision.candidate?.status === 'posted') {
        await onRefresh();
      }
    } catch (requestError) {
      setPaymentError(requestError instanceof Error ? requestError.message : 'Could not review this payment candidate.');
    } finally {
      setPaymentBusy(false);
    }
  };

  const confirmPaymentAndComplete = async () => {
    if (!selectedAction || !paymentCheck?.candidates[0]) return;
    setPaymentBusy(true);
    try {
      await endpoints.actionQueue.confirmPaymentAndComplete(String(selectedAction.id), {
        revision: paymentCheck.revision,
        idempotency_key: `owl-confirm-payment:${selectedAction.id}:${paymentCheck.revision}`,
      });
      setConfirmPayment(false);
      await onRefresh();
    } catch (requestError) {
      setPaymentError(requestError instanceof Error ? requestError.message : 'Could not confirm payment.');
    } finally {
      setPaymentBusy(false);
    }
  };

  return (
    <div className="action-workspace-tab">
      <div className="action-set-header">
        <div><strong>Actions on this document</strong><span>Each action has an independent lifecycle.</span></div>
        <Button size="sm" onClick={() => setNewActionOpen((current) => !current)}>
          <Plus size={15} /> {newActionOpen ? 'Cancel' : 'Add action'}
        </Button>
      </div>

      {error ? <ErrorState message={`Actions unavailable: ${error}`} onRetry={() => void onRefresh()} /> : null}
      {loading ? <div className="intake-inline-state"><RefreshCw className="intake-spin" size={15} /> Loading actions…</div> : null}
      {selectionWarning ? <div className="intake-inline-error" role="alert">{selectionWarning}</div> : null}

      {newActionOpen ? (
        <ActionForm
          draft={newAction}
          onChange={setNewAction}
          footer={(
            <Button
              variant="primary"
              size="sm"
              disabled={!newAction.title.trim() || busy}
              onClick={() => void onCreate(newAction).then(() => {
                setNewAction(documentActionDraft());
                setNewActionOpen(false);
              })}
            >
              Add action
            </Button>
          )}
        />
      ) : null}

      {!loading && !error && !actions.length ? (
        <EmptyState title="No action analysis yet" desc="Refresh analysis or add an action manually." />
      ) : null}

      {actions.length ? (
        <div className="action-set-list">
          {actions.map((action) => (
            <div className="action-set-row" key={action.id}>
              <button
                type="button"
                className={action.id === selectedAction?.id ? 'active' : ''}
                onClick={() => {
                  if (dirty && action.id !== selectedAction?.id) {
                    setSelectionWarning('Save the selected action before switching.');
                    return;
                  }
                  setSelectionWarning('');
                  onSelectAction(action.id);
                }}
              >
                <span>
                  <strong>{normalizeDocumentActionType(action.action_type)} · {action.title || `Action #${action.id}`}</strong>
                  <small>{formatDate(action.due_date)}</small>
                </span>
                <em>{action.status === 'not_an_action' ? 'Not an action' : action.status || 'pending'}</em>
              </button>
              <ActionOutcomeMenu
                action={action}
                disabled={busy}
                onStatus={(status, until) => {
                  if (dirty && action.id === selectedAction?.id) {
                    setSelectionWarning('Save the selected action before choosing an outcome.');
                    return;
                  }
                  if (status === 'not_an_action') setConfirmNotAction(action);
                  else void onStatus(action, status, until);
                }}
                onHistory={() => {
                  onSelectAction(action.id);
                  document.querySelector<HTMLDetailsElement>('#selected-action-history')?.setAttribute('open', '');
                }}
              />
            </div>
          ))}
        </div>
      ) : null}

      {selectedAction ? (
        <>
          <div className="selected-action-context">
            <div>
              <span>{selectedAction.status === 'not_an_action' ? 'Not an action' : selectedAction.status || 'pending'}</span>
              <span>{selectedAction.is_primary === false || selectedAction.origin === 'user' ? 'Manual or split action' : 'Inferred'}</span>
              <span>{formatDate(selectedAction.due_date)}</span>
            </div>
            <strong>{Math.round(selectedAction.confidence ?? 0)}% confidence</strong>
          </div>
          <ActionForm
            draft={draft}
            onChange={setDraft}
            footer={(
              <div className="action-save-row">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={!dirty || !valid || busy}
                  onClick={() => void onSave(selectedAction, draft)}
                >
                  Save action
                </Button>
                <span>{dirty ? 'Unsaved changes' : 'Saved'}</span>
              </div>
            )}
          />
          {normalizeDocumentActionType(selectedAction.action_type) === 'PAY' && selectedAction.obligation_id ? (
            <PaymentCheckPanel
              paymentCheck={paymentCheck}
              error={paymentError}
              loading={paymentLoading}
              busy={paymentBusy}
              eligible={Boolean(
                selectedAction.amount
                && (selectedAction.payee_or_owner || selectedAction.correspondent)
                && (selectedAction.due_date || selectedAction.document_date),
              )}
              onCheck={() => void checkForPayment()}
              onConfirm={() => setConfirmPayment(true)}
              selectedCandidateId={selectedPaymentCandidateId}
              onSelectCandidate={setSelectedPaymentCandidateId}
              onReviewCandidate={(action, candidate) => setPendingPaymentDecision({ action, candidate })}
            />
          ) : null}
          <QuickWorkLinks action={selectedAction} />
          <details className="action-disclosure">
            <summary>Why OWL suggested this <ChevronDown size={16} /></summary>
            <p>{selectedAction.ai_reasoning || 'No classifier explanation was recorded.'}</p>
          </details>
          <details className="action-disclosure" id="selected-action-history">
            <summary><span><History size={15} /> Action history</span><ChevronDown size={16} /></summary>
            {selectedAction.history?.length ? (
              <ol>
                {selectedAction.history.map((event) => (
                  <li key={event.id}>
                    <time>{formatDate(event.created_at)}</time>
                    <span>{event.event_type || `${event.previous_status || 'unknown'} → ${event.new_status || 'unknown'}`}</span>
                  </li>
                ))}
              </ol>
            ) : <p>No lifecycle changes have been recorded.</p>}
          </details>
          {primaryAction}
        </>
      ) : null}

      {confirmNotAction ? (
        <div className="workspace-dialog-backdrop">
          <section role="dialog" aria-modal="true" aria-labelledby="shared-not-action-title" className="workspace-dialog">
            <h2 id="shared-not-action-title">Mark as Not an action?</h2>
            <p>Use this when OWL incorrectly identified work that does not exist. This records classifier feedback without filing or changing the document.</p>
            <div>
              <Button onClick={() => setConfirmNotAction(null)}>Cancel</Button>
              <Button variant="primary" onClick={() => void onStatus(confirmNotAction, 'not_an_action').then(() => setConfirmNotAction(null))}>Save feedback</Button>
            </div>
          </section>
        </div>
      ) : null}

      {confirmPayment && selectedAction && paymentCheck ? (
        <div className="workspace-dialog-backdrop">
          <section role="dialog" aria-modal="true" aria-labelledby="shared-confirm-payment-title" className="workspace-dialog">
            <h2 id="shared-confirm-payment-title">Confirm this posted payment?</h2>
            <p>OWL will record your confirmation and complete the selected action and obligation in one operation.</p>
            <div>
              <Button onClick={() => setConfirmPayment(false)}>Cancel</Button>
              <Button variant="primary" onClick={() => void confirmPaymentAndComplete()}>Confirm payment and complete</Button>
            </div>
          </section>
        </div>
      ) : null}

      {pendingPaymentDecision && selectedAction && paymentCheck ? (
        <div className="workspace-dialog-backdrop">
          <section role="dialog" aria-modal="true" aria-labelledby="review-payment-candidate-title" className="workspace-dialog">
            <h2 id="review-payment-candidate-title">
              {pendingPaymentDecision.action === 'confirm'
                ? 'Confirm this transaction?'
                : pendingPaymentDecision.action === 'reject_all'
                  ? 'None of these transactions match?'
                  : 'Discard this candidate?'}
            </h2>
            <p>
              {pendingPaymentDecision.action === 'confirm'
                ? pendingPaymentDecision.candidate?.status === 'posted'
                  ? 'OWL will use this posted transaction as payment evidence and complete the action and obligation.'
                  : 'OWL will record this pending transaction as the selected match without completing the obligation.'
                : pendingPaymentDecision.action === 'reject_all'
                  ? 'OWL will reject every suggested transaction. The payment action will remain open.'
                  : 'OWL will remove this transaction from the candidate list. You can run the payment check again later.'}
            </p>
            <div>
              <Button onClick={() => setPendingPaymentDecision(null)} disabled={paymentBusy}>Cancel</Button>
              <Button
                variant={pendingPaymentDecision.action === 'confirm' ? 'primary' : 'danger'}
                disabled={paymentBusy}
                onClick={() => void reviewPaymentCandidate(pendingPaymentDecision)}
              >
                {paymentBusy
                  ? 'Saving…'
                  : pendingPaymentDecision.action === 'confirm'
                    ? pendingPaymentDecision.candidate?.status === 'posted'
                      ? 'Confirm and complete'
                      : 'Confirm pending match'
                    : pendingPaymentDecision.action === 'reject_all'
                      ? 'Reject all candidates'
                      : 'Discard candidate'}
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function ActionForm({
  draft,
  onChange,
  footer,
}: {
  draft: DocumentActionDraft;
  onChange: (draft: DocumentActionDraft) => void;
  footer: ReactNode;
}) {
  const schema = DOCUMENT_ACTION_FIELD_SCHEMA[draft.action_type];
  return (
    <div className="action-workspace-form">
      <label><span>Type</span><select value={draft.action_type} onChange={(event) => onChange({ ...draft, action_type: event.target.value as DocumentActionType })}>{DOCUMENT_ACTION_TYPES.map((type) => <option key={type}>{type}</option>)}</select></label>
      <label className="wide"><span>Title</span><input value={draft.title} onChange={(event) => onChange({ ...draft, title: event.target.value })} /></label>
      {schema.owner ? <label><span>{schema.owner}</span><input value={draft.payee_or_owner} onChange={(event) => onChange({ ...draft, payee_or_owner: event.target.value })} /></label> : null}
      {schema.amount ? <label><span>Amount</span><input type="number" min="0" step="0.01" value={draft.amount} onChange={(event) => onChange({ ...draft, amount: event.target.value })} /></label> : null}
      <label><span>{schema.due}</span><input type="date" value={draft.due_date} onChange={(event) => onChange({ ...draft, due_date: event.target.value })} /></label>
      {schema.reference ? <label><span>{schema.reference}</span><input value={draft.reference} onChange={(event) => onChange({ ...draft, reference: event.target.value })} /></label> : null}
      <label className="wide"><span>Details</span><textarea rows={3} value={draft.summary} onChange={(event) => onChange({ ...draft, summary: event.target.value })} /></label>
      <div className="wide">{footer}</div>
    </div>
  );
}

function ActionOutcomeMenu({
  action,
  disabled,
  onStatus,
  onHistory,
}: {
  action: DocumentAction;
  disabled: boolean;
  onStatus: (status: DocumentActionStatus, snoozedUntil?: string) => void;
  onHistory: () => void;
}) {
  const resolved = RESOLVED_STATUSES.has((action.status || 'pending').toLowerCase());
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" className="action-set-menu-trigger" aria-label={`More outcomes for ${action.title || `action ${action.id}`}`} disabled={disabled}>
          <MoreHorizontal size={18} />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="workspace-popover" sideOffset={5} align="end">
          {resolved ? <button type="button" onClick={() => onStatus('pending')}>Move back to Pending</button> : (
            <>
              <button type="button" onClick={() => onStatus('completed')}>Mark done</button>
              <button type="button" onClick={() => onStatus('snoozed', nextMondayAtNine())}>Remind later</button>
              <button type="button" onClick={() => onStatus('not_an_action')}>Not an action</button>
              <button type="button" onClick={() => onStatus('dismissed')}>Won't do</button>
            </>
          )}
          <button type="button" onClick={onHistory}>View history</button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function PaymentCheckPanel({
  paymentCheck,
  error,
  loading,
  busy,
  eligible,
  onCheck,
  onConfirm,
  selectedCandidateId,
  onSelectCandidate,
  onReviewCandidate,
}: {
  paymentCheck: PaymentCheck | null;
  error: string;
  loading: boolean;
  busy: boolean;
  eligible: boolean;
  onCheck: () => void;
  onConfirm: () => void;
  selectedCandidateId: string | null;
  onSelectCandidate: (candidateId: string) => void;
  onReviewCandidate: (action: PaymentCandidateDecision, candidate?: PaymentCandidate) => void;
}) {
  const candidates = paymentCheck?.candidates || [];
  const candidate = candidates[0];
  const searchContext = paymentCheck?.search_context;
  const checking = busy || paymentCheck?.state === 'checking';
  const unavailable = !loading && !paymentCheck && Boolean(error);
  const candidateHeading = paymentCheck?.state === 'multiple_matches'
    ? 'Candidates requiring review'
    : paymentCheck?.state === 'no_match'
      ? 'Closest transactions'
      : 'Matched transaction';
  const stateLabel: Record<PaymentCheckState, string> = {
    unchecked: 'Ready to check',
    checking: 'Checking accounts',
    posted_match: 'Posted payment found',
    pending_match: 'Pending payment found',
    multiple_matches: 'Multiple possible payments',
    no_match: 'No payment found',
    partial_payment: 'Partial payment found',
    source_unavailable: 'Payment source unavailable',
    stale: 'Payment result is stale',
    conflict: 'Conflicting payment evidence',
    possible_double_payment: 'Possible double payment',
    confirmed: 'Payment confirmed',
  };
  return (
    <section className={`payment-check-card state-${paymentCheck?.state || 'unchecked'}`} aria-label="Payment check">
      <div className="payment-check-heading">
        <div><span>Payment reconciliation</span><strong aria-live="polite">{loading ? 'Loading payment status…' : unavailable ? 'Payment status unavailable' : stateLabel[paymentCheck?.state || 'unchecked']}</strong></div>
        <Button size="sm" disabled={!eligible || loading || checking} onClick={onCheck}>
          <RefreshCw size={15} className={checking ? 'spin' : ''} /> {checking ? 'Checking…' : unavailable ? 'Retry check' : paymentCheck?.state === 'unchecked' ? 'Check for payment' : 'Check again'}
        </Button>
      </div>
      <p>{error || paymentCheck?.message || 'Search eligible financial accounts for a matching transaction.'}</p>
      {searchContext ? (
        <details className="payment-diagnostics">
          <summary>Search details <ChevronDown size={15} /></summary>
          <dl className="payment-search-context" aria-label="Payment search scope">
            {searchContext.eligible_account_count != null ? (
              <div><dt>Accounts searched</dt><dd>{searchContext.eligible_account_count}</dd></div>
            ) : null}
            {searchContext.candidate_pool_size != null ? (
              <div><dt>Transactions evaluated</dt><dd>{searchContext.candidate_pool_size}</dd></div>
            ) : null}
            {searchContext.window_start && searchContext.window_end ? (
              <div><dt>Search period</dt><dd>{formatDate(searchContext.window_start)}–{formatDate(searchContext.window_end)}</dd></div>
            ) : null}
            {searchContext.amount_tolerance != null ? (
              <div><dt>Amount tolerance</dt><dd>{formatCurrency(searchContext.amount_tolerance, candidate?.currency || 'USD')}</dd></div>
            ) : null}
          </dl>
        </details>
      ) : null}
      {candidates.length ? (
        <section className="payment-candidate-results" aria-label="Payment candidates">
          <div className="payment-candidate-results-heading">
            <strong>{candidateHeading}</strong>
            <span>{candidates.length} {candidates.length === 1 ? 'result' : 'ranked results'}</span>
          </div>
          {paymentCheck?.state === 'no_match' ? (
            <p>These are the closest results, but none met Tyrion&apos;s match threshold.</p>
          ) : null}
          <ol>
            {candidates.map((item, index) => (
              <li key={item.id} className={selectedCandidateId === item.id ? 'selected' : ''}>
                <div className="payment-candidate-heading">
                  {paymentCheck?.state === 'multiple_matches' ? (
                    <input
                      type="radio"
                      name="payment-candidate"
                      value={item.id}
                      checked={selectedCandidateId === item.id}
                      onChange={() => onSelectCandidate(item.id)}
                      aria-label={`Select ${item.payee || `candidate ${index + 1}`}`}
                    />
                  ) : null}
                  <span className="payment-candidate-rank" aria-label={`Rank ${item.rank || index + 1}`}>
                    {item.rank || index + 1}
                  </span>
                  <div>
                    <strong>{item.payee || 'Payee not provided'}</strong>
                    <span>
                      {item.confidence ? `${item.confidence} confidence` : 'Not scored'}
                      {item.score == null ? '' : ` · ${item.score.toFixed(0)}% score`}
                    </span>
                  </div>
                  {paymentCheck?.state === 'multiple_matches' ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => onReviewCandidate('reject', item)}
                    >
                      Discard
                    </Button>
                  ) : null}
                </div>
                <dl className="payment-candidate">
                  <div><dt>Amount</dt><dd>{item.amount == null ? 'Not provided' : formatCurrency(item.amount, item.currency || 'USD')}</dd></div>
                  <div><dt>Transaction</dt><dd>{formatDate(item.transaction_date)} · {item.status || 'unknown'}</dd></div>
                  <div><dt>Account</dt><dd>{item.account_display_name || 'Eligible account'}{item.account_last_four ? ` •••• ${item.account_last_four}` : ''}</dd></div>
                </dl>
                {(item.match_reasons || []).length ? (
                  <details className="payment-candidate-details">
                    <summary>Why this result? <ChevronDown size={14} /></summary>
                    <ul className="payment-match-reasons" aria-label={`Match signals for ${item.payee || `candidate ${index + 1}`}`}>
                      {(item.match_reasons || []).map((reason) => (
                        <li key={reason}>{formatMatchReason(reason)}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </li>
            ))}
          </ol>
        </section>
      ) : paymentCheck?.state === 'no_match' ? (
        <p className="payment-no-candidates">No transaction had enough matching evidence to rank.</p>
      ) : null}
      {paymentCheck?.state === 'posted_match' ? (
        <div className="payment-check-confirm">
          <span>A posted match does not complete this obligation until you confirm it.</span>
          <Button variant="primary" size="sm" disabled={busy} onClick={onConfirm}>Confirm payment and complete</Button>
        </div>
      ) : null}
      {paymentCheck?.state === 'multiple_matches' ? (
        <div className="payment-check-confirm">
          <span>Select the transaction that belongs to this obligation, or reject all suggestions.</span>
          <div className="payment-review-actions">
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => onReviewCandidate('reject_all')}
            >
              None of these
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={busy || !selectedCandidateId}
              onClick={() => {
                const selected = candidates.find((item) => item.id === selectedCandidateId);
                if (selected) onReviewCandidate('confirm', selected);
              }}
            >
              {candidates.find((item) => item.id === selectedCandidateId)?.status === 'pending'
                ? 'Confirm pending match'
                : 'Confirm selected'}
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function QuickWorkLinks({ action }: { action: DocumentAction }) {
  const links: Array<{ label: string; href: string }> = [];
  if (action.recommended_cta?.url) links.push({ label: action.recommended_cta.label || 'Open action link', href: action.recommended_cta.url });
  if (action.extracted_data?.payment_url) links.push({ label: 'Pay online', href: action.extracted_data.payment_url });
  if (action.extracted_data?.phone) links.push({ label: `Call ${action.extracted_data.phone}`, href: `tel:${action.extracted_data.phone}` });
  if (action.extracted_data?.email) links.push({ label: `Email ${action.extracted_data.email}`, href: `mailto:${action.extracted_data.email}` });
  for (const link of action.extracted_data?.links ?? []) {
    if (!/^https?:\/\//i.test(link.url)) continue;
    if (!links.some((candidate) => candidate.href === link.url)) links.push({ label: link.label || 'Open link', href: link.url });
  }
  if (!links.length) return null;
  return (
    <div className="action-work-links">
      <strong>Do the work</strong>
      <div>{links.map((link) => <a key={link.href} href={link.href} target={link.href.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer">{link.label}<ExternalLink size={14} /></a>)}</div>
      <p>Links and contact details are extracted from this document.</p>
    </div>
  );
}
