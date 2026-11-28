/** Small formatting helpers for the Draft tab: numbers, counts and times in the owner's locale. */

const numberFormat = new Intl.NumberFormat();
const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });

export function formatNumber(value: number): string {
  return numberFormat.format(value);
}

/** "1 word", "1,842 words". */
export function plural(count: number, one: string, many: string): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

function dateOf(iso: string | null | undefined): Date | undefined {
  if (!iso) return undefined;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/** "27 Nov 2026, 15:04", or an empty string for a missing or unreadable time. */
export function formatDateTime(iso: string | null | undefined): string {
  const date = dateOf(iso);
  return date ? dateTimeFormat.format(date) : '';
}

/** "15:04". */
export function formatTime(iso: string | null | undefined): string {
  const date = dateOf(iso);
  return date ? timeFormat.format(date) : '';
}

/** The value of a `datetime-local` input for an ISO time, in local time. */
export function toLocalInput(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** An ISO time (UTC) for a `datetime-local` value, or `undefined` when it is not a time. */
export function fromLocalInput(value: string): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}
