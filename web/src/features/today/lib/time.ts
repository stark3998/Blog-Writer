/**
 * Time words for Today and the Engage panel: when something is due, how long other members' text
 * is kept, when a voice was last engaged. Pure functions of an ISO timestamp and `now` (epoch ms),
 * so the components pass the clock from `useNow` and the tests pass a fixed one.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export interface TimeOptions {
  /** BCP 47 locale for dates and times (default: the device's). */
  readonly locale?: string;
  /** IANA zone (default: the device's). */
  readonly timeZone?: string;
}

/** Epoch milliseconds of an ISO timestamp, or `undefined` when it is not a date. */
export function timeOf(iso: string | null | undefined): number | undefined {
  if (!iso) return undefined;
  const value = Date.parse(iso);
  return Number.isNaN(value) ? undefined : value;
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** "25 min", "3 h", "2 days": a duration as people say it, rounded up for what is left. */
export function spanWords(ms: number): string {
  const span = Math.max(0, ms);
  if (span < HOUR) return `${Math.max(1, Math.ceil(span / MINUTE))} min`;
  if (span < 2 * DAY) return `${Math.ceil(span / HOUR)} h`;
  return plural(Math.ceil(span / DAY), 'day');
}

/** "25 min", "3 h", "2 days" for time that has passed (rounded down: "1 h" means at least an hour). */
export function agoWords(ms: number): string {
  const span = Math.max(0, ms);
  if (span < HOUR) return `${Math.max(1, Math.floor(span / MINUTE))} min`;
  if (span < 2 * DAY) return `${Math.floor(span / HOUR)} h`;
  return plural(Math.floor(span / DAY), 'day');
}

function dayKey(time: number, timeZone?: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(time);
}

/** True when both instants fall on the same calendar day in `timeZone`. */
export function isSameDay(a: number, b: number, timeZone?: string): boolean {
  return dayKey(a, timeZone) === dayKey(b, timeZone);
}

/** "10:00 AM" (the locale's clock). */
export function clockTime(time: number, { locale, timeZone }: TimeOptions = {}): string {
  return new Intl.DateTimeFormat(locale, { timeZone, hour: 'numeric', minute: '2-digit' }).format(time);
}

/**
 * "today at 10:00 AM", "tomorrow at 9:00 AM", "Tue at 10:00 AM" (within a week), "Dec 3 at
 * 10:00 AM" (later): a moment relative to `now`, for "Due …" and "Auto-publishes …".
 */
export function momentWords(time: number, now: number, options: TimeOptions = {}): string {
  const { locale, timeZone } = options;
  const at = clockTime(time, options);
  if (isSameDay(time, now, timeZone)) return `today at ${at}`;
  if (isSameDay(time, now + DAY, timeZone)) return `tomorrow at ${at}`;
  if (isSameDay(time, now - DAY, timeZone)) return `yesterday at ${at}`;
  if (time > now && time - now < 6 * DAY) {
    return `${new Intl.DateTimeFormat(locale, { timeZone, weekday: 'short' }).format(time)} at ${at}`;
  }
  return `${new Intl.DateTimeFormat(locale, { timeZone, month: 'short', day: 'numeric' }).format(time)} at ${at}`;
}

export type DueTone = 'overdue' | 'soon' | 'later';

export interface DueLabel {
  /** "Due in 25 min", "Overdue by 2 h", "Due tomorrow at 9:00 AM". */
  readonly text: string;
  readonly tone: DueTone;
}

/** Within this, a due time reads as a countdown ("Due in 25 min") and counts as soon. */
const COUNTDOWN = 2 * HOUR;

/** When an item is due, relative to `now`. */
export function dueLabel(dueAt: string | null | undefined, now: number, options: TimeOptions = {}): DueLabel | undefined {
  const due = timeOf(dueAt);
  if (due === undefined) return undefined;
  const left = due - now;
  if (left < 0) return { text: `Overdue by ${agoWords(-left)}`, tone: 'overdue' };
  if (left < COUNTDOWN) return { text: `Due in ${spanWords(left)}`, tone: 'soon' };
  return { text: `Due ${momentWords(due, now, options)}`, tone: 'later' };
}

/** How long other members' text is still kept: "Deleted in 31 h", "Deleted in 12 min". */
export function keptWords(expiresAt: string | null | undefined, now: number): string {
  const expires = timeOf(expiresAt);
  if (expires === undefined) return 'Deleted within 48 h';
  const left = expires - now;
  return left <= 0 ? 'Being deleted now' : `Deleted in ${spanWords(left)}`;
}

/** "today", "yesterday", "3 days ago": when something last happened. */
export function sinceWords(iso: string | null | undefined, now: number, timeZone?: string): string | undefined {
  const time = timeOf(iso);
  if (time === undefined) return undefined;
  if (isSameDay(time, now, timeZone)) return 'today';
  if (isSameDay(time, now - DAY, timeZone)) return 'yesterday';
  if (time > now) return 'today';
  return `${agoWords(now - time)} ago`;
}

/** "Nov 23": a short date. */
export function shortDate(iso: string, { locale, timeZone }: TimeOptions = {}): string {
  const time = timeOf(iso.length === 10 ? `${iso}T12:00:00Z` : iso);
  if (time === undefined) return iso;
  // A bare date (a week's Monday) is a calendar day, not an instant: read it in UTC.
  const zone = iso.length === 10 ? 'UTC' : timeZone;
  return new Intl.DateTimeFormat(locale, { timeZone: zone, month: 'short', day: 'numeric' }).format(time);
}
