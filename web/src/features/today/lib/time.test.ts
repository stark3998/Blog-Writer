// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { agoWords, dueLabel, isSameDay, keptWords, momentWords, shortDate, sinceWords, spanWords } from './time';

const NOW = Date.parse('2026-11-27T15:00:00Z');
const MIN = 60_000;
const HOUR = 60 * MIN;
const UTC = { locale: 'en-US', timeZone: 'UTC' } as const;

describe('spans', () => {
  it('rounds time left up and time passed down, in minutes, hours, then days', () => {
    expect(spanWords(25 * MIN)).toBe('25 min');
    expect(spanWords(20_000)).toBe('1 min');
    expect(spanWords(90 * MIN)).toBe('2 h');
    expect(spanWords(31 * HOUR)).toBe('31 h');
    expect(spanWords(50 * HOUR)).toBe('3 days');
    expect(agoWords(90 * MIN)).toBe('1 h');
    expect(agoWords(49 * HOUR)).toBe('2 days');
  });
});

describe('dueLabel', () => {
  it('counts down within two hours and says when it is overdue', () => {
    expect(dueLabel(new Date(NOW + 25 * MIN).toISOString(), NOW, UTC)).toEqual({ text: 'Due in 25 min', tone: 'soon' });
    expect(dueLabel(new Date(NOW - 90 * MIN).toISOString(), NOW, UTC)).toEqual({ text: 'Overdue by 1 h', tone: 'overdue' });
  });

  it('names the day and time beyond that', () => {
    expect(dueLabel('2026-11-27T19:00:00Z', NOW, UTC)).toEqual({ text: 'Due today at 7:00 PM', tone: 'later' });
    expect(dueLabel('2026-11-28T09:00:00Z', NOW, UTC)?.text).toBe('Due tomorrow at 9:00 AM');
    expect(dueLabel('2026-12-01T10:00:00Z', NOW, UTC)?.text).toBe('Due Tue at 10:00 AM');
    expect(dueLabel('2026-12-09T10:00:00Z', NOW, UTC)?.text).toBe('Due Dec 9 at 10:00 AM');
  });

  it('reads the server’s microsecond timestamps and ignores what is not a date', () => {
    expect(dueLabel('2026-11-27T15:30:00.123456Z', NOW, UTC)?.text).toBe('Due in 31 min');
    expect(dueLabel(null, NOW)).toBeUndefined();
    expect(dueLabel('soon', NOW)).toBeUndefined();
  });
});

describe('keptWords', () => {
  it('says how long other members’ text is still kept', () => {
    expect(keptWords(new Date(NOW + 31 * HOUR).toISOString(), NOW)).toBe('Deleted in 31 h');
    expect(keptWords(new Date(NOW + 12 * MIN).toISOString(), NOW)).toBe('Deleted in 12 min');
    expect(keptWords(new Date(NOW - MIN).toISOString(), NOW)).toBe('Being deleted now');
    expect(keptWords(undefined, NOW)).toBe('Deleted within 48 h');
  });
});

describe('days', () => {
  it('compares calendar days in a zone', () => {
    expect(isSameDay(Date.parse('2026-11-27T01:00:00Z'), NOW, 'UTC')).toBe(true);
    expect(isSameDay(Date.parse('2026-11-27T01:00:00Z'), NOW, 'America/New_York')).toBe(false);
  });

  it('says when something last happened', () => {
    expect(sinceWords('2026-11-27T08:00:00Z', NOW, 'UTC')).toBe('today');
    expect(sinceWords('2026-11-26T08:00:00Z', NOW, 'UTC')).toBe('yesterday');
    expect(sinceWords('2026-11-23T08:00:00Z', NOW, 'UTC')).toBe('4 days ago');
    expect(sinceWords(null, NOW)).toBeUndefined();
  });

  it('writes moments and short dates', () => {
    expect(momentWords(Date.parse('2026-11-26T10:00:00Z'), NOW, UTC)).toBe('yesterday at 10:00 AM');
    expect(shortDate('2026-11-23', UTC)).toBe('Nov 23');
  });
});
