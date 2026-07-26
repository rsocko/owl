/**
 * UndoToast — auto-dismissing toast with an undo action for reversible operations.
 *
 * Used by the EOB matching workflow so reversible actions auto-confirm immediately
 * but give the user a brief window to undo. See UX-03 audit finding.
 */

import { useEffect, useRef, useState } from 'react';

export interface UndoToastProps {
  /** The toast message to display. */
  message: string;
  /** Milliseconds before auto-dismiss. Defaults to 5000. */
  duration?: number;
  /** Called when the user clicks Undo within the timeout window. */
  onUndo: () => void;
  /** Called when the toast is dismissed (either via undo, manual dismiss, or timeout). */
  onDismiss: () => void;
}

export default function UndoToast({
  message,
  duration = 5000,
  onUndo,
  onDismiss,
}: UndoToastProps) {
  const [remaining, setRemaining] = useState(duration);
  const startRef = useRef(Date.now());
  const intervalRef = useRef<number | null>(null);
  const onDismissRef = useRef(onDismiss);
  const onUndoRef = useRef(onUndo);

  // Keep refs current without restarting the timer
  useEffect(() => { onDismissRef.current = onDismiss; }, [onDismiss]);
  useEffect(() => { onUndoRef.current = onUndo; }, [onUndo]);

  useEffect(() => {
    startRef.current = Date.now();

    const tick = () => {
      const elapsed = Date.now() - startRef.current;
      const left = Math.max(0, duration - elapsed);
      setRemaining(left);
      if (left <= 0) {
        onDismissRef.current();
      }
    };

    intervalRef.current = window.setInterval(tick, 100);
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
      }
    };
  }, [duration]);

  const handleUndo = () => {
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current);
    }
    onUndoRef.current();
  };

  const progress = Math.max(0, remaining / duration) * 100;

  return (
    <div className="toast success undo-toast" role="status" aria-live="polite">
      <span className="toast-message">{message}</span>
      <button
        className="btn ghost sm"
        onClick={handleUndo}
        type="button"
        style={{ marginLeft: 12, fontWeight: 700 }}
      >
        Undo
      </button>
      <div
        className="undo-toast-progress"
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          height: 3,
          width: `${progress}%`,
          background: 'var(--success, #22c55e)',
          transition: 'width 100ms linear',
          borderRadius: '0 0 6px 6px',
        }}
      />
    </div>
  );
}
