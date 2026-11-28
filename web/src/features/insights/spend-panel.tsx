import { ChartColumn, Table2 } from 'lucide-react';
import { useId, useRef, useState, type KeyboardEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Fieldset } from '@/components/ui/field';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { StatTile } from '@/components/ui/stat-tile';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ProblemAlert } from '@/features/data/problem-alert';
import { cn } from '@/lib/cn';

import { BlockLoading, SubHeading } from '../settings/components/settings-block';
import { formatDay, formatPercent, formatUsd } from '../settings/format';
import { niceMax, rankedTotals, type RankedTotal } from './chart-math';
import { taskLabel } from './labels';
import { useSpend, type SpendDay, type SpendView } from './queries';

const WINDOWS: readonly { days: number; label: string }[] = [
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
];

const PLOT_HEIGHT = 160;

function topTask(day: SpendDay): string | undefined {
  const [first] = Object.entries(day.byTask).sort((a, b) => b[1] - a[1]);
  return first && first[1] > 0 ? taskLabel(first[0]) : undefined;
}

/**
 * Daily AI spend as columns, today's in ink and the others lighter, with the trailing median as a
 * reference line. One tab stop: arrow keys move between days, and the focused or hovered day shows
 * its amount; the table view lists every day.
 */
function DailySpendChart({ view }: { view: SpendView }) {
  const days = view.days;
  const [active, setActive] = useState(days.length - 1);
  const [shown, setShown] = useState<number | undefined>();
  const items = useRef<(HTMLLIElement | null)[]>([]);
  const captionId = useId();
  const top = niceMax(Math.max(view.trailingMedian, ...days.map((day) => day.costUsd)));
  const median = Math.min(view.trailingMedian / top, 1);
  const focused = shown === undefined ? undefined : days[shown];

  const move = (event: KeyboardEvent<HTMLLIElement>, index: number) => {
    const last = days.length - 1;
    const next =
      event.key === 'ArrowRight'
        ? Math.min(index + 1, last)
        : event.key === 'ArrowLeft'
          ? Math.max(index - 1, 0)
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : undefined;
    if (next === undefined) return;
    event.preventDefault();
    setActive(next);
    items.current[next]?.focus();
  };

  return (
    <figure aria-labelledby={captionId} className="grid gap-2">
      <figcaption id={captionId} className="text-sm text-ink-muted">
        {`Spend per day, ${view.timezone} time. The line is the trailing daily median (${formatUsd(view.trailingMedian)}).`}
      </figcaption>
      <div className="relative" style={{ height: PLOT_HEIGHT + 24 }}>
        {/* The value of the day under the pointer or the keyboard. */}
        <p aria-hidden="true" className="absolute left-0 top-0 h-5 text-sm text-ink">
          {focused ? (
            <>
              <span className="font-semibold">{formatUsd(focused.costUsd)}</span>
              <span className="text-ink-muted">{` on ${formatDay(focused.date)}`}</span>
            </>
          ) : null}
        </p>
        <div className="absolute inset-x-0 bottom-0" style={{ height: PLOT_HEIGHT }}>
          <div aria-hidden="true" className="absolute inset-x-0 bottom-0 h-px bg-line-strong" />
          <div aria-hidden="true" className="absolute inset-x-0 top-0 h-px bg-line" />
          <span aria-hidden="true" className="absolute -top-0.5 right-0 -translate-y-full text-xs tabular-nums text-ink-subtle">
            {formatUsd(top)}
          </span>
          {view.trailingMedian > 0 ? (
            <div aria-hidden="true" className="absolute inset-x-0 h-px bg-ink-muted" style={{ bottom: `${String(median * 100)}%` }} />
          ) : null}
          <ul aria-label="Spend per day" className="absolute inset-0 flex items-end gap-0.5">
            {days.map((day, index) => {
              const height = Math.min(day.costUsd / top, 1) * 100;
              const today = index === days.length - 1;
              return (
                <li
                  key={day.date}
                  ref={(node) => {
                    items.current[index] = node;
                  }}
                  tabIndex={index === active ? 0 : -1}
                  className="group relative flex h-full min-w-0 max-w-6 flex-1 items-end outline-offset-2"
                  onFocus={() => {
                    setActive(index);
                    setShown(index);
                  }}
                  onBlur={() => {
                    setShown(undefined);
                  }}
                  onPointerEnter={() => {
                    setShown(index);
                  }}
                  onPointerLeave={() => {
                    setShown(undefined);
                  }}
                  onKeyDown={(event) => {
                    move(event, index);
                  }}
                >
                  <span className="sr-only">{`${formatDay(day.date)}${today ? ' (today)' : ''}: ${formatUsd(day.costUsd)}`}</span>
                  <span
                    aria-hidden="true"
                    className={cn(
                      'block w-full rounded-t-[0.25rem] transition-opacity group-hover:opacity-80',
                      today ? 'bg-ink' : 'bg-ink-subtle',
                      shown === index && 'opacity-80',
                    )}
                    style={{ height: `${String(height)}%` }}
                  />
                </li>
              );
            })}
          </ul>
        </div>
      </div>
      <div aria-hidden="true" className="flex justify-between text-xs tabular-nums text-ink-subtle">
        <span>{days[0] ? formatDay(days[0].date) : ''}</span>
        <span>Today</span>
      </div>
    </figure>
  );
}

function SpendTable({ view }: { view: SpendView }) {
  return (
    <Table>
      <TableCaption>{`Spend per day, ${view.timezone} time, newest first.`}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Day</TableHead>
          <TableHead numeric>Spend</TableHead>
          <TableHead>Biggest task</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {[...view.days].reverse().map((day) => (
          <TableRow key={day.date}>
            <TableCell>{formatDay(day.date)}</TableCell>
            <TableCell numeric>{formatUsd(day.costUsd)}</TableCell>
            <TableCell>{topTask(day) ?? '–'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Ranked bars in one hue: the parts of the window's spend, largest first. */
function RankedBars({ title, totals, labelOf }: { title: string; totals: readonly RankedTotal[]; labelOf: (key: string) => string }) {
  const headingId = useId();
  const largest = totals[0]?.total ?? 0;
  return (
    <section aria-labelledby={headingId} className="grid content-start gap-3">
      <SubHeading id={headingId}>{title}</SubHeading>
      {totals.length === 0 ? (
        <p className="text-sm text-ink-muted">Nothing spent in this window.</p>
      ) : (
        <ul className="grid gap-2.5">
          {totals.map((item) => (
            <li key={item.key} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-3 text-sm">
              <span className="truncate text-ink">{item.key === 'other' ? 'Everything else' : labelOf(item.key)}</span>
              <span className="flex min-w-0 items-center gap-2">
                <span
                  aria-hidden="true"
                  className="block h-2 rounded-r-[0.25rem] bg-ink"
                  style={{ width: `${String(largest > 0 ? (item.total / largest) * 75 : 0)}%` }}
                />
                <span className="shrink-0 tabular-nums text-ink-muted">{formatUsd(item.total)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The spend view: today against the brake, the window's total, the fallback and held-for-review
 * rates, spend per day, and where it went by task and by model.
 */
export function SpendPanel() {
  const [days, setDays] = useState(30);
  const [asTable, setAsTable] = useState(false);
  const spend = useSpend(days);

  const filters = (
    <Fieldset legend="Window">
      {(group) => (
        <SegmentedControl
          {...group}
          value={String(days)}
          onValueChange={(value) => {
            const next = WINDOWS.find((item) => String(item.days) === value);
            if (next) setDays(next.days);
          }}
        >
          {WINDOWS.map((item) => (
            <SegmentedControlItem key={item.days} value={String(item.days)}>
              {item.label}
            </SegmentedControlItem>
          ))}
        </SegmentedControl>
      )}
    </Fieldset>
  );

  if (spend.isPending) {
    return (
      <div className="grid gap-6">
        {filters}
        <BlockLoading label="Loading the spend" lines={4} />
      </div>
    );
  }
  if (spend.isError) {
    return (
      <div className="grid gap-6">
        {filters}
        <ProblemAlert
          error={spend.error}
          title="Couldn’t load the spend."
          onRetry={() => void spend.refetch()}
          retryLabel="Try loading the spend again"
        />
      </div>
    );
  }
  const view = spend.data;
  const today = view.days.at(-1);
  const vsMedian = today && view.trailingMedian > 0 ? today.costUsd / view.trailingMedian - 1 : undefined;

  return (
    <div className="grid gap-8">
      {filters}
      <div className={cn('grid gap-8 transition-opacity', spend.isPlaceholderData && 'opacity-60')}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label="Today"
            value={today?.costUsd ?? null}
            format={formatUsd}
            delta={vsMedian === undefined ? undefined : { value: vsMedian, label: 'against the median', good: 'down' }}
            caption={`The brake engages at ${formatUsd(view.brakeThreshold)}.`}
          />
          <StatTile label={`Last ${String(days)} days`} value={view.total} format={formatUsd} />
          <StatTile
            label="Fallback rate"
            value={view.fallbackRate}
            format={(value) => formatPercent(value, 1)}
            caption="Calls a fallback model served."
          />
          <StatTile
            label="Held for review"
            value={view.reviewOnlyRate}
            format={(value) => formatPercent(value, 1)}
            caption="Drafts and posts made review-only."
          />
        </div>
        <div className="grid gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <SubHeading>Per day</SubHeading>
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={asTable}
              onClick={() => {
                setAsTable((current) => !current);
              }}
            >
              {asTable ? <ChartColumn aria-hidden="true" /> : <Table2 aria-hidden="true" />}
              {asTable ? 'Show the chart' : 'Show as a table'}
            </Button>
          </div>
          {view.days.length === 0 ? (
            <p className="text-ink-muted">No spend recorded yet.</p>
          ) : asTable ? (
            <SpendTable view={view} />
          ) : (
            <DailySpendChart key={days} view={view} />
          )}
        </div>
        <div className="grid gap-8 md:grid-cols-2">
          <RankedBars title="By task" totals={rankedTotals(view.days, 'byTask')} labelOf={taskLabel} />
          <RankedBars title="By model" totals={rankedTotals(view.days, 'byModel')} labelOf={(key) => key} />
        </div>
      </div>
    </div>
  );
}
