/** Numbers, dates and counts as the Library writes them. */

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const date = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });
const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const monthDay = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });

/** "1,500". */
export function formatInteger(value: number): string {
  return integer.format(value);
}

/** "2.5"; whole numbers without a decimal. */
export function formatDecimal(value: number): string {
  return decimal.format(value);
}

/** A 0-1 fraction as a percentage: 0.33 is "33%", 0.335 is "33.5%". */
export function formatShare(fraction: number): string {
  return `${decimal.format(Math.round(fraction * 1000) / 10)}%`;
}

function parse(iso: string | null | undefined): Date | undefined {
  if (!iso) return undefined;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed;
}

/** "Nov 26, 2026", or `undefined` for a missing or unreadable timestamp. */
export function formatDate(iso: string | null | undefined): string | undefined {
  const parsed = parse(iso);
  return parsed ? date.format(parsed) : undefined;
}

/** "Nov 26, 2026, 3:04 PM". */
export function formatDateTime(iso: string | null | undefined): string | undefined {
  const parsed = parse(iso);
  return parsed ? dateTime.format(parsed) : undefined;
}

/** "Nov 16 – Nov 22, 2026": a digest's period. */
export function formatPeriod(startIso: string, endIso: string): string {
  const start = parse(startIso);
  const end = parse(endIso);
  if (!start || !end) return formatDate(startIso) ?? startIso;
  return `${monthDay.format(start)} – ${date.format(end)}`;
}

/** "1 story", "3 stories": the count and the right noun. */
export function plural(count: number, one: string, many: string): string {
  return `${formatInteger(count)} ${count === 1 ? one : many}`;
}

/** "a, b and c". */
export function listOf(items: readonly string[]): string {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items.at(-1) ?? ''}`;
}

/** The date part of a timestamp for `<input type="date">` ("2026-11-26"), or "". */
export function toDateInput(iso: string | null | undefined): string {
  if (!iso) return '';
  return /^\d{4}-\d{2}-\d{2}/.exec(iso)?.[0] ?? '';
}

/** A date input's value as the server's fixed-width UTC timestamp (midnight UTC), or `null`. */
export function fromDateInput(value: string): string | null {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00.000000Z` : null;
}
