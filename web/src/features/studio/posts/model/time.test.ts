// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { dayPhrase, formatSlot, formatTime, fromLocalInput, relativeTime, toLocalInput } from './time';

const NOW = Date.parse('2026-12-01T13:00:00.000Z');

describe('slot times', () => {
  it('reads a slot in its own zone, with the zone named', () => {
    const label = formatSlot({ utc: '2026-12-01T14:00:00.000000Z', timezone: 'America/New_York' });
    expect(label).toMatch(/09:00/);
    expect(label).toMatch(/EST|GMT-5/);
    expect(formatTime('2026-12-01T14:00:00.000000Z', 'America/New_York')).toMatch(/09:00/);
  });

  it('survives a zone name it does not know', () => {
    expect(formatSlot({ utc: '2026-12-01T14:00:00.000000Z', timezone: 'Mars/Olympus' })).not.toBe('');
  });

  it('says how far away a time is, the way a person would', () => {
    expect(relativeTime('2026-12-01T13:25:00.000Z', NOW)).toBe('in 25 minutes');
    expect(relativeTime('2026-12-01T12:55:00.000Z', NOW)).toBe('5 minutes ago');
    expect(relativeTime('2026-12-01T16:00:00.000Z', NOW)).toBe('in 3 hours');
    expect(relativeTime('2026-12-01T13:00:20.000Z', NOW)).toBe('now');
    expect(relativeTime('2026-12-05T13:00:00.000Z', NOW)).toBe('in 4 days');
    expect(relativeTime('not a date', NOW)).toBe('');
  });

  it('names the day relative to today in the slot’s zone', () => {
    expect(dayPhrase('2026-12-01T14:00:00.000Z', NOW, 'America/New_York')).toBe('today');
    expect(dayPhrase('2026-12-02T14:00:00.000Z', NOW, 'America/New_York')).toBe('tomorrow');
    // 01:00 UTC on the 2nd is still the 1st in New York.
    expect(dayPhrase('2026-12-02T01:00:00.000Z', NOW, 'America/New_York')).toBe('today');
    expect(dayPhrase('2026-12-08T14:00:00.000Z', NOW, 'America/New_York')).toMatch(/^on /);
  });
});

describe('datetime-local values', () => {
  it('round-trip through the device’s zone', () => {
    const iso = '2026-12-01T14:00:00.000Z';
    const local = toLocalInput(iso);
    expect(local).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(fromLocalInput(local)).toBe(iso);
  });

  it('refuses what is not a date and time', () => {
    expect(fromLocalInput('')).toBeUndefined();
    expect(fromLocalInput('tomorrow at nine')).toBeUndefined();
    expect(toLocalInput(null)).toBe('');
  });
});
