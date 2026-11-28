import type { Slot } from './types';

/** Trimmed text, or `undefined` when there is none. */
export function nonEmpty(text: string | null | undefined): string | undefined {
  const trimmed = text?.trim();
  return trimmed === undefined || trimmed === '' ? undefined : trimmed;
}

/**
 * Dates and times as the Studio shows them, in the reader's locale. Timestamps are the server's
 * fixed-width UTC ISO strings (contract 1.2); slots carry their own IANA zone.
 */

const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });
const monthFormat = new Intl.DateTimeFormat(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' });
const dayFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC' });

function parse(iso: string | null | undefined): Date | undefined {
  if (!iso) return undefined;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/** "27 Nov 2026, 14:05", or `undefined` for a missing or unreadable timestamp. */
export function formatDateTime(iso: string | null | undefined): string | undefined {
  const date = parse(iso);
  return date ? dateTimeFormat.format(date) : undefined;
}

/** "27 Nov 2026". */
export function formatDate(iso: string | null | undefined): string | undefined {
  const date = parse(iso);
  return date ? dateFormat.format(date) : undefined;
}

const PARTIAL_DATE = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/;

/**
 * A source's publication date as the research recorded it: `YYYY`, `YYYY-MM` or `YYYY-MM-DD`
 * ("2025", "Mar 2025", "12 Mar 2025"). Anything else is shown as it is.
 */
export function formatPartialDate(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const match = PARTIAL_DATE.exec(value);
  if (!match) return value;
  const [, year, month, day] = match;
  if (month === undefined) return year;
  const monthIndex = Number(month) - 1;
  if (monthIndex < 0 || monthIndex > 11) return value;
  if (day === undefined) return monthFormat.format(Date.UTC(Number(year), monthIndex, 1));
  return dayFormat.format(Date.UTC(Number(year), monthIndex, Number(day)));
}

/** The zone's city, for "08:30 New York time" ("America/New_York" -> "New York"). */
export function zoneCity(timezone: string): string {
  const city = timezone.split('/').at(-1) ?? timezone;
  return city.replace(/_/g, ' ');
}

/**
 * A post slot in its own time zone: "Tue 2 Dec, 08:30 New York time". Falls back to the reader's
 * zone when the slot's zone is unknown to the browser.
 */
export function formatSlot(slot: Pick<Slot, 'utc' | 'timezone'>): string | undefined {
  const date = parse(slot.utc);
  if (!date) return undefined;
  try {
    const local = new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: slot.timezone,
    }).format(date);
    return `${local} ${zoneCity(slot.timezone)} time`;
  } catch {
    return dateTimeFormat.format(date);
  }
}

/** "$0.42", or "less than $0.01" for a cost that rounds to nothing. */
export function formatCost(usd: number | null | undefined): string | undefined {
  if (usd === null || usd === undefined || !Number.isFinite(usd) || usd <= 0) return undefined;
  if (usd < 0.005) return 'less than $0.01';
  return `$${usd.toFixed(2)}`;
}
