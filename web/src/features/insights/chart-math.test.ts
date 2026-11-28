// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { dimensionRows, halfWidth, niceMax, niceRelativeBound, rankedTotals } from './chart-math';
import { valueLabel } from './labels';
import { countOf, dueOffset, snapshotOrder } from './snapshot-form';
import { overview, postSummary } from './testing/fixtures';

const HOUR = 3_600_000;
const now = Date.parse('2026-11-27T15:00:00Z');

describe('chart scales', () => {
  it('rounds relative differences up to a readable bound', () => {
    expect(niceRelativeBound(0.18)).toBe(0.25);
    expect(niceRelativeBound(-0.4)).toBe(0.5);
    expect(niceRelativeBound(1.3)).toBe(2);
    expect(niceRelativeBound(14)).toBe(14);
  });

  it('measures a bar against its half of the axis, never past it', () => {
    expect(halfWidth(0.25, 0.5)).toBe(0.5);
    expect(halfWidth(-0.5, 0.5)).toBe(1);
    expect(halfWidth(2, 0.5)).toBe(1);
    expect(halfWidth(Number.NaN, 0.5)).toBe(0);
  });

  it('picks round axis maxima', () => {
    expect(niceMax(4.12)).toBe(5);
    expect(niceMax(17)).toBe(20);
    expect(niceMax(0.3)).toBe(0.5);
    expect(niceMax(0)).toBe(1);
  });
});

describe('the rows of a dimension', () => {
  it('keeps the server’s order and marks thin evidence', () => {
    const rows = dimensionRows(overview(), 'pillar', 'engagement');
    expect(rows.map((row) => [row.value, row.vs, row.few])).toEqual([
      ['agents', 0.25, false],
      ['identity', -0.2, false],
      ['governance', 0, true],
    ]);
    expect(dimensionRows(overview(), 'pillar', 'reposts').every((row) => row.rate === null)).toBe(true);
  });

  it('names values in the owner’s words', () => {
    expect(valueLabel('pillar', 'agents', new Map([['agents', 'AI agents & security']]))).toBe('AI agents & security');
    expect(valueLabel('go_live_mode', 'kit')).toBe('Ready-to-post kit');
    expect(valueLabel('hook_type', 'none')).toBe('No hook type');
    expect(valueLabel('lane', 'something_new')).toBe('something new');
  });
});

describe('ranked totals', () => {
  const day = (byTask: Record<string, number>) => ({ date: '2026-11-27', costUsd: 0, byTask, byModel: {} });

  it('sums the days, largest first, folding the tail into “other”', () => {
    const days = [day({ draft: 2, triage: 0.5 }), day({ draft: 1, research: 3, image: 0 })];
    expect(rankedTotals(days, 'byTask')).toEqual([
      { key: 'draft', total: 3 },
      { key: 'research', total: 3 },
      { key: 'triage', total: 0.5 },
    ]);
    expect(rankedTotals(days, 'byTask', 2)).toEqual([
      { key: 'draft', total: 3 },
      { key: 'other', total: 3.5 },
    ]);
  });
});

describe('the snapshot form’s rules', () => {
  it('knows which snapshot a post is due for, like the server', () => {
    const ago = (hours: number) => new Date(now - hours * HOUR).toISOString();
    expect(dueOffset(ago(35), now)).toBeUndefined();
    expect(dueOffset(ago(36), now)).toBe('h48');
    expect(dueOffset(ago(71.9), now)).toBe('h48');
    expect(dueOffset(ago(6 * 24), now)).toBe('d7');
    expect(dueOffset(ago(8 * 24), now)).toBeUndefined();
    expect(dueOffset(null, now)).toBeUndefined();
  });

  it('offers due posts first, then the newest', () => {
    const posts = [
      postSummary({ id: 'old', postedAt: new Date(now - 30 * 24 * HOUR).toISOString() }).item,
      postSummary({ id: 'fresh', postedAt: new Date(now - 2 * HOUR).toISOString() }).item,
      postSummary({ id: 'due', postedAt: new Date(now - 48 * HOUR).toISOString() }).item,
    ];
    expect(snapshotOrder(posts, now).map((post) => post.id)).toEqual(['due', 'fresh', 'old']);
  });

  it('reads counts with thousands separators, never a decimal', () => {
    expect(countOf('')).toBeNull();
    expect(countOf('2400')).toBe(2400);
    expect(countOf('2,400')).toBe(2400);
    expect(countOf('2.400')).toBe(2400);
    expect(countOf('1 234 567')).toBe(1234567);
    expect(countOf('1.5')).toBeUndefined();
    expect(countOf('2,40')).toBeUndefined();
    expect(countOf('-3')).toBeUndefined();
    expect(countOf('2000000000')).toBeUndefined();
  });
});
