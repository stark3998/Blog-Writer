import type { components } from '@/lib/api/schema';

import type { InsightDimension, RateKey } from './labels';

/** The numbers behind Insights' charts: scales, ranked totals and the rows of a dimension. */

type Schemas = components['schemas'];
type InsightsOverview = Schemas['InsightsOverview'];
type SpendDay = Schemas['SpendDay'];

/** Below this many measured posts a row is shown, but marked as too few to read much into. */
export const FEW_POSTS = 3;

const RELATIVE_STEPS = [0.1, 0.25, 0.5, 1, 2, 3, 5, 10];

/** The smallest round bound at or above `value` for relative differences (0.25 = ±25 %). */
export function niceRelativeBound(value: number): number {
  const size = Math.abs(value);
  return RELATIVE_STEPS.find((step) => step >= size) ?? Math.ceil(size);
}

/** How far a bar reaches into its half of a diverging axis (0 to 1). */
export function halfWidth(value: number, bound: number): number {
  if (!Number.isFinite(value) || bound <= 0) return 0;
  return Math.min(Math.abs(value) / bound, 1);
}

/** The smallest of 1, 2, 2.5 or 5 times a power of ten at or above `value` (an axis maximum). */
export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find((multiple) => multiple * power >= value - 1e-9) ?? 10;
  return step * power;
}

export interface DimensionRow {
  readonly value: string;
  readonly posts: number;
  /** The row's median per 1K impressions for the metric (`null`: no post has that metric). */
  readonly rate: number | null;
  /** Relative to the owner's median (0.25 = 25 % above); `null` when either side is unknown. */
  readonly vs: number | null;
  readonly few: boolean;
}

/** One dimension's rows for one metric, most posts first (the server's order). */
export function dimensionRows(overview: InsightsOverview, dimension: InsightDimension, metric: RateKey): DimensionRow[] {
  return overview.rows
    .filter((row) => row.dimension === dimension)
    .map((row) => ({
      value: row.value,
      posts: row.posts,
      rate: row.ratesPer1k[metric] ?? null,
      vs: row.vsMedian[metric] ?? null,
      few: row.posts < FEW_POSTS,
    }));
}

export interface RankedTotal {
  readonly key: string;
  readonly total: number;
}

/**
 * Totals of `byTask` or `byModel` over the days, largest first. Past `top` entries the rest fold
 * into one "other" entry, so a long tail never needs more marks.
 */
export function rankedTotals(days: readonly SpendDay[], pick: 'byTask' | 'byModel', top = 7): RankedTotal[] {
  const totals = new Map<string, number>();
  for (const day of days) {
    for (const [key, value] of Object.entries(day[pick])) {
      totals.set(key, (totals.get(key) ?? 0) + value);
    }
  }
  const ranked = [...totals.entries()]
    .filter(([, total]) => total > 0)
    .map(([key, total]) => ({ key, total }))
    .sort((a, b) => b.total - a.total || a.key.localeCompare(b.key));
  if (ranked.length <= top) return ranked;
  const kept = ranked.slice(0, top - 1);
  const rest = ranked.slice(top - 1).reduce((sum, item) => sum + item.total, 0);
  return [...kept, { key: 'other', total: rest }];
}
