import type { components } from '@/lib/api/schema';

import type { CadenceView, SlotTemplate, WeeklyTime } from './settings';
import {
  addDays,
  gapLabel,
  HOUR_MS,
  localDateOf,
  localToUtcMs,
  momentLabel,
  parseTimestamp,
  slotLabel,
  utcToLocal,
  WEEKDAY_IDS,
  weekDates,
  weekdayId,
} from './time';

/**
 * The week plan's rules as the web needs them (pure: no I/O, no clock). They mirror the server's
 * (`blogwriter.planner.plan`, `blogwriter.domain.slots`) so the calendar can show a hard block or
 * a warning **before** a move is sent; the server checks everything again and stays the authority
 * (its findings land in `WeekPlan.warnings`).
 */

type Schemas = components['schemas'];
export type WeekPlan = Schemas['WeekPlan'];
export type PlannedSlot = Schemas['PlannedSlot'];
export type PlanSlotIn = Schemas['PlanSlotIn'];
export type PostSummary = Schemas['PostSummary'];
export type Finding = Schemas['Finding'];
export type Lane = Schemas['Lane'];
export type PostFormat = Schemas['PostFormat'];
export type PostStatus = Schemas['PostStatus'];
export type PlanStatus = Schemas['PlanStatus'];
export type ArticleStatus = Schemas['ArticleStatus'];
export type FlagshipPlan = Schemas['FlagshipPlan'];

/** The id of a week's plan (`domain.weeks.plan_id`); also its query key's id. */
export function planIdFor(weekStart: string): string {
  return `plan-${weekStart}`;
}

/** Posts that hold their go-live day: nothing else may go out that day (`planner.slots.taken_slots`). */
export const HOLDING_STATUSES: ReadonlySet<PostStatus> = new Set<PostStatus>([
  'approved',
  'staged',
  'publishing',
  'needs_attention',
  'kit',
  'posted',
]);

/** Posts that are gone: they hold nothing and the calendar leaves them out. */
export const GONE_STATUSES: ReadonlySet<PostStatus> = new Set<PostStatus>(['archived', 'expired', 'deleted']);

/** Posts the planner may still move (`planner.slots.MOVABLE_STATUSES`). */
export const MOVABLE_STATUSES: ReadonlySet<PostStatus> = new Set<PostStatus>([
  'generating',
  'draft',
  'approved',
  'staged',
  'missed',
]);

// ------------------------------------------------------------------------------------------------
// Slots
// ------------------------------------------------------------------------------------------------

export function slotUtcMs(slot: PlannedSlot): number {
  return parseTimestamp(slot.slot.utc) ?? localToUtcMs(slot.slot.localDate, slot.slot.localTime, slot.slot.timezone);
}

/** The plan's slots in time order. */
export function sortedSlots(plan: WeekPlan): PlannedSlot[] {
  return [...(plan.slots ?? [])].sort((a, b) => slotUtcMs(a) - slotUtcMs(b));
}

/** The templates of one weekday, primary first, then by time. */
export function templatesFor(cadence: CadenceView, day: string): SlotTemplate[] {
  const id = weekdayId(day);
  return cadence.postSlots
    .filter((template) => template.day === id)
    .sort((a, b) => (a.kind === b.kind ? a.time.localeCompare(b.time) : a.kind === 'primary' ? -1 : 1));
}

/** `primary`/`optional` when a template has this weekday and time, else `custom` (`planner.slots.template_kind`). */
export function templateKind(cadence: CadenceView, date: string, time: string): 'primary' | 'optional' | 'custom' {
  return templatesFor(cadence, date).find((template) => template.time === time)?.kind ?? 'custom';
}

/**
 * The time a slot takes on another day: that day's template time when the slot sits at a
 * template time now (moving Tue 10:00 to Monday gives Monday's 12:00), else its own time (a time
 * the owner picked stays).
 */
export function targetTime(cadence: CadenceView, slot: PlannedSlot, date: string): string {
  const current = slot.slot.localTime;
  const atTemplate = templateKind(cadence, slot.slot.localDate, current) !== 'custom';
  if (!atTemplate) return current;
  return templatesFor(cadence, date)[0]?.time ?? current;
}

// ------------------------------------------------------------------------------------------------
// What holds a moment in and around the week
// ------------------------------------------------------------------------------------------------

export interface CalendarEntry {
  /** The plan slot's id, or the post's id for a post outside the plan. */
  readonly id: string;
  readonly kind: 'slot' | 'post';
  readonly utcMs: number;
  readonly date: string;
  readonly time: string;
  /** "Wed 2 Dec, 10:00". */
  readonly label: string;
  readonly title: string;
}

/** The ids of posts the plan's slots hold. */
export function planPostIds(plan: WeekPlan | undefined): Set<string> {
  const ids = new Set<string>();
  for (const slot of plan?.slots ?? []) if (slot.postId) ids.add(slot.postId);
  return ids;
}

/** When a post holds its day: when it went out, else its slot. */
export function postMoment(post: PostSummary): number | undefined {
  if (post.status === 'posted') return parseTimestamp(post.postedAt) ?? parseTimestamp(post.slot?.utc);
  return parseTimestamp(post.slot?.utc);
}

/** A slot's title for people: its working title, the post's hook, or what it is. */
export function slotTitle(slot: PlannedSlot, post?: PostSummary): string {
  const hook = post?.hook.trim();
  if (hook) return hook;
  const title = slot.title?.trim();
  if (title) return title;
  return 'Untitled post';
}

/** Everything that holds a moment: the plan's slots and the posts outside the plan that hold a day. */
export function calendarEntries(
  plan: WeekPlan | undefined,
  posts: readonly PostSummary[],
  zone: string,
): CalendarEntry[] {
  const inPlan = planPostIds(plan);
  const byId = new Map(posts.map((post) => [post.id, post]));
  const entries: CalendarEntry[] = [];
  for (const slot of plan?.slots ?? []) {
    const utcMs = slotUtcMs(slot);
    const { date, time } = utcToLocal(utcMs, zone);
    entries.push({
      id: slot.id,
      kind: 'slot',
      utcMs,
      date,
      time,
      label: slotLabel(date, time),
      title: slotTitle(slot, slot.postId ? byId.get(slot.postId) : undefined),
    });
  }
  for (const post of posts) {
    if (inPlan.has(post.id) || !HOLDING_STATUSES.has(post.status)) continue;
    const utcMs = postMoment(post);
    if (utcMs === undefined) continue;
    const { date, time } = utcToLocal(utcMs, zone);
    entries.push({
      id: post.id,
      kind: 'post',
      utcMs,
      date,
      time,
      label: slotLabel(date, time),
      title: post.hook.trim() || 'A post outside the plan',
    });
  }
  return entries.sort((a, b) => a.utcMs - b.utcMs);
}

/** Posts with a slot in the week that are not in the plan and still on the calendar. */
export function outsidePosts(plan: WeekPlan | undefined, posts: readonly PostSummary[]): PostSummary[] {
  const inPlan = planPostIds(plan);
  return posts.filter((post) => !inPlan.has(post.id) && !GONE_STATUSES.has(post.status) && post.slot !== null);
}

// ------------------------------------------------------------------------------------------------
// Moving a slot: what each day of the week allows
// ------------------------------------------------------------------------------------------------

export type DayBlock =
  | { readonly reason: 'taken'; readonly by: CalendarEntry }
  | { readonly reason: 'past' };

export interface DayWarning {
  readonly with: CalendarEntry;
  readonly gapMs: number;
  /** "Under 24 hours from Wed 2 Dec, 10:00 (19 hours)". */
  readonly message: string;
}

export interface DayOption {
  readonly date: string;
  /** The time the slot would take on this day. */
  readonly time: string;
  readonly utcMs: number;
  /** The day the slot is on now. */
  readonly current: boolean;
  /** Why the slot cannot go here; `undefined` when it can. */
  readonly block: DayBlock | undefined;
  /** Allowed, with something to know first. */
  readonly warnings: readonly DayWarning[];
}

export interface MoveContext {
  readonly plan: WeekPlan;
  readonly posts: readonly PostSummary[];
  readonly cadence: CadenceView;
  readonly zone: string;
  readonly nowMs: number;
}

function sameDayEntries(entries: readonly CalendarEntry[], date: string, except: string): CalendarEntry[] {
  return entries.filter((entry) => entry.date === date && entry.id !== except);
}

/**
 * Where a slot may go: for each day of the week, the time it would take there, whether a post
 * already holds that day (a hard block: posts are never stacked) or the time has passed, and the
 * posts less than `minGapHoursWarn` hours away (a warning only).
 */
export function dayOptions(slot: PlannedSlot, context: MoveContext, time?: string): DayOption[] {
  const { plan, posts, cadence, zone, nowMs } = context;
  const entries = calendarEntries(plan, posts, zone);
  const others = entries.filter((entry) => entry.id !== slot.id && entry.id !== slot.postId);
  const minGapMs = cadence.minGapHoursWarn * HOUR_MS;
  return weekDates(plan.weekStart).map((date) => {
    const chosen = time ?? targetTime(cadence, slot, date);
    const utcMs = localToUtcMs(date, chosen, zone);
    const current = date === slot.slot.localDate && chosen === slot.slot.localTime;
    const holders = sameDayEntries(others, date, slot.id);
    let block: DayBlock | undefined;
    if (utcMs <= nowMs) block = { reason: 'past' };
    else if (holders[0] && cadence.sameDayBlock) block = { reason: 'taken', by: holders[0] };
    const warnings: DayWarning[] = [];
    for (const other of others) {
      const gapMs = Math.abs(other.utcMs - utcMs);
      const sameDay = other.date === date;
      if (sameDay && cadence.sameDayBlock) continue;
      if (sameDay || gapMs < minGapMs) {
        warnings.push({
          with: other,
          gapMs,
          message: sameDay
            ? `Same day as ${other.label}`
            : `Under ${String(cadence.minGapHoursWarn)} hours from ${other.label} (${gapLabel(gapMs)})`,
        });
      }
    }
    return { date, time: chosen, utcMs, current, block, warnings };
  });
}

/** The option for one date (for a drop). */
export function dayOption(slot: PlannedSlot, context: MoveContext, date: string, time?: string): DayOption | undefined {
  return dayOptions(slot, context, time).find((option) => option.date === date);
}

/** A sentence for a blocked day: "Taken by Wed 2 Dec, 10:00", "Time has passed". */
export function blockMessage(block: DayBlock): string {
  return block.reason === 'past' ? 'That time has passed' : `Taken: a post goes out ${block.by.label}`;
}

// ------------------------------------------------------------------------------------------------
// Plan edits (PUT /api/plan/weeks/{weekStart})
// ------------------------------------------------------------------------------------------------

/**
 * Every slot as the edit route takes it, with only id, date and time: fields left out keep their
 * value on the server, and a slot left out of the list is removed, so an edit always sends all.
 */
export function slotInputs(plan: WeekPlan): PlanSlotIn[] {
  return sortedSlots(plan).map((slot) => ({ id: slot.id, localDate: slot.slot.localDate, localTime: slot.slot.localTime }));
}

export function withMovedSlot(plan: WeekPlan, slotId: string, date: string, time: string): PlanSlotIn[] {
  return slotInputs(plan).map((input) => (input.id === slotId ? { ...input, localDate: date, localTime: time } : input));
}

export interface SlotContent {
  readonly localDate: string;
  readonly localTime: string;
  readonly lane: Lane | null;
  readonly pillar: string | null;
  readonly format: PostFormat | null;
  readonly title: string | null;
}

/** The slot with new contents. A slot with a post sends only its time: the post sets the rest. */
export function withEditedSlot(plan: WeekPlan, slotId: string, content: SlotContent, hasPost: boolean): PlanSlotIn[] {
  return slotInputs(plan).map((input) => {
    if (input.id !== slotId) return input;
    const base = { id: input.id, localDate: content.localDate, localTime: content.localTime };
    if (hasPost) return { ...base, title: content.title };
    return { ...base, lane: content.lane, pillar: content.pillar, format: content.format, title: content.title };
  });
}

/** A slot id the edit route accepts (`^[a-z0-9-]+$`, at most 40 characters). */
export function newSlotId(random: () => number = Math.random): string {
  const part = () => Math.floor(random() * 36 ** 6).toString(36).padStart(6, '0');
  return `own-${part()}${part()}`;
}

export function withAddedSlot(plan: WeekPlan, id: string, content: SlotContent): PlanSlotIn[] {
  return [
    ...slotInputs(plan),
    {
      id,
      localDate: content.localDate,
      localTime: content.localTime,
      lane: content.lane,
      pillar: content.pillar,
      format: content.format,
      title: content.title,
    },
  ];
}

export function withoutSlot(plan: WeekPlan, slotId: string): PlanSlotIn[] {
  return slotInputs(plan).filter((input) => input.id !== slotId);
}

/**
 * The plan as it will look after an edit, for the screen to show at once (the server's answer
 * replaces it): slot times converted in the plan's zone, contents merged, slots in time order.
 */
export function previewPlan(plan: WeekPlan, inputs: readonly PlanSlotIn[], cadence: CadenceView): WeekPlan {
  const previous = new Map((plan.slots ?? []).map((slot) => [slot.id, slot]));
  const zone = plan.timezone;
  const slots: PlannedSlot[] = inputs.map((input) => {
    const old = previous.get(input.id);
    const utcMs = localToUtcMs(input.localDate, input.localTime, zone);
    const { date, time } = utcToLocal(utcMs, zone);
    const slot = {
      localDate: date,
      localTime: time,
      timezone: zone,
      utc: new Date(utcMs).toISOString(),
      kind: templateKind(cadence, date, time),
    };
    return {
      ...old,
      id: input.id,
      slot,
      postId: old?.postId ?? input.postId ?? null,
      lane: input.lane !== undefined ? input.lane : (old?.lane ?? null),
      pillar: input.pillar !== undefined ? input.pillar : (old?.pillar ?? null),
      format: input.format !== undefined ? input.format : (old?.format ?? null),
      title: input.title !== undefined ? input.title : (old?.title ?? null),
    };
  });
  slots.sort((a, b) => slotUtcMs(a) - slotUtcMs(b));
  return { ...plan, slots };
}

// ------------------------------------------------------------------------------------------------
// Pillar balance and findings
// ------------------------------------------------------------------------------------------------

export interface PillarBalance {
  readonly counts: ReadonlyMap<string, number>;
  /** Posts off every pillar (or with no pillar yet). */
  readonly other: number;
  readonly total: number;
  readonly onPillar: number;
  /** 0-1, or `null` for a week without posts. */
  readonly share: number | null;
}

/** Posts per pillar over the whole week, and the on-pillar share (`planner.plan.pillar_balance`). */
export function pillarBalance(slots: readonly PlannedSlot[], pillarIds: readonly string[]): PillarBalance {
  const counts = new Map<string, number>(pillarIds.map((id) => [id, 0]));
  let other = 0;
  for (const slot of slots) {
    const pillar = slot.pillar;
    const count = pillar ? counts.get(pillar) : undefined;
    if (pillar && count !== undefined) counts.set(pillar, count + 1);
    else other += 1;
  }
  const total = slots.length;
  const onPillar = total - other;
  return { counts, other, total, onPillar, share: total === 0 ? null : onPillar / total };
}

/** True when the week meets the on-pillar rule (at least `minShare` of its posts on a pillar). */
export function meetsOnPillarRule(balance: PillarBalance, minShare: number): boolean {
  return balance.share === null || balance.share >= minShare - 1e-9;
}

/** The slot a finding is about: its `ref` is a slot id, or (spacing) the later slot's UTC time. */
export function findingSlot(finding: Finding, slots: readonly PlannedSlot[]): PlannedSlot | undefined {
  const ref = finding.ref;
  if (!ref) return undefined;
  const byId = slots.find((slot) => slot.id === ref);
  if (byId) return byId;
  const refMs = parseTimestamp(ref);
  if (refMs === undefined) return undefined;
  return slots.find((slot) => slotUtcMs(slot) === refMs);
}

/** Findings that stop the approval (409 `slot_conflict`, `mix_rules`). */
export function blockingFindings(findings: readonly Finding[] | undefined): Finding[] {
  return (findings ?? []).filter((finding) => finding.severity === 'block');
}

export function advisoryFindings(findings: readonly Finding[] | undefined): Finding[] {
  return (findings ?? []).filter((finding) => finding.severity !== 'block');
}

/** The `findings` of a 409 `slot_conflict` or `mix_rules` problem (same shape as the plan's warnings). */
export function problemFindings(extensions: Readonly<Record<string, unknown>> | undefined): Finding[] {
  const raw = extensions?.findings;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry: unknown): Finding[] => {
    if (typeof entry !== 'object' || entry === null) return [];
    const record = entry as Record<string, unknown>;
    if (typeof record.code !== 'string' || typeof record.message !== 'string') return [];
    const severity = record.severity === 'warn' || record.severity === 'info' ? record.severity : 'block';
    return [
      {
        code: record.code,
        severity,
        message: record.message,
        ref: typeof record.ref === 'string' ? record.ref : null,
        overridable: false,
      },
    ];
  });
}

// ------------------------------------------------------------------------------------------------
// The flagship's week
// ------------------------------------------------------------------------------------------------

export type MilestoneId = 'research' | 'interview' | 'draft' | 'veto' | 'publish';

export interface Milestone {
  readonly id: MilestoneId;
  readonly label: string;
  readonly utcMs: number;
  readonly date: string;
  readonly time: string;
}

function weeklyIn(weekStart: string, weekly: WeeklyTime, zone: string, weeksLater = 0): number {
  const date = addDays(weekStart, 7 * weeksLater + WEEKDAY_IDS.indexOf(weekly.day));
  return localToUtcMs(date, weekly.time, zone);
}

/**
 * The first article publish time at least the veto lead after the draft day
 * (`planner.jobs._flagship_publish_at`, `domain.slots.next_article_publish`).
 */
export function expectedPublishMs(weekStart: string, cadence: CadenceView, zone: string): number {
  const draft = weeklyIn(weekStart, cadence.draftDay, zone);
  const earliest = draft + cadence.minVetoLeadHours * HOUR_MS;
  for (let weeks = 0; weeks < 3; weeks += 1) {
    const candidate = weeklyIn(weekStart, cadence.articlePublish, zone, weeks);
    if (candidate >= earliest) return candidate;
  }
  return weeklyIn(weekStart, cadence.articlePublish, zone, 2);
}

/** The flagship's week, from research to publication, in the plan's zone. */
export function flagshipMilestones(
  weekStart: string,
  cadence: CadenceView,
  zone: string,
  publishAt?: string | null,
): Milestone[] {
  const steps: [MilestoneId, string, number][] = [
    ['research', 'Research starts', weeklyIn(weekStart, cadence.researchStart, zone)],
    ['interview', 'Your interview answers are due', weeklyIn(weekStart, cadence.interviewDue, zone)],
    ['draft', 'Drafting, critique and revision', weeklyIn(weekStart, cadence.draftDay, zone)],
    ['veto', 'Veto notice, if it publishes on its own', weeklyIn(weekStart, cadence.vetoNotice, zone)],
    ['publish', 'Publishes', parseTimestamp(publishAt) ?? expectedPublishMs(weekStart, cadence, zone)],
  ];
  return steps.map(([id, label, utcMs]) => ({ id, label, utcMs, ...utcToLocal(utcMs, zone) }));
}

const STAGE: Record<ArticleStatus, number> = {
  planned: 0,
  researching: 1,
  interviewing: 2,
  drafting: 3,
  gating: 3,
  in_review: 4,
  approved: 4,
  publishing: 5,
  publish_failed: 5,
  published: 6,
  unpublishing: 6,
  unpublished: 6,
  needs_attention: -1,
  archived: -1,
};

const MILESTONE_STAGE: Record<MilestoneId, number> = { research: 1, interview: 2, draft: 3, veto: 4, publish: 5 };

export type MilestoneState = 'done' | 'current' | 'upcoming' | 'passed';

/**
 * A milestone's state: from the article's status once it exists (research is done when the
 * article is past researching), else from the clock (`passed` or `upcoming`).
 */
export function milestoneState(milestone: Milestone, status: ArticleStatus | undefined, nowMs: number): MilestoneState {
  const stage = status === undefined ? -1 : STAGE[status];
  if (stage < 0) return milestone.utcMs <= nowMs ? 'passed' : 'upcoming';
  const own = MILESTONE_STAGE[milestone.id];
  if (stage > own) return 'done';
  if (stage === own) return 'current';
  return 'upcoming';
}

// ------------------------------------------------------------------------------------------------
// When the planner proposes a week
// ------------------------------------------------------------------------------------------------

/**
 * When the scheduled plan job runs for the week starting `weekStart`: the cadence's plan time
 * (Sunday 18:00) of the week before, minus the lead (`sched-planner-plan-week`).
 */
export function planJobMs(weekStart: string, cadence: CadenceView, zone: string): number {
  const planTime = weeklyIn(addDays(weekStart, -7), cadence.planWeek, zone);
  return planTime - cadence.planLeadMinutes * 60_000;
}

/** "Sun 29 Nov, 17:00": a moment in the plan's zone. */
export function zoneMomentLabel(ms: number, zone: string): string {
  return momentLabel(ms, zone);
}

/** True when the week is over in `zone` (the generate route refuses it: `week_past`). */
export function isPastWeek(weekStart: string, nowMs: number, zone: string): boolean {
  return addDays(weekStart, 7) <= localDateOf(nowMs, zone);
}
