/** Standardized toast duration config (UX-12) */
export const TOAST_DURATION = {
  success: 3_000,
  warning: 5_000,
  error: 0, // persistent - dismissed manually
  undo: 8_000, // undo actions need longer window
} as const;

export type ToastTone = 'success' | 'error' | 'warning';

export function getToastDuration(tone: ToastTone | undefined = 'success', hasUndo = false): number {
  if (hasUndo) return TOAST_DURATION.undo;
  return TOAST_DURATION[tone] ?? TOAST_DURATION.success;
}
