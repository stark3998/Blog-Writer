import type { Slot } from './types';

/**
 * Times as the go-live page and the composer say them. A slot is a local date and time in the
 * planner's IANA zone plus its UTC instant (contract 1.2); it is shown in that zone, named, so a
 * slot reads the same on any device.
 */

function validDate(iso: string | null | undefined): Date | undefined {
  if (!iso) return undefined;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/** "Tue 1 Dec, 09:00 EST" for a slot, in the slot's own zone. */
export function formatSlot(slot: Pick<Slot, 'utc' | 'timezone'>): string {
  return formatInstant(slot.utc, slot.timezone);
}

/** "Tue 1 Dec, 09:00 EST": a weekday, date and time with the zone's short name. */
export function formatInstant(iso: string | null | undefined, timeZone?: string): string {
  const date = validDate(iso);
  if (!date) return '';
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone,
      timeZoneName: 'short',
    }).format(date);
  } catch {
    // An unknown zone name: fall back to the device's zone rather than failing the page.
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  }
}

/** "09:00" for the slot's local time in its zone (the short label a push uses). */
export function formatTime(iso: string | null | undefined, timeZone?: string): string {
  const date = validDate(iso);
  if (!date) return '';
  try {
    return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', timeZone }).format(date);
  } catch {
    return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(date);
  }
}

/** "26 Nov, 15:04" for a moment that has happened (approval, posting). */
export function formatMoment(iso: string | null | undefined): string {
  const date = validDate(iso);
  if (!date) return '';
  return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(
    date,
  );
}

/**
 * "in 25 minutes", "in 3 hours", "5 minutes ago", "now": how far `iso` is from `now`, rounded
 * the way a person would say it.
 */
export function relativeTime(iso: string | null | undefined, now: number): string {
  const date = validDate(iso);
  if (!date) return '';
  const deltaMs = date.getTime() - now;
  const minutes = Math.round(Math.abs(deltaMs) / 60_000);
  if (minutes < 1) return 'now';
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  const sign = deltaMs < 0 ? -1 : 1;
  if (minutes < 60) return format.format(sign * minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (hours < 48) return format.format(sign * hours, 'hour');
  return format.format(sign * Math.round(hours / 24), 'day');
}

/** "2026-12-01": the calendar day of an instant in a zone. */
function localDay(epochMs: number, timeZone: string | undefined): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone }).format(epochMs);
  } catch {
    return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(epochMs);
  }
}

/**
 * "today", "tomorrow", "yesterday" or "on Tue 1 Dec": the day of `iso` relative to `now`,
 * both read in `timeZone` (a slot's own zone).
 */
export function dayPhrase(iso: string, now: number, timeZone?: string): string {
  const date = validDate(iso);
  if (!date) return '';
  const day = Date.parse(`${localDay(date.getTime(), timeZone)}T00:00:00Z`);
  const today = Date.parse(`${localDay(now, timeZone)}T00:00:00Z`);
  const diff = Math.round((day - today) / 86_400_000);
  if (diff === 0) return 'today';
  if (diff === 1) return 'tomorrow';
  if (diff === -1) return 'yesterday';
  try {
    return `on ${new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', timeZone }).format(date)}`;
  } catch {
    return `on ${new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' }).format(date)}`;
  }
}

/** The value of a `datetime-local` input for an instant, in the device's zone ("2026-12-01T09:00"). */
export function toLocalInput(iso: string | null | undefined): string {
  const date = validDate(iso);
  if (!date) return '';
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** The instant a `datetime-local` value means in the device's zone, as ISO-8601 UTC; `undefined` if empty or invalid. */
export function fromLocalInput(value: string): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/** The device's IANA zone name ("Europe/Berlin"), for labels next to local inputs. */
export function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}
