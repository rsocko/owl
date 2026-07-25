import { useMemo } from 'react';

type Frequency = 'every_minutes' | 'hourly' | 'daily' | 'weekly';

type ParsedSchedule = {
  frequency: Frequency;
  minute: number;
  hour: number;
  dayOfWeek: number;
  intervalMinutes: number;
};

const DAYS_OF_WEEK = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'] as const;

const MINUTE_INTERVALS = [5, 10, 15, 20, 30] as const;

function parseCron(cron: string): ParsedSchedule {
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) {
    return { frequency: 'daily', minute: 0, hour: 9, dayOfWeek: 1, intervalMinutes: 30 };
  }
  const [minPart, hourPart, , , dowPart] = parts;

  // Every N minutes: */N * * * *
  if (minPart.startsWith('*/') && hourPart === '*') {
    const n = parseInt(minPart.slice(2), 10);
    return { frequency: 'every_minutes', minute: 0, hour: 0, dayOfWeek: 1, intervalMinutes: n || 30 };
  }

  // Every N hours: 0 */N * * *  (or M */N * * *)
  if (hourPart.startsWith('*/')) {
    const m = parseInt(minPart, 10) || 0;
    const n = parseInt(hourPart.slice(2), 10);
    return { frequency: 'hourly', minute: m, hour: 0, dayOfWeek: 1, intervalMinutes: n > 1 ? n * 60 : 60 };
  }

  const minute = parseInt(minPart, 10) || 0;
  const hour = parseInt(hourPart, 10) || 0;

  // Weekly: specific DOW
  if (dowPart !== '*') {
    const dow = parseInt(dowPart, 10) || 0;
    return { frequency: 'weekly', minute, hour, dayOfWeek: dow, intervalMinutes: 30 };
  }

  // Daily
  return { frequency: 'daily', minute, hour, dayOfWeek: 1, intervalMinutes: 30 };
}

function toCron(parsed: ParsedSchedule): string {
  switch (parsed.frequency) {
    case 'every_minutes':
      return `*/${parsed.intervalMinutes} * * * *`;
    case 'hourly':
      return `${parsed.minute} * * * *`;
    case 'daily':
      return `${parsed.minute} ${parsed.hour} * * *`;
    case 'weekly':
      return `${parsed.minute} ${parsed.hour} * * ${parsed.dayOfWeek}`;
  }
}

function describeSchedule(parsed: ParsedSchedule): string {
  const pad = (n: number) => n.toString().padStart(2, '0');
  const timeStr = `${pad(parsed.hour % 12 || 12)}:${pad(parsed.minute)} ${parsed.hour < 12 ? 'AM' : 'PM'}`;
  switch (parsed.frequency) {
    case 'every_minutes':
      return `Every ${parsed.intervalMinutes} minutes`;
    case 'hourly':
      return `Every hour at :${pad(parsed.minute)}`;
    case 'daily':
      return `Daily at ${timeStr}`;
    case 'weekly':
      return `Every ${DAYS_OF_WEEK[parsed.dayOfWeek]} at ${timeStr}`;
  }
}

export function CronScheduleEditor({
  value,
  onChange,
  id,
}: {
  value: string;
  onChange: (cron: string) => void;
  id?: string;
}) {
  const parsed = useMemo(() => parseCron(value), [value]);

  function update(patch: Partial<ParsedSchedule>) {
    const next = { ...parsed, ...patch };
    onChange(toCron(next));
  }

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        {/* Frequency picker */}
        <select
          id={id}
          value={parsed.frequency}
          onChange={(e) => update({ frequency: e.target.value as Frequency })}
          style={{ flex: '0 0 auto', minWidth: 130 }}
        >
          <option value="every_minutes">Every N min</option>
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>

        {/* Interval for every_minutes */}
        {parsed.frequency === 'every_minutes' && (
          <select
            value={parsed.intervalMinutes}
            onChange={(e) => update({ intervalMinutes: parseInt(e.target.value, 10) })}
            style={{ flex: '0 0 auto', minWidth: 80 }}
          >
            {MINUTE_INTERVALS.map((n) => (
              <option key={n} value={n}>
                {n} min
              </option>
            ))}
          </select>
        )}

        {/* Minute-of-hour for hourly */}
        {parsed.frequency === 'hourly' && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.85rem' }}>
            at&nbsp;:
            <input
              type="number"
              min={0}
              max={59}
              value={parsed.minute}
              onChange={(e) => update({ minute: clamp(parseInt(e.target.value, 10) || 0, 0, 59) })}
              style={{ width: 56 }}
            />
          </label>
        )}

        {/* Day picker for weekly */}
        {parsed.frequency === 'weekly' && (
          <select
            value={parsed.dayOfWeek}
            onChange={(e) => update({ dayOfWeek: parseInt(e.target.value, 10) })}
            style={{ flex: '0 0 auto', minWidth: 110 }}
          >
            {DAYS_OF_WEEK.map((name, i) => (
              <option key={i} value={i}>
                {name}
              </option>
            ))}
          </select>
        )}

        {/* Time picker for daily / weekly */}
        {(parsed.frequency === 'daily' || parsed.frequency === 'weekly') && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.85rem' }}>
            at
            <input
              type="time"
              value={`${parsed.hour.toString().padStart(2, '0')}:${parsed.minute.toString().padStart(2, '0')}`}
              onChange={(e) => {
                const [h, m] = e.target.value.split(':').map(Number);
                update({ hour: h, minute: m });
              }}
              style={{ width: 110 }}
            />
          </label>
        )}
      </div>

      {/* Human-readable summary + raw cron */}
      <div
        style={{
          marginTop: 6,
          fontSize: '0.78rem',
          color: 'var(--text-muted)',
          display: 'flex',
          gap: 12,
          alignItems: 'center',
        }}
      >
        <span>{describeSchedule(parsed)}</span>
        <code style={{ opacity: 0.6, fontSize: '0.75rem' }}>{value}</code>
      </div>
    </div>
  );
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
