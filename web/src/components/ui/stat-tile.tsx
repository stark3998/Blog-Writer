import { ArrowDownRight, ArrowRight, ArrowUpRight } from 'lucide-react';
import type { ComponentProps, ReactNode } from 'react';

import { cn } from '@/lib/cn';

/** Full numbers below this, compact ones (12.9K, 4.2M) from here up. */
const COMPACT_FROM = 10_000;

const standard = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 });

/** A stat's value as people read it: 1,284 / 12.9K / 4.2M. */
export function formatStatValue(value: number): string {
  return Math.abs(value) < COMPACT_FROM ? standard.format(value) : compact.format(value);
}

const signedPercent = new Intl.NumberFormat(undefined, {
  style: 'percent',
  maximumFractionDigits: 1,
  signDisplay: 'exceptZero',
});
const signedNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, signDisplay: 'exceptZero' });

export interface StatDelta {
  /** The change; for `format: 'percent'` a fraction (0.12 is +12%). */
  value: number;
  /** What it is compared with, in words: "vs your median", "vs last week". */
  label: string;
  format?: 'percent' | 'number' | ((value: number) => string);
  /**
   * Which way is good. `up` (default) makes a rise green and a fall red; `down` the reverse
   * (spend, fallback rate); `neutral` keeps both in ink.
   */
  good?: 'up' | 'down' | 'neutral';
}

function formatDelta({ value, format = 'percent' }: StatDelta): string {
  if (typeof format === 'function') return format(value);
  return format === 'percent' ? signedPercent.format(value) : signedNumber.format(value);
}

function deltaTone({ value, good = 'up' }: StatDelta): string {
  if (value === 0 || good === 'neutral') return 'text-ink-muted';
  return (value > 0) === (good === 'up') ? 'text-success' : 'text-danger';
}

export interface StatTileProps extends Omit<ComponentProps<'dl'>, 'children'> {
  /** Sentence case, no colon: "Impressions per post". */
  label: ReactNode;
  /** The number, or `null` when there is none yet. */
  value: number | null;
  /** Formats the value (default: 1,284 / 12.9K). */
  format?: (value: number) => string;
  /** A unit after the number, smaller: "per 1K", "min". */
  unit?: ReactNode;
  delta?: StatDelta;
  /** One line of context under the number. */
  caption?: ReactNode;
}

/**
 * One headline number with its label, an optional change and a caption (the dataviz figure
 * contract). The value is the sans at display size with proportional figures; the change has a
 * sign and an arrow as well as its colour. Lay several out in a grid for a KPI row.
 */
export function StatTile({ label, value, format = formatStatValue, unit, delta, caption, className, ...props }: StatTileProps) {
  return (
    <dl data-slot="stat-tile" className={cn('flex flex-col gap-1 rounded-lg border border-line bg-sheet p-4', className)} {...props}>
      <dt className="text-sm text-ink-muted">{label}</dt>
      <dd className="flex flex-wrap items-baseline gap-x-1.5 text-3xl font-semibold text-ink">
        {value === null ? (
          <>
            <span aria-hidden="true">–</span>
            <span className="sr-only">No data yet</span>
          </>
        ) : (
          format(value)
        )}
        {unit !== undefined && value !== null ? <span className="text-sm font-normal text-ink-muted">{unit}</span> : null}
      </dd>
      {delta && value !== null ? (
        <dd className={cn('flex flex-wrap items-center gap-x-1 text-sm font-medium', deltaTone(delta))}>
          {delta.value > 0 ? (
            <ArrowUpRight aria-hidden="true" className="size-4 shrink-0" />
          ) : delta.value < 0 ? (
            <ArrowDownRight aria-hidden="true" className="size-4 shrink-0" />
          ) : (
            <ArrowRight aria-hidden="true" className="size-4 shrink-0" />
          )}
          <span>{formatDelta(delta)}</span>
          <span className="font-normal text-ink-muted">{delta.label}</span>
        </dd>
      ) : null}
      {caption !== undefined ? <dd className="text-xs text-ink-subtle">{caption}</dd> : null}
    </dl>
  );
}
