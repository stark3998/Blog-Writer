/**
 * Wall-clock math in the plan's IANA zone (America/New_York by default), with the server's DST
 * rule (`blogwriter.domain.weeks`):
 *
 * - a wall time that does not exist (spring forward: 02:30 on 2026-03-08 in New York) maps to the
 *   offset before the gap, so it lands at 03:30 EDT;
 * - a wall time that happens twice (fall back: 01:30 on 2026-11-01) maps to its first occurrence
 *   (01:30 EDT).
 *
 * Dates are `YYYY-MM-DD` strings and times `HH:MM` strings, as the server sends them. Weeks start
 * on Monday. Labels are built by hand ("Tue 1 Dec, 10:00"), so they match the server's own
 * ("Tue 10:00") on every device, and carry the zone's abbreviation where it matters (EST, EDT).
 */

const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

export const DEFAULT_TIMEZONE = 'America/New_York';

const WEEKDAYS_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;
const WEEKDAYS_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] as const;
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;
const MONTHS_LONG = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const;

/** The server's weekday names (`domain.base.WEEKDAYS`), Monday first. */
export const WEEKDAY_IDS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
export type WeekdayId = (typeof WEEKDAY_IDS)[number];

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;

function pad(value: number, width = 2): string {
  return String(value).padStart(width, '0');
}

interface DateParts {
  readonly year: number;
  readonly month: number;
  readonly day: number;
}

/** `YYYY-MM-DD` as numbers; `undefined` for anything else (including 2026-02-30). */
export function parseLocalDate(date: string): DateParts | undefined {
  const match = DATE_RE.exec(date);
  if (!match) return undefined;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const check = new Date(Date.UTC(year, month - 1, day));
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) {
    return undefined;
  }
  return { year, month, day };
}

/** True for a valid `HH:MM` (00:00 to 23:59). */
export function isLocalTime(time: string): boolean {
  return TIME_RE.test(time);
}

function dateParts(date: string): DateParts {
  const parts = parseLocalDate(date);
  if (!parts) throw new RangeError(`not a date: ${date}`);
  return parts;
}

function timeParts(time: string): { hour: number; minute: number } {
  const match = TIME_RE.exec(time);
  if (!match) throw new RangeError(`not a time of day: ${time}`);
  return { hour: Number(match[1]), minute: Number(match[2]) };
}

/** Midnight UTC of a calendar date: date arithmetic without any zone. */
function dateMs(date: string): number {
  const { year, month, day } = dateParts(date);
  return Date.UTC(year, month - 1, day);
}

function dateOf(ms: number): string {
  const value = new Date(ms);
  return `${String(value.getUTCFullYear())}-${pad(value.getUTCMonth() + 1)}-${pad(value.getUTCDate())}`;
}

/** `date` plus `days` calendar days. */
export function addDays(date: string, days: number): string {
  return dateOf(dateMs(date) + days * DAY_MS);
}

/** 0 for Monday … 6 for Sunday. */
export function weekdayIndex(date: string): number {
  return (new Date(dateMs(date)).getUTCDay() + 6) % 7;
}

export function weekdayId(date: string): WeekdayId {
  return WEEKDAY_IDS[weekdayIndex(date)] ?? 'mon';
}

/** The Monday of the week containing `date`. */
export function mondayOf(date: string): string {
  return addDays(date, -weekdayIndex(date));
}

/** True when `date` is a valid Monday (a week's id). */
export function isMonday(date: string): boolean {
  return parseLocalDate(date) !== undefined && weekdayIndex(date) === 0;
}

/** The seven dates of the week starting on Monday `weekStart`. */
export function weekDates(weekStart: string): string[] {
  return Array.from({ length: 7 }, (_, index) => addDays(weekStart, index));
}

/** Whole days from `from` to `to` (negative when `to` is earlier). */
export function daysBetween(from: string, to: string): number {
  return Math.round((dateMs(to) - dateMs(from)) / DAY_MS);
}

// ------------------------------------------------------------------------------------------------
// Zones
// ------------------------------------------------------------------------------------------------

const partFormatters = new Map<string, Intl.DateTimeFormat>();

function partFormatter(zone: string): Intl.DateTimeFormat {
  let formatter = partFormatters.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    partFormatters.set(zone, formatter);
  }
  return formatter;
}

interface WallClock extends DateParts {
  readonly hour: number;
  readonly minute: number;
  readonly second: number;
}

function wallClock(ms: number, zone: string): WallClock {
  const parts = partFormatter(zone).formatToParts(new Date(ms));
  const read = (type: Intl.DateTimeFormatPartTypes): number => Number(parts.find((part) => part.type === type)?.value);
  const hour = read('hour');
  return {
    year: read('year'),
    month: read('month'),
    day: read('day'),
    // Some engines write midnight as 24 even with h23.
    hour: hour === 24 ? 0 : hour,
    minute: read('minute'),
    second: read('second'),
  };
}

/** True when `zone` is an IANA zone this browser knows. */
export function isKnownZone(zone: string): boolean {
  try {
    partFormatter(zone);
    return true;
  } catch {
    return false;
  }
}

/** The zone's offset from UTC at the instant `ms`, in minutes (New York in winter: -300). */
export function zoneOffsetMinutes(ms: number, zone: string): number {
  const wall = wallClock(ms, zone);
  const asUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute, wall.second);
  return Math.round((asUtc - Math.floor(ms / 1000) * 1000) / MINUTE_MS);
}

/**
 * The UTC instant (ms) of a wall time in `zone`, with the server's DST rule (module docs).
 * `RangeError` for an invalid date or time.
 */
export function localToUtcMs(date: string, time: string, zone: string): number {
  const { year, month, day } = dateParts(date);
  const { hour, minute } = timeParts(time);
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  // Zone transitions are months apart, so a 72-hour window holds at most one.
  const before = zoneOffsetMinutes(wall - 36 * HOUR_MS, zone);
  const after = zoneOffsetMinutes(wall + 36 * HOUR_MS, zone);
  // The offset before a change first: for a time that happens twice, that is its first occurrence.
  for (const offset of before === after ? [before] : [before, after]) {
    const instant = wall - offset * MINUTE_MS;
    if (zoneOffsetMinutes(instant, zone) === offset) return instant;
  }
  // A time skipped by the change: the offset before it, like Python's fold=0.
  return wall - before * MINUTE_MS;
}

/** The local date and time of the instant `ms` in `zone`. */
export function utcToLocal(ms: number, zone: string): { date: string; time: string } {
  const wall = wallClock(ms, zone);
  return {
    date: `${String(wall.year)}-${pad(wall.month)}-${pad(wall.day)}`,
    time: `${pad(wall.hour)}:${pad(wall.minute)}`,
  };
}

/** The local date of `ms` in `zone`. */
export function localDateOf(ms: number, zone: string): string {
  return utcToLocal(ms, zone).date;
}

/** The Monday of the week that contains the instant `ms` in `zone` (the planner's "this week"). */
export function currentWeekStart(ms: number, zone: string): string {
  return mondayOf(localDateOf(ms, zone));
}

/** Parses a server timestamp (`2026-11-26T15:04:05.123456Z`) to ms; `undefined` when it is not one. */
export function parseTimestamp(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? undefined : ms;
}

/**
 * The server's timestamp format: UTC with exactly six fractional digits
 * (`domain.base.to_iso`). Query filters and request bodies are refused in any other shape.
 */
export function toTimestamp(ms: number): string {
  return new Date(ms).toISOString().replace(/\.(\d{3})Z$/, '.$1000Z');
}

const abbreviationFormatters = new Map<string, Intl.DateTimeFormat>();

/** The zone's short name at an instant: "EST", "EDT" (or "GMT+1" where the browser has no name). */
export function zoneAbbreviation(ms: number, zone: string): string {
  let formatter = abbreviationFormatters.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'short' });
    abbreviationFormatters.set(zone, formatter);
  }
  return formatter.formatToParts(new Date(ms)).find((part) => part.type === 'timeZoneName')?.value ?? zone;
}

/** "New York" for `America/New_York`: how the owner names the zone. */
export function zoneCity(zone: string): string {
  const city = zone.split('/').at(-1) ?? zone;
  return city.replace(/_/g, ' ');
}

/** The browser's own zone, when it reports one. */
export function deviceZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}

export interface ClockChange {
  /** The local date the clocks change on. */
  readonly date: string;
  /** Minutes the clocks move: 60 forward in spring, -60 back in autumn. */
  readonly shiftMinutes: number;
  readonly from: string;
  readonly to: string;
}

/** The day in the week starting `weekStart` on which the zone's clocks change, if any. */
export function clockChangeInWeek(weekStart: string, zone: string): ClockChange | undefined {
  const midnights = Array.from({ length: 8 }, (_, index) => localToUtcMs(addDays(weekStart, index), '00:00', zone));
  for (let index = 0; index < 7; index += 1) {
    const start = midnights[index];
    const end = midnights[index + 1];
    if (start === undefined || end === undefined) continue;
    const before = zoneOffsetMinutes(start, zone);
    const after = zoneOffsetMinutes(end, zone);
    if (before !== after) {
      return {
        date: addDays(weekStart, index),
        shiftMinutes: after - before,
        from: zoneAbbreviation(start, zone),
        to: zoneAbbreviation(end, zone),
      };
    }
  }
  return undefined;
}

// ------------------------------------------------------------------------------------------------
// Labels
// ------------------------------------------------------------------------------------------------

/** "Tue 1 Dec". */
export function dayLabel(date: string): string {
  const { month, day } = dateParts(date);
  return `${WEEKDAYS_SHORT[weekdayIndex(date)] ?? ''} ${String(day)} ${MONTHS_SHORT[month - 1] ?? ''}`;
}

/** "1 Dec". */
export function shortDate(date: string): string {
  const { month, day } = dateParts(date);
  return `${String(day)} ${MONTHS_SHORT[month - 1] ?? ''}`;
}

/** "Tuesday 1 December". */
export function longDayLabel(date: string): string {
  const { month, day } = dateParts(date);
  return `${WEEKDAYS_LONG[weekdayIndex(date)] ?? ''} ${String(day)} ${MONTHS_LONG[month - 1] ?? ''}`;
}

/** "Tue". */
export function weekdayShort(date: string): string {
  return WEEKDAYS_SHORT[weekdayIndex(date)] ?? '';
}

/** "Tue 1 Dec, 10:00". */
export function slotLabel(date: string, time: string): string {
  return `${dayLabel(date)}, ${time}`;
}

/** "Tue 1 Dec, 10:00" for an instant, in `zone`. */
export function momentLabel(ms: number, zone: string): string {
  const { date, time } = utcToLocal(ms, zone);
  return slotLabel(date, time);
}

/** "30 Nov – 6 Dec", with the year when the week spans two ("28 Dec 2026 – 3 Jan 2027"). */
export function weekRangeLabel(weekStart: string): string {
  const end = addDays(weekStart, 6);
  const first = dateParts(weekStart);
  const last = dateParts(end);
  const short = (parts: DateParts) => `${String(parts.day)} ${MONTHS_SHORT[parts.month - 1] ?? ''}`;
  if (first.year !== last.year) {
    return `${short(first)} ${String(first.year)} – ${short(last)} ${String(last.year)}`;
  }
  return `${short(first)} – ${short(last)}`;
}

/** "the week of 30 November": a week in a sentence. */
export function weekOfLabel(weekStart: string): string {
  const { month, day } = dateParts(weekStart);
  return `the week of ${String(day)} ${MONTHS_LONG[month - 1] ?? ''}`;
}

/** "in 41 hours", "in 3 days", "in 20 minutes"; "now" for the past. */
export function relativeFuture(targetMs: number, nowMs: number): string {
  const delta = targetMs - nowMs;
  if (delta <= 0) return 'now';
  const minutes = Math.round(delta / MINUTE_MS);
  if (minutes < 60) return `in ${String(minutes)} ${minutes === 1 ? 'minute' : 'minutes'}`;
  const hours = Math.round(delta / HOUR_MS);
  if (hours < 48) return `in ${String(hours)} ${hours === 1 ? 'hour' : 'hours'}`;
  const days = Math.round(delta / DAY_MS);
  return `in ${String(days)} days`;
}

/** "3 hours", "26 hours": a gap between two posts. */
export function gapLabel(ms: number): string {
  const hours = Math.abs(ms) / HOUR_MS;
  if (hours < 1) {
    const minutes = Math.round(Math.abs(ms) / MINUTE_MS);
    return `${String(minutes)} ${minutes === 1 ? 'minute' : 'minutes'}`;
  }
  const rounded = Math.round(hours);
  return `${String(rounded)} ${rounded === 1 ? 'hour' : 'hours'}`;
}

export { DAY_MS, HOUR_MS, MINUTE_MS };
