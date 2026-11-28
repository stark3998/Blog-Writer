import { Clock } from 'lucide-react';
import { useId } from 'react';

import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';

import type { PlanStatus } from '../lib/plan';
import { clockChangeInWeek, dayLabel, deviceZone, localToUtcMs, shortDate, zoneAbbreviation, zoneCity } from '../lib/time';

interface WeekPickerProps {
  thisWeek: string;
  nextWeek: string;
  selected: string;
  onSelect: (weekStart: string) => void;
  statusOf: (weekStart: string) => PlanStatus | undefined;
  zone: string;
}

/**
 * This week or next week (or the week a link named), and the zone every time on the page is in,
 * with the day the clocks change when the week has one.
 */
export function WeekPicker({ thisWeek, nextWeek, selected, onSelect, statusOf, zone }: WeekPickerProps) {
  const noteId = useId();
  const weeks = [thisWeek, nextWeek, ...(selected !== thisWeek && selected !== nextWeek ? [selected] : [])];
  const label = (week: string) =>
    week === thisWeek ? 'This week' : week === nextWeek ? 'Next week' : `Week of ${shortDate(week)}`;
  const change = clockChangeInWeek(selected, zone);
  const device = deviceZone();
  const midWeek = localToUtcMs(selected, '12:00', zone);

  return (
    <div className="space-y-3">
      <SegmentedControl
        aria-label="Week"
        aria-describedby={noteId}
        value={selected}
        onValueChange={onSelect}
        fullWidth
        className="md:w-auto md:min-w-96"
      >
        {weeks.map((week) => (
          <SegmentedControlItem key={week} value={week}>
            {label(week)}
            {statusOf(week) === 'proposed' ? (
              <span className="rounded-sm bg-warn-soft px-1.5 text-xs font-medium text-warn">To approve</span>
            ) : null}
          </SegmentedControlItem>
        ))}
      </SegmentedControl>
      <div id={noteId} className="space-y-1 text-sm text-ink-muted">
        <p className="flex items-center gap-1.5">
          <Clock aria-hidden="true" className="size-3.5 shrink-0" />
          {`All times are ${zoneCity(zone)} time (${zoneAbbreviation(midWeek, zone)}).`}
        </p>
        {change ? (
          <p>
            {`Clocks go ${change.shiftMinutes < 0 ? 'back' : 'forward'} an hour on ${dayLabel(change.date)}: ${change.from} before, ${change.to} after.`}
          </p>
        ) : null}
        {device && device !== zone ? <p>{`Your device is on ${zoneCity(device)} time; the plan keeps ${zoneCity(zone)} time.`}</p> : null}
      </div>
    </div>
  );
}
