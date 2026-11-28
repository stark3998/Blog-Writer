import { useId } from 'react';

import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cn } from '@/lib/cn';

import { formatNumber, formatPercent, formatSignedPercent } from '../settings/format';
import { FEW_POSTS, halfWidth, niceRelativeBound, type DimensionRow } from './chart-math';
import { RATE_KEYS, type RateKey } from './labels';
import type { InsightsRow } from './queries';

/** "25% above your median", "10% below", "at your median". */
export function relativeWords(vs: number): string {
  if (Math.abs(vs) < 0.005) return 'at your median';
  return `${formatPercent(Math.abs(vs))} ${vs > 0 ? 'above' : 'below'} your median`;
}

function rateText(rate: number | null): string {
  return rate === null ? '–' : formatNumber(rate);
}

/**
 * Rates against the owner's own trailing median, one row per value of a dimension: a diverging
 * bar from the median line (right is above it, left below), the signed difference at its tip and
 * the rate per 1,000 impressions beside it. Polarity reads from the side and the sign, not the
 * colour alone; rows with fewer than three posts are marked as thin evidence.
 */
export function RatesChart({
  rows,
  metric,
  labelOf,
  title,
}: {
  rows: readonly DimensionRow[];
  metric: RateKey;
  labelOf: (value: string) => string;
  /** Names the chart for assistive technology ("Engagement by format"). */
  title: string;
}) {
  const known = rows.map((row) => row.vs).filter((vs): vs is number => vs !== null);
  const bound = niceRelativeBound(Math.max(0.1, ...known.map((vs) => Math.abs(vs) * 1.2)));
  const unit = RATE_KEYS.find((item) => item.value === metric)?.unit ?? metric;
  const captionId = useId();
  return (
    <figure aria-labelledby={captionId} className="grid gap-2">
      <figcaption id={captionId} className="sr-only">
        {title}
      </figcaption>
      <div
        aria-hidden="true"
        className="hidden grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_6rem] gap-x-4 text-xs text-ink-subtle sm:grid"
      >
        <span />
        <span className="text-center">Against your median</span>
        <span className="text-right">{`${unit} per 1K`}</span>
      </div>
      <ul className="border-t border-line">
        {rows.map((row) => {
          const width = row.vs === null ? 0 : halfWidth(row.vs, bound) * 50;
          const above = (row.vs ?? 0) >= 0;
          const label = labelOf(row.value);
          return (
            <li
              key={row.value}
              className={cn(
                'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 border-b border-line py-3',
                'sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_6rem]',
              )}
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-ink">{label}</p>
                <p className="text-xs text-ink-subtle">
                  {`${String(row.posts)} ${row.posts === 1 ? 'post' : 'posts'}`}
                  {row.few ? `, too few to read much into` : null}
                </p>
              </div>
              <p className="text-right tabular-nums text-ink sm:order-3">
                {rateText(row.rate)}
                <span className="sr-only">{` ${unit} per 1,000 impressions`}</span>
              </p>
              <div className="col-span-2 sm:order-2 sm:col-span-1">
                <div aria-hidden="true" className={cn('relative h-6', row.few && 'opacity-50')}>
                  <div className="absolute inset-y-0 left-1/2 w-px bg-ink-muted" />
                  {row.vs === null ? null : (
                    <>
                      <div
                        className={cn(
                          'absolute top-1.5 h-3',
                          above ? 'left-1/2 rounded-r-[0.25rem] bg-success' : 'right-1/2 rounded-l-[0.25rem] bg-danger',
                        )}
                        style={{ width: `${String(width)}%` }}
                      />
                      <span
                        className="absolute top-0.5 whitespace-nowrap text-xs font-medium tabular-nums text-ink"
                        style={
                          above
                            ? { left: `calc(50% + ${String(width)}% + 0.375rem)` }
                            : { right: `calc(50% + ${String(width)}% + 0.375rem)` }
                        }
                      >
                        {formatSignedPercent(row.vs)}
                      </span>
                    </>
                  )}
                </div>
                <p className="sr-only">
                  {row.vs === null ? 'No comparison: the median for this metric is unknown.' : `${relativeWords(row.vs)}.`}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
      <div
        aria-hidden="true"
        className="grid grid-cols-[minmax(0,1fr)] text-xs tabular-nums text-ink-subtle sm:grid-cols-[minmax(8rem,14rem)_minmax(0,1fr)_6rem] sm:gap-x-4"
      >
        <span className="hidden sm:block" />
        <span className="flex justify-between">
          <span>{formatSignedPercent(-bound)}</span>
          <span>Your median</span>
          <span>{formatSignedPercent(bound)}</span>
        </span>
      </div>
      <p className="text-xs text-ink-subtle">
        {`Right of the line: above your trailing median; left: below. Rows with fewer than ${String(FEW_POSTS)} posts are faded.`}
      </p>
    </figure>
  );
}

const TABLE_KEYS: readonly RateKey[] = ['reactions', 'comments', 'reposts', 'saves', 'sends', 'engagement'];

/** The chart's table twin: every rate per 1K of each row, and its difference to the median. */
export function RatesTable({
  rows,
  metric,
  labelOf,
  title,
}: {
  rows: readonly InsightsRow[];
  metric: RateKey;
  labelOf: (value: string) => string;
  title: string;
}) {
  const label = (key: RateKey) => RATE_KEYS.find((item) => item.value === key)?.label ?? key;
  return (
    <Table>
      <TableCaption>{`${title}. Rates per 1,000 impressions; the last column compares ${label(metric).toLocaleLowerCase()} with your median.`}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Value</TableHead>
          <TableHead numeric>Posts</TableHead>
          {TABLE_KEYS.map((key) => (
            <TableHead key={key} numeric>
              {label(key)}
            </TableHead>
          ))}
          <TableHead numeric>Against median</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const vs = row.vsMedian[metric] ?? null;
          return (
            <TableRow key={row.value}>
              <TableCell className="font-medium">{labelOf(row.value)}</TableCell>
              <TableCell numeric>{row.posts}</TableCell>
              {TABLE_KEYS.map((key) => (
                <TableCell key={key} numeric>
                  {rateText(row.ratesPer1k[key] ?? null)}
                </TableCell>
              ))}
              <TableCell numeric>{vs === null ? '–' : formatSignedPercent(vs)}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
