import type { components } from '@/lib/api/schema';

/**
 * Words and numbers for Settings and Insights: money, rates, times in the owner's zone, schedule
 * rules and job names. Everything takes plain values (no hooks), so it is easy to test.
 */

export type Weekday = components['schemas']['Weekday'];
type ScheduleRule = components['schemas']['ScheduleRuleView'];

export const WEEKDAYS: readonly Weekday[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

export const WEEKDAY_LABELS: Readonly<Record<Weekday, string>> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
};

const usd = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const usdPrecise = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

/** "$4.12"; amounts under a cent but above zero read "under $0.01". */
export function formatUsd(value: number): string {
  if (value > 0 && value < 0.005) return `under ${usd.format(0.01)}`;
  return usd.format(value);
}

/** A price with the precision prices need ("$0.075" per 1M tokens). */
export function formatPrice(value: number): string {
  return usdPrecise.format(value);
}

const percentFormats = new Map<number, Intl.NumberFormat>();

/** A share as a percentage: `0.125` is "13%" (or "12.5%" with one digit). */
export function formatPercent(fraction: number, digits = 0): string {
  let format = percentFormats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: digits });
    percentFormats.set(digits, format);
  }
  return format.format(fraction);
}

const signedPercent = new Intl.NumberFormat(undefined, {
  style: 'percent',
  maximumFractionDigits: 0,
  signDisplay: 'exceptZero',
});

/** A relative difference with its sign: `0.25` is "+25%", `-0.1` is "−10%". */
export function formatSignedPercent(fraction: number): string {
  return signedPercent.format(fraction);
}

const decimal = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const wholeNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

/** 12.3 / 4 / 1,284. */
export function formatNumber(value: number, { whole = false }: { whole?: boolean } = {}): string {
  return (whole ? wholeNumber : decimal).format(value);
}

function validDate(iso: string | null | undefined): Date | undefined {
  if (!iso) return undefined;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

const dateTimeFormats = new Map<string, Intl.DateTimeFormat>();

function dateTimeFormat(key: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  let format = dateTimeFormats.get(key);
  if (!format) {
    format = new Intl.DateTimeFormat(undefined, options);
    dateTimeFormats.set(key, format);
  }
  return format;
}

/** A zone the browser knows, or `undefined` (the browser's own zone then). */
function knownZone(timeZone: string | undefined): string | undefined {
  if (!timeZone) return undefined;
  try {
    new Intl.DateTimeFormat(undefined, { timeZone });
    return timeZone;
  } catch {
    return undefined;
  }
}

/** "27 Nov 2026, 15:04" in `timeZone` (the browser's zone without one); "–" when unknown. */
export function formatDateTime(iso: string | null | undefined, timeZone?: string): string {
  const date = validDate(iso);
  if (!date) return '–';
  const zone = knownZone(timeZone);
  return dateTimeFormat(`dt:${zone ?? ''}`, { dateStyle: 'medium', timeStyle: 'short', timeZone: zone }).format(date);
}

/** "27 Nov 2026" in `timeZone`; "–" when unknown. */
export function formatDate(iso: string | null | undefined, timeZone?: string): string {
  const date = validDate(iso);
  if (!date) return '–';
  const zone = knownZone(timeZone);
  return dateTimeFormat(`d:${zone ?? ''}`, { dateStyle: 'medium', timeZone: zone }).format(date);
}

/** "Thu 27 Nov" for a `YYYY-MM-DD` calendar date (no time zone shift). */
export function formatDay(localDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(localDate);
  if (!match) return localDate;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return dateTimeFormat('day', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date);
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
const UNITS: readonly [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
];

/** "3 hours ago", "in 2 days", "now" against `now` (ms since the epoch); "–" when unknown. */
export function formatRelative(iso: string | null | undefined, now: number): string {
  const date = validDate(iso);
  if (!date) return '–';
  const seconds = Math.round((date.getTime() - now) / 1000);
  const size = Math.abs(seconds);
  if (size < 45) return relative.format(0, 'second');
  for (const [unit, length] of UNITS) {
    if (size >= length) return relative.format(Math.round(seconds / length), unit);
  }
  return relative.format(Math.round(seconds / 60), 'minute');
}

/** "15 min", "2 h", "1 h 30 min". */
export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${formatNumber(minutes, { whole: true })} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${String(hours)} h` : `${String(hours)} h ${String(rest)} min`;
}

/** "48 h" or "7 days" for reminder offsets in hours. */
export function formatHours(hours: number): string {
  if (hours >= 48 && hours % 24 === 0) return `${String(hours / 24)} days`;
  return `${formatNumber(hours, { whole: true })} h`;
}

function listDays(days: readonly Weekday[]): string {
  const names = days.map((day) => WEEKDAY_LABELS[day]);
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1) ?? ''}`;
}

/** A schedule rule in words: "Every Sunday at 15:00", "Every day at 03:30", "Every 6 hours". */
export function describeRule(rule: ScheduleRule): string {
  switch (rule.kind) {
    case 'daily':
      return `Every day at ${rule.at ?? '–'}`;
    case 'weekly':
      return `Every ${listDays(rule.days ?? [])} at ${rule.at ?? '–'}`;
    case 'interval': {
      const seconds = rule.seconds ?? 0;
      if (seconds > 0 && seconds % 3600 === 0) {
        const hours = seconds / 3600;
        return hours === 1 ? 'Every hour' : `Every ${String(hours)} hours`;
      }
      return `Every ${formatMinutes(Math.round(seconds / 60))}`;
    }
  }
}

/** Owner-facing names of the job types (`domain.jobtypes`). */
const JOB_TYPE_LABELS: Readonly<Record<string, string>> = {
  'research.run': 'Research run',
  'signals.crawl': 'Feed crawl and triage',
  'signals.scout': 'Weekly scouting',
  'stories.seed': 'Story seeding',
  'stories.classify': 'Story confidentiality check',
  'voice.build-guide': 'Voice guide build',
  'voice.digest': 'Voice digest',
  'gates.run': 'Gate checks',
  'gates.standards-refresh': 'Standards catalog refresh',
  'linkedin.introspect': 'LinkedIn token check',
  'backups.dump': 'Backup',
  'pipeline.article': 'Article pipeline',
  'pipeline.revise': 'Article revision',
  'pipeline.interview-expire': 'Interview expiry',
  'publish.article': 'Publish to the portfolio',
  'publish.unpublish': 'Unpublish from the portfolio',
  'publish.staging': 'Portfolio preview build',
  'posts.derive': 'Posts from an article',
  'posts.generate': 'Post writing',
  'posts.check': 'Post checks',
  'posts.render': 'Post media render',
  'posts.stage': 'Post staging for its slot',
  'posts.expire': 'Post expiry',
  'planner.plan-week': 'Weekly plan',
  'planner.veto-notice': 'Veto notice',
  'planner.remind': 'Reminder',
  'planner.post-live': 'Reminders after go-live',
  'planner.post-scheduled': 'Reminders for a scheduled post',
  'planner.slot-check': 'Missed-slot check',
  'planner.scorecard': 'Weekly scorecard',
  'engagement.purge': 'Pasted-text cleanup',
  'analytics.suggest': 'Insight suggestions',
  'analytics.eval': 'Evaluation run',
};

/** "signals.crawl" -> "Feed crawl and triage"; unknown types read as words. */
export function jobTypeLabel(type: string): string {
  const known = JOB_TYPE_LABELS[type];
  if (known) return known;
  const words = type.replace(/[._-]+/g, ' ').trim();
  return words === '' ? type : words.charAt(0).toUpperCase() + words.slice(1);
}

/** The job types the System page filters by, in the order of the pipeline. */
export const JOB_TYPES: readonly string[] = Object.keys(JOB_TYPE_LABELS);
