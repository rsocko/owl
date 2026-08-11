export type ReminderPreset = 'tomorrow' | 'next_week';

function toDateInputValue(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function reminderUntil(preset: ReminderPreset, now = new Date()): string {
  const reminder = new Date(now);
  reminder.setDate(reminder.getDate() + (preset === 'tomorrow' ? 1 : 7));
  reminder.setHours(9, 0, 0, 0);
  return reminder.toISOString();
}

export function customReminderUntil(dateValue: string, now = new Date()): string | null {
  if (!dateValue || dateValue < minimumReminderDate(now)) return null;
  const reminder = new Date(`${dateValue}T09:00:00`);
  return Number.isNaN(reminder.getTime()) ? null : reminder.toISOString();
}

export function minimumReminderDate(now = new Date()): string {
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  return toDateInputValue(tomorrow);
}
