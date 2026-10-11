import { useCallback, useEffect, useState } from 'react';
import {
  endpoints,
  type MonarchDeliveryResult,
  type MonarchDeliveryState,
} from '../lib/api';
import ConfirmModal from './ConfirmModal';

interface MonarchDeliveryActionProps {
  documentId: number;
}

const LABELS: Record<MonarchDeliveryState, string> = {
  not_submitted: 'Send to Monarch',
  submitting: 'Sending to Monarch',
  unknown: 'Reconcile Monarch status',
  processing: 'Check Monarch status',
  acknowledged: 'Sent to Monarch',
  retryable: 'Retry Monarch send',
  failed: 'Monarch send failed',
  blocked_recovery: 'Monarch recovery document',
};

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Monarch delivery status is unavailable.';
}

export default function MonarchDeliveryAction({
  documentId,
}: MonarchDeliveryActionProps) {
  const [delivery, setDelivery] = useState<MonarchDeliveryResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<'send' | 'retry' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDelivery(await endpoints.documents.monarchDelivery.status(documentId));
      setError(null);
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(
    async (action: 'send' | 'retry' | 'reconcile') => {
      setBusy(true);
      try {
        const result = await endpoints.documents.monarchDelivery[action](documentId);
        setDelivery(result);
        setError(null);
      } catch (actionError) {
        setError(errorMessage(actionError));
      } finally {
        setBusy(false);
        setConfirming(null);
      }
    },
    [documentId],
  );

  if (loading) {
    return (
      <button className="monarch-delivery-action" disabled aria-busy="true">
        Loading Monarch status
      </button>
    );
  }

  if (!delivery) {
    return (
      <button
        className="monarch-delivery-action monarch-delivery-error"
        onClick={() => void load()}
        title={error ?? undefined}
      >
        Retry Monarch status
      </button>
    );
  }

  const action =
    delivery.state === 'not_submitted' && delivery.can_send
      ? 'send'
      : delivery.state === 'unknown' ||
          delivery.state === 'processing' ||
          delivery.display_tag_pending
          ? 'reconcile'
          : delivery.can_retry
            ? 'retry'
          : null;
  const disabled = busy || action === null;
  const label = busy
    ? action === 'reconcile'
      ? 'Reconciling Monarch status'
      : 'Sending to Monarch'
    : delivery.state === 'not_submitted' && !delivery.can_send
      ? 'Monarch send disabled'
      : delivery.display_tag_pending
        ? 'Sync sent status tag'
        : delivery.state === 'processing' && delivery.acknowledged
          ? 'Sent; processing in Monarch'
      : LABELS[delivery.state];

  const activate = () => {
    if (action === 'send' || action === 'retry') {
      setConfirming(action);
      return;
    }
    if (action === 'reconcile') void run('reconcile');
  };

  return (
    <>
      <span className="monarch-delivery-control">
        <button
          className={`monarch-delivery-action state-${delivery.state}`}
          disabled={disabled}
          aria-busy={busy}
          onClick={activate}
          title={error ?? delivery.reason_code ?? undefined}
        >
          {label}
        </button>
        <span className="sr-only" aria-live="polite">
          {error ?? `Monarch delivery status: ${delivery.state.replaceAll('_', ' ')}`}
        </span>
      </span>
      <ConfirmModal
        open={confirming !== null}
        title={confirming === 'retry' ? 'Retry sending to Monarch?' : 'Send to Monarch?'}
        description={
          confirming === 'retry'
            ? 'OWL will reconcile the prior attempt first. It will resend only when Tyrion confirms that no delivery exists.'
            : 'OWL will securely stream this canonical Paperless document to Tyrion for Monarch processing. The document will not leave a stored copy in OWL.'
        }
        confirmLabel={confirming === 'retry' ? 'Reconcile and retry' : 'Send to Monarch'}
        confirmVariant="primary"
        busy={busy}
        onConfirm={() => void run(confirming ?? 'send')}
        onCancel={() => setConfirming(null)}
      />
    </>
  );
}
