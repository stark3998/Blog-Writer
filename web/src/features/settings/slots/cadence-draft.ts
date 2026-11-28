import type { components } from '@/lib/api/schema';

import { WEEKDAYS, type Weekday } from '../format';

/**
 * The cadence form's draft, its request body and its checks. The checks mirror the server's
 * (`domain.settings.CadenceSettings` ranges and `kb.checks.cadence_problems`) and use the same
 * field paths as its 422 `validation_failed` answer (`vetoNotice.time`, `postSlots.2.day`), so the
 * form shows its own errors and the server's in the same places.
 */

type Schemas = components['schemas'];
export type CadenceSettings = Schemas['CadenceSettings'];
export type CadenceIn = Schemas['CadenceIn'];
export type WeekMode = Schemas['WeekMode'];
type WeeklyTime = Schemas['WeeklyTime'];
type SlotTemplate = Schemas['SlotTemplate'];

export interface WeeklyTimeDraft {
  day: Weekday;
  time: string;
}

export interface SlotDraft {
  /** A stable key for the list (not sent). */
  key: string;
  day: Weekday;
  time: string;
  kind: SlotTemplate['kind'];
  windowMinutes: string;
}

export interface CadenceDraft {
  timezone: string;
  planWeek: WeeklyTimeDraft;
  planLeadMinutes: string;
  researchStart: WeeklyTimeDraft;
  interviewDue: WeeklyTimeDraft;
  draftDay: WeeklyTimeDraft;
  vetoNotice: WeeklyTimeDraft;
  articlePublish: WeeklyTimeDraft;
  minVetoLeadHours: string;
  businessHoursStart: string;
  businessHoursEnd: string;
  postSlots: SlotDraft[];
  postsNormal: string;
  postsLight: string;
  postsMax: string;
  sameDayBlock: boolean;
  minGapHoursWarn: string;
  missedSlotGraceMinutes: string;
  stageLeadMinutes: string;
  replyMinutes: string;
  snapshotHours: string;
  crawlIntervalHours: string;
  scout: WeeklyTimeDraft;
  digest: WeeklyTimeDraft;
  backupTime: string;
  defaultMode: WeekMode;
}

export const WEEKLY_FIELDS = [
  'planWeek',
  'researchStart',
  'interviewDue',
  'draftDay',
  'vetoNotice',
  'articlePublish',
  'scout',
  'digest',
] as const;
export type WeeklyField = (typeof WEEKLY_FIELDS)[number];

const TIME = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
export const MAX_SLOTS = 14;

/** The server's defaults (`CadenceSettings`), for fields an older document left out. */
const DEFAULT_TIMES: Readonly<Record<WeeklyField, WeeklyTimeDraft>> = {
  planWeek: { day: 'sun', time: '18:00' },
  researchStart: { day: 'mon', time: '08:00' },
  interviewDue: { day: 'wed', time: '18:00' },
  draftDay: { day: 'thu', time: '09:00' },
  vetoNotice: { day: 'fri', time: '12:00' },
  articlePublish: { day: 'mon', time: '09:00' },
  scout: { day: 'sun', time: '15:00' },
  digest: { day: 'mon', time: '08:00' },
};
const DEFAULT_SLOTS: readonly Pick<SlotTemplate, 'day' | 'time' | 'kind' | 'windowMinutes'>[] = [
  { day: 'tue', time: '10:00', kind: 'primary', windowMinutes: 120 },
  { day: 'wed', time: '10:00', kind: 'primary', windowMinutes: 120 },
  { day: 'thu', time: '10:00', kind: 'primary', windowMinutes: 120 },
  { day: 'mon', time: '12:00', kind: 'optional', windowMinutes: 120 },
  { day: 'fri', time: '15:00', kind: 'optional', windowMinutes: 120 },
];
const DEFAULT_PER_WEEK = { normal: 3, light: 2, away: 0, max: 5 };
const DEFAULT_REPLY_MINUTES = [15, 60, 180];
const DEFAULT_SNAPSHOT_HOURS = [48, 168];

function weekly(value: WeeklyTime | undefined, field: WeeklyField): WeeklyTimeDraft {
  return value ? { day: value.day, time: value.time } : { ...DEFAULT_TIMES[field] };
}

function list(values: readonly number[] | undefined): string {
  return (values ?? []).join(', ');
}

let slotKeys = 0;
/** A key for a new slot row. */
export function newSlotKey(): string {
  slotKeys += 1;
  return `slot-${String(slotKeys)}`;
}

export function slotDraft(slot: Pick<SlotTemplate, 'day' | 'time' | 'kind' | 'windowMinutes'>, key: string): SlotDraft {
  return { key, day: slot.day, time: slot.time, kind: slot.kind, windowMinutes: String(slot.windowMinutes) };
}

export function toCadenceDraft(item: CadenceSettings): CadenceDraft {
  const perWeek = item.postsPerWeek ?? DEFAULT_PER_WEEK;
  return {
    timezone: item.timezone,
    planWeek: weekly(item.planWeek, 'planWeek'),
    planLeadMinutes: String(item.planLeadMinutes),
    researchStart: weekly(item.researchStart, 'researchStart'),
    interviewDue: weekly(item.interviewDue, 'interviewDue'),
    draftDay: weekly(item.draftDay, 'draftDay'),
    vetoNotice: weekly(item.vetoNotice, 'vetoNotice'),
    articlePublish: weekly(item.articlePublish, 'articlePublish'),
    minVetoLeadHours: String(item.minVetoLeadHours),
    businessHoursStart: item.businessHoursStart,
    businessHoursEnd: item.businessHoursEnd,
    // Keys by position: stable for one load of the document, and equal on every call, so a draft
    // compares equal to the document it came from.
    postSlots: (item.postSlots ?? DEFAULT_SLOTS).map((slot, index) => slotDraft(slot, `saved-${String(index)}`)),
    postsNormal: String(perWeek.normal),
    postsLight: String(perWeek.light),
    postsMax: String(perWeek.max),
    sameDayBlock: item.sameDayBlock,
    minGapHoursWarn: String(item.minGapHoursWarn),
    missedSlotGraceMinutes: String(item.missedSlotGraceMinutes),
    stageLeadMinutes: String(item.stageLeadMinutes),
    replyMinutes: list(item.reminders ? item.reminders.replyMinutes : DEFAULT_REPLY_MINUTES),
    snapshotHours: list(item.reminders ? item.reminders.snapshotHours : DEFAULT_SNAPSHOT_HOURS),
    crawlIntervalHours: String(item.crawlIntervalHours),
    scout: weekly(item.scout, 'scout'),
    digest: weekly(item.digest, 'digest'),
    backupTime: item.backupTime,
    defaultMode: item.defaultMode,
  };
}

/** A whole number, or `undefined` for anything else ("", "1.5", "two"). */
export function wholeNumber(text: string): number | undefined {
  const trimmed = text.trim();
  if (!/^-?\d+$/.test(trimmed)) return undefined;
  const value = Number(trimmed);
  return Number.isSafeInteger(value) ? value : undefined;
}

/** "15, 60, 180" -> [15, 60, 180]; `undefined` entries mark what is not a whole number. */
export function numberList(text: string): (number | undefined)[] {
  return text
    .split(/[\s,;]+/)
    .filter((part) => part !== '')
    .map(wholeNumber);
}

function weekMinutes(value: WeeklyTimeDraft): number | undefined {
  if (!TIME.test(value.time)) return undefined;
  const [hours = '0', minutes = '0'] = value.time.split(':');
  return WEEKDAYS.indexOf(value.day) * 1440 + Number(hours) * 60 + Number(minutes);
}

function dayMinutes(time: string): number | undefined {
  if (!TIME.test(time)) return undefined;
  const [hours = '0', minutes = '0'] = time.split(':');
  return Number(hours) * 60 + Number(minutes);
}

/** True for an IANA zone the browser knows (the server checks with its own zone database). */
export function isKnownZone(zone: string): boolean {
  if (zone.trim() === '' || !zone.includes('/')) return zone === 'UTC';
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** Every problem with the draft, by request path. Empty when it can be saved. */
export function cadenceProblems(draft: CadenceDraft): Record<string, string> {
  const problems: Record<string, string> = {};
  const add = (path: string, message: string) => {
    problems[path] ??= message;
  };
  const range = (path: string, text: string, min: number, max: number) => {
    const value = wholeNumber(text);
    if (value === undefined) add(path, 'Enter a whole number.');
    else if (value < min || value > max) add(path, `Between ${String(min)} and ${String(max)}.`);
    return value;
  };
  const time = (path: string, value: string) => {
    if (!TIME.test(value)) add(path, 'Use a time such as 09:30.');
  };

  if (!isKnownZone(draft.timezone)) add('timezone', 'Pick a time zone such as America/New_York.');
  for (const field of WEEKLY_FIELDS) time(`${field}.time`, draft[field].time);
  range('planLeadMinutes', draft.planLeadMinutes, 0, 720);
  const lead = range('minVetoLeadHours', draft.minVetoLeadHours, 24, 168);
  time('businessHoursStart', draft.businessHoursStart);
  time('businessHoursEnd', draft.businessHoursEnd);
  time('backupTime', draft.backupTime);

  const start = dayMinutes(draft.businessHoursStart);
  const end = dayMinutes(draft.businessHoursEnd);
  if (start !== undefined && end !== undefined) {
    if (start >= end) add('businessHoursEnd', 'Business hours must end after they start.');
    const veto = dayMinutes(draft.vetoNotice.time);
    if (start < end && veto !== undefined && (veto < start || veto > end)) {
      add('vetoNotice.time', 'The veto notice goes out within business hours.');
    }
  }
  const notice = weekMinutes(draft.vetoNotice);
  const publish = weekMinutes(draft.articlePublish);
  if (notice !== undefined && publish !== undefined && lead !== undefined) {
    const gap = (publish - notice + 7 * 1440) % (7 * 1440);
    if (gap < lead * 60) {
      add('vetoNotice', `The veto notice must go out at least ${String(lead)} hours before the article publishes.`);
    }
  }

  if (draft.postSlots.length > MAX_SLOTS) add('postSlots', `At most ${String(MAX_SLOTS)} slots.`);
  const seenTimes = new Set<string>();
  const seenDays = new Set<Weekday>();
  draft.postSlots.forEach((slot, index) => {
    const base = `postSlots.${String(index)}`;
    time(`${base}.time`, slot.time);
    range(`${base}.windowMinutes`, slot.windowMinutes, 0, 240);
    const id = `${slot.day} ${slot.time}`;
    if (seenTimes.has(id)) add(base, 'Listed twice.');
    else if (draft.sameDayBlock && seenDays.has(slot.day)) {
      add(`${base}.day`, 'Two slots on one day while two posts on a day are blocked.');
    }
    seenTimes.add(id);
    seenDays.add(slot.day);
  });

  const normal = range('postsPerWeek.normal', draft.postsNormal, 0, 7);
  const light = range('postsPerWeek.light', draft.postsLight, 0, 7);
  const most = range('postsPerWeek.max', draft.postsMax, 1, 7);
  if (normal !== undefined && light !== undefined && most !== undefined && !(light <= normal && normal <= most)) {
    add('postsPerWeek', 'Posts per week must be light ≤ normal ≤ maximum.');
  }

  range('minGapHoursWarn', draft.minGapHoursWarn, 0, 72);
  range('missedSlotGraceMinutes', draft.missedSlotGraceMinutes, 0, 720);
  range('stageLeadMinutes', draft.stageLeadMinutes, 15, 240);
  range('crawlIntervalHours', draft.crawlIntervalHours, 1, 48);

  const offsets = (path: string, text: string, max: number, most: number) => {
    const values = numberList(text);
    if (values.length > most) add(path, `At most ${String(most)} reminders.`);
    const seen = new Set<number>();
    values.forEach((value, index) => {
      const at = `${path}.${String(index)}`;
      if (value === undefined) add(at, 'Use whole numbers separated by commas.');
      else if (value < 1 || value > max) add(at, `Each between 1 and ${String(max)}.`);
      else if (seen.has(value)) add(at, 'Listed twice.');
      if (value !== undefined) seen.add(value);
    });
  };
  offsets('reminders.replyMinutes', draft.replyMinutes, 1440, 6);
  offsets('reminders.snapshotHours', draft.snapshotHours, 720, 4);
  return problems;
}

/** The PUT body. Nested values keep fields this client does not know (a newer server's). */
export function toCadenceBody(draft: CadenceDraft, base: CadenceSettings): CadenceIn {
  const whole = (text: string) => wholeNumber(text) ?? 0;
  const at = (value: WeeklyTimeDraft, original: WeeklyTime | undefined): WeeklyTime => ({
    ...original,
    day: value.day,
    time: value.time,
  });
  const numbers = (text: string) => numberList(text).filter((value): value is number => value !== undefined);
  const originalSlots = base.postSlots ?? [];
  return {
    timezone: draft.timezone.trim(),
    planWeek: at(draft.planWeek, base.planWeek),
    planLeadMinutes: whole(draft.planLeadMinutes),
    researchStart: at(draft.researchStart, base.researchStart),
    interviewDue: at(draft.interviewDue, base.interviewDue),
    draftDay: at(draft.draftDay, base.draftDay),
    vetoNotice: at(draft.vetoNotice, base.vetoNotice),
    articlePublish: at(draft.articlePublish, base.articlePublish),
    minVetoLeadHours: whole(draft.minVetoLeadHours),
    businessHoursStart: draft.businessHoursStart,
    businessHoursEnd: draft.businessHoursEnd,
    postSlots: draft.postSlots.map((slot) => {
      const index = /^saved-(\d+)$/.exec(slot.key)?.[1];
      const original = index === undefined ? undefined : originalSlots[Number(index)];
      return {
        ...original,
        day: slot.day,
        time: slot.time,
        kind: slot.kind,
        windowMinutes: whole(slot.windowMinutes),
      };
    }),
    postsPerWeek: {
      ...base.postsPerWeek,
      normal: whole(draft.postsNormal),
      light: whole(draft.postsLight),
      away: 0,
      max: whole(draft.postsMax),
    },
    sameDayBlock: draft.sameDayBlock,
    minGapHoursWarn: whole(draft.minGapHoursWarn),
    missedSlotGraceMinutes: whole(draft.missedSlotGraceMinutes),
    stageLeadMinutes: whole(draft.stageLeadMinutes),
    reminders: {
      ...base.reminders,
      replyMinutes: numbers(draft.replyMinutes),
      snapshotHours: numbers(draft.snapshotHours),
    },
    crawlIntervalHours: whole(draft.crawlIntervalHours),
    scout: at(draft.scout, base.scout),
    digest: at(draft.digest, base.digest),
    backupTime: draft.backupTime,
    defaultMode: draft.defaultMode,
  };
}

/** The slots ordered through the week (for the week strip and the conflict comparison). */
export function slotsByWeek(slots: readonly Pick<SlotDraft, 'day' | 'time'>[]): Pick<SlotDraft, 'day' | 'time'>[] {
  return [...slots].sort(
    (a, b) => WEEKDAYS.indexOf(a.day) - WEEKDAYS.indexOf(b.day) || a.time.localeCompare(b.time),
  );
}
