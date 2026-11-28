import { cn } from '@/lib/cn';

import { WEEKDAY_LABELS, WEEKDAYS } from '../format';
import type { SlotDraft } from './cadence-draft';

/**
 * The post slots across one week, as the owner will live them: a column per day, a mark per
 * slot. Solid marks are the slots every week uses; outlined ones are used only in weeks with more
 * than three posts. It repeats what the form says, so it is hidden from screen readers.
 */
export function WeekStrip({ slots }: { slots: readonly Pick<SlotDraft, 'key' | 'day' | 'time' | 'kind'>[] }) {
  return (
    <div aria-hidden="true" className="max-w-measure">
      <ol className="grid grid-cols-7 gap-1 rounded-lg border border-line bg-sunken p-2">
        {WEEKDAYS.map((day) => {
          const today = slots.filter((slot) => slot.day === day).sort((a, b) => a.time.localeCompare(b.time));
          return (
            <li key={day} className="flex min-h-24 min-w-0 flex-col items-stretch gap-1 rounded-md bg-sheet p-1.5">
              <span className="text-center text-xs font-semibold text-ink-muted">
                {WEEKDAY_LABELS[day].slice(0, 3)}
              </span>
              {today.map((slot) => (
                <span
                  key={slot.key}
                  className={cn(
                    'rounded-sm px-1 py-0.5 text-center text-xs tabular-nums',
                    slot.kind === 'primary'
                      ? 'bg-ink font-semibold text-ink-inverse'
                      : 'border border-dashed border-line-strong text-ink-muted',
                  )}
                >
                  {slot.time}
                </span>
              ))}
            </li>
          );
        })}
      </ol>
      <p className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-5 rounded-sm bg-ink" />
          Every week
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-5 rounded-sm border border-dashed border-line-strong" />
          Only in weeks with more than three posts
        </span>
      </p>
    </div>
  );
}
