// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { backoffDelay } from './backoff';

const exact = { baseMs: 1000, maxMs: 30_000, random: () => 0.5 };

describe('backoffDelay', () => {
  it('doubles from the base delay', () => {
    expect([0, 1, 2, 3, 4].map((attempt) => backoffDelay(attempt, exact))).toEqual([1000, 2000, 4000, 8000, 16_000]);
  });

  it('never exceeds the cap, however many attempts', () => {
    expect(backoffDelay(5, exact)).toBe(30_000);
    expect(backoffDelay(60, exact)).toBe(30_000);
    expect(backoffDelay(5000, { ...exact, random: () => 0.999 })).toBe(30_000);
  });

  it('spreads each delay by the jitter fraction, both ways', () => {
    expect(backoffDelay(1, { ...exact, random: () => 0 })).toBe(1600);
    expect(backoffDelay(1, { ...exact, random: () => 0.999_999 })).toBe(2400);
    expect(backoffDelay(1, { ...exact, jitter: 0, random: () => 0 })).toBe(2000);
  });

  it('treats a negative attempt as the first', () => {
    expect(backoffDelay(-3, exact)).toBe(1000);
  });

  it('stays within bounds with the real random source', () => {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      const delay = backoffDelay(attempt, { baseMs: 1000, maxMs: 30_000 });
      const expected = Math.min(30_000, 1000 * 2 ** attempt);
      expect(delay).toBeGreaterThanOrEqual(expected * 0.8 - 1);
      expect(delay).toBeLessThanOrEqual(Math.min(30_000, expected * 1.2 + 1));
    }
  });
});
