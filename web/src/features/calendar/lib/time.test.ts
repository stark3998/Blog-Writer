// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  addDays,
  clockChangeInWeek,
  currentWeekStart,
  dayLabel,
  daysBetween,
  gapLabel,
  isLocalTime,
  isMonday,
  localToUtcMs,
  longDayLabel,
  momentLabel,
  mondayOf,
  parseLocalDate,
  relativeFuture,
  slotLabel,
  toTimestamp,
  utcToLocal,
  weekDates,
  weekdayId,
  weekOfLabel,
  weekRangeLabel,
  zoneAbbreviation,
  zoneCity,
  zoneOffsetMinutes,
} from './time';

const NY = 'America/New_York';
const iso = (ms: number) => new Date(ms).toISOString();

describe('calendar dates', () => {
  it('validates dates and times the way the server does', () => {
    expect(parseLocalDate('2026-11-30')).toEqual({ year: 2026, month: 11, day: 30 });
    expect(parseLocalDate('2026-02-30')).toBeUndefined();
    expect(parseLocalDate('30/11/2026')).toBeUndefined();
    expect(isLocalTime('09:00')).toBe(true);
    expect(isLocalTime('24:00')).toBe(false);
    expect(isLocalTime('9:00')).toBe(false);
  });

  it('does date arithmetic without a zone', () => {
    expect(addDays('2026-11-30', 6)).toBe('2026-12-06');
    expect(addDays('2026-12-28', 7)).toBe('2027-01-04');
    expect(mondayOf('2026-12-06')).toBe('2026-11-30');
    expect(mondayOf('2026-11-30')).toBe('2026-11-30');
    expect(weekdayId('2026-12-01')).toBe('tue');
    expect(isMonday('2026-11-30')).toBe(true);
    expect(isMonday('2026-12-01')).toBe(false);
    expect(isMonday('2026-02-30')).toBe(false);
    expect(weekDates('2026-11-30')).toEqual([
      '2026-11-30',
      '2026-12-01',
      '2026-12-02',
      '2026-12-03',
      '2026-12-04',
      '2026-12-05',
      '2026-12-06',
    ]);
    expect(daysBetween('2026-11-30', '2026-12-14')).toBe(14);
  });
});

describe('zone math (the server DST rule)', () => {
  it('converts ordinary wall times in winter and summer', () => {
    expect(iso(localToUtcMs('2026-12-01', '10:00', NY))).toBe('2026-12-01T15:00:00.000Z');
    expect(iso(localToUtcMs('2026-07-01', '10:00', NY))).toBe('2026-07-01T14:00:00.000Z');
    expect(zoneOffsetMinutes(Date.parse('2026-12-01T15:00:00Z'), NY)).toBe(-300);
    expect(zoneOffsetMinutes(Date.parse('2026-07-01T14:00:00Z'), NY)).toBe(-240);
  });

  it('maps a time skipped in spring to the same offset after the gap (02:30 becomes 03:30 EDT)', () => {
    const instant = localToUtcMs('2026-03-08', '02:30', NY);
    expect(iso(instant)).toBe('2026-03-08T07:30:00.000Z');
    expect(utcToLocal(instant, NY)).toEqual({ date: '2026-03-08', time: '03:30' });
    expect(zoneAbbreviation(instant, NY)).toBe('EDT');
    expect(iso(localToUtcMs('2026-03-08', '01:30', NY))).toBe('2026-03-08T06:30:00.000Z');
    expect(iso(localToUtcMs('2026-03-08', '03:30', NY))).toBe('2026-03-08T07:30:00.000Z');
  });

  it('maps a time that happens twice in autumn to its first occurrence (01:30 EDT)', () => {
    expect(iso(localToUtcMs('2026-11-01', '01:30', NY))).toBe('2026-11-01T05:30:00.000Z');
    expect(iso(localToUtcMs('2026-11-01', '00:30', NY))).toBe('2026-11-01T04:30:00.000Z');
    expect(iso(localToUtcMs('2026-11-01', '03:00', NY))).toBe('2026-11-01T08:00:00.000Z');
    // The second 01:30 is still read back as local 01:30, now in EST.
    expect(utcToLocal(Date.parse('2026-11-01T06:30:00Z'), NY)).toEqual({ date: '2026-11-01', time: '01:30' });
    expect(zoneAbbreviation(Date.parse('2026-11-01T06:30:00Z'), NY)).toBe('EST');
  });

  it('finds this week’s Monday in the owner’s zone, not the device’s', () => {
    // Sunday 29 November, 22:00 in New York is already Monday in UTC.
    expect(currentWeekStart(Date.parse('2026-11-30T03:00:00Z'), NY)).toBe('2026-11-23');
    expect(currentWeekStart(Date.parse('2026-11-30T05:00:00Z'), NY)).toBe('2026-11-30');
  });

  it('names the day the clocks change, and which way', () => {
    expect(clockChangeInWeek('2026-10-26', NY)).toEqual({
      date: '2026-11-01',
      shiftMinutes: -60,
      from: 'EDT',
      to: 'EST',
    });
    expect(clockChangeInWeek('2026-03-02', NY)).toEqual({
      date: '2026-03-08',
      shiftMinutes: 60,
      from: 'EST',
      to: 'EDT',
    });
    expect(clockChangeInWeek('2026-11-30', NY)).toBeUndefined();
  });

  it('writes timestamps in the server’s six-digit format', () => {
    expect(toTimestamp(Date.UTC(2026, 10, 26, 15, 4, 5, 123))).toBe('2026-11-26T15:04:05.123000Z');
    expect(toTimestamp(Date.UTC(2026, 10, 26))).toBe('2026-11-26T00:00:00.000000Z');
  });
});

describe('labels', () => {
  it('names days, slots and weeks the same way on every device', () => {
    expect(dayLabel('2026-12-01')).toBe('Tue 1 Dec');
    expect(longDayLabel('2026-12-01')).toBe('Tuesday 1 December');
    expect(slotLabel('2026-12-01', '10:00')).toBe('Tue 1 Dec, 10:00');
    expect(momentLabel(Date.parse('2026-12-01T15:00:00Z'), NY)).toBe('Tue 1 Dec, 10:00');
    expect(weekRangeLabel('2026-11-30')).toBe('30 Nov – 6 Dec');
    expect(weekRangeLabel('2026-12-28')).toBe('28 Dec 2026 – 3 Jan 2027');
    expect(weekOfLabel('2026-11-30')).toBe('the week of 30 November');
    expect(zoneCity(NY)).toBe('New York');
    expect(zoneCity('America/Los_Angeles')).toBe('Los Angeles');
  });

  it('says how far away something is', () => {
    const now = Date.parse('2026-11-27T12:00:00Z');
    expect(relativeFuture(now + 20 * 60_000, now)).toBe('in 20 minutes');
    expect(relativeFuture(now + 41 * 3_600_000, now)).toBe('in 41 hours');
    expect(relativeFuture(now + 3 * 86_400_000, now)).toBe('in 3 days');
    expect(relativeFuture(now - 1, now)).toBe('now');
    expect(gapLabel(23 * 3_600_000)).toBe('23 hours');
    expect(gapLabel(30 * 60_000)).toBe('30 minutes');
  });
});
