import { cn } from '@/lib/cn';

/** LinkedIn member tokens last 60 days, with no refresh (`domain.settings.TOKEN_LIFETIME_DAYS`). */
export const TOKEN_LIFETIME_DAYS = 60;
/** The reconnect banner shows from this day (`TOKEN_BANNER_DAY`). */
export const TOKEN_BANNER_DAY = 50;
/** Days the app sends a reminder (`TOKEN_ALERT_DAYS`). */
export const TOKEN_ALERT_DAYS: readonly number[] = [53, 57, 59];

const TICKS = [0, 10, 20, 30, 40, 50, 60];

function percent(day: number): string {
  return `${String((Math.min(Math.max(day, 0), TOKEN_LIFETIME_DAYS) / TOKEN_LIFETIME_DAYS) * 100)}%`;
}

/** "Day 52 of 60. 8 days left; reconnect before then." */
export function tokenAgeText(day: number): string {
  const left = Math.max(TOKEN_LIFETIME_DAYS - day, 0);
  if (left === 0) return `Day ${String(day)} of ${String(TOKEN_LIFETIME_DAYS)}. The token has run out.`;
  const days = left === 1 ? '1 day left' : `${String(left)} days left`;
  return day >= TOKEN_BANNER_DAY
    ? `Day ${String(day)} of ${String(TOKEN_LIFETIME_DAYS)}. ${days}: reconnect before then.`
    : `Day ${String(day)} of ${String(TOKEN_LIFETIME_DAYS)}. ${days}.`;
}

/**
 * The token's 60 days as a ruler: the ink line is how far it has run, the shaded end is the
 * reconnect window (the banner from day 50), and the small ticks are the reminder days. A meter for
 * assistive technology, with the day and what it means in words.
 */
export function TokenRuler({ day, needsReconnect = false }: { day: number; needsReconnect?: boolean }) {
  const clamped = Math.min(Math.max(day, 0), TOKEN_LIFETIME_DAYS);
  const late = clamped >= TOKEN_BANNER_DAY;
  return (
    <div className="max-w-measure">
      <div
        role="meter"
        aria-label="LinkedIn token age"
        aria-valuemin={0}
        aria-valuemax={TOKEN_LIFETIME_DAYS}
        aria-valuenow={clamped}
        aria-valuetext={tokenAgeText(clamped)}
        className="relative h-10"
      >
        {/* The track and the reconnect window. */}
        <div className="absolute inset-x-0 top-3 h-2 rounded-full bg-wash-strong" />
        <div
          className="absolute top-3 right-0 h-2 rounded-r-full bg-warn-soft"
          style={{ left: percent(TOKEN_BANNER_DAY) }}
        />
        {/* Days run so far. */}
        <div
          className={cn(
            'absolute left-0 top-3 h-2 rounded-l-full',
            needsReconnect ? 'bg-danger' : late ? 'bg-warn' : 'bg-ink',
          )}
          style={{ width: percent(clamped) }}
        />
        {/* Today. */}
        <div
          className={cn(
            'absolute top-1 h-6 w-1 -translate-x-1/2 rounded-full ring-2 ring-sheet',
            needsReconnect ? 'bg-danger' : late ? 'bg-warn' : 'bg-ink',
          )}
          style={{ left: percent(clamped) }}
        />
        {/* Reminder days. */}
        {TOKEN_ALERT_DAYS.map((alert) => (
          <div
            key={alert}
            className="absolute top-6 h-2 w-px -translate-x-1/2 bg-warn"
            style={{ left: percent(alert) }}
          />
        ))}
      </div>
      <div aria-hidden="true" className="relative h-5 text-xs tabular-nums text-ink-subtle">
        {TICKS.map((tick) => (
          <span
            key={tick}
            className={cn(
              'absolute -translate-x-1/2',
              tick === 0 && 'translate-x-0',
              tick === TOKEN_LIFETIME_DAYS && '-translate-x-full',
            )}
            style={{ left: percent(tick) }}
          >
            {tick === 0 ? 'Day 0' : String(tick)}
          </span>
        ))}
      </div>
      <p className="mt-1 text-sm text-ink-muted">
        Reminders on days {TOKEN_ALERT_DAYS.join(', ')}; the banner shows from day {TOKEN_BANNER_DAY}.
      </p>
    </div>
  );
}
