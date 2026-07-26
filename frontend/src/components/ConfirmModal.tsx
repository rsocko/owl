/**
 * ConfirmModal — reusable confirmation dialog for destructive/irreversible actions.
 *
 * Used by the EOB matching workflow to ensure all destructive actions (confirm match,
 * reject match, mark as orphan) require explicit confirmation, while reversible actions
 * use auto-confirm + undo toast instead. See UX-03 audit finding.
 */

import type { ReactNode } from 'react';
import { Modal, Button } from './ui';

export interface ConfirmModalProps {
  /** Whether the modal is visible. */
  open: boolean;
  /** Modal heading. */
  title: string;
  /** Descriptive body text explaining the action and its consequences. */
  description: ReactNode;
  /** Label for the primary confirm button. Defaults to "Confirm". */
  confirmLabel?: string;
  /** Visual variant for the confirm button. Defaults to "danger". */
  confirmVariant?: 'primary' | 'success' | 'danger';
  /** Label for the cancel button. Defaults to "Cancel". */
  cancelLabel?: string;
  /** Whether the confirm action is in progress (shows spinner/disables buttons). */
  busy?: boolean;
  /** Called when the user confirms the action. */
  onConfirm: () => void;
  /** Called when the user cancels or closes the modal. */
  onCancel: () => void;
}

export default function ConfirmModal({
  open,
  title,
  description,
  confirmLabel = 'Confirm',
  confirmVariant = 'danger',
  cancelLabel = 'Cancel',
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  if (!open) return null;

  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <div className="btn-group" style={{ justifyContent: 'flex-end' }}>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button variant={confirmVariant} onClick={onConfirm} disabled={busy}>
            {busy ? 'Processing…' : confirmLabel}
          </Button>
        </div>
      }
    >
      <div style={{ lineHeight: 1.5 }}>{description}</div>
    </Modal>
  );
}
