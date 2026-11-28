import type { StatusLabel } from '../lib/labels';
import { FORMAT_LABEL, LANE_LABEL, POST_STATUS } from '../lib/labels';
import {
  findingSlot,
  GONE_STATUSES,
  MOVABLE_STATUSES,
  slotTitle,
  slotUtcMs,
  sortedSlots,
  type Finding,
  type PlannedSlot,
  type PostSummary,
  type WeekPlan,
} from '../lib/plan';
import type { PillarView } from '../lib/settings';
import { slotLabel, utcToLocal, zoneAbbreviation } from '../lib/time';

/**
 * One planned slot as the screens show it: its post (when one fills it), its words, and what the
 * owner may do with it. Built once per render from the plan and the week's posts.
 */
export interface SlotModel {
  readonly slot: PlannedSlot;
  readonly post: PostSummary | undefined;
  readonly title: string;
  readonly date: string;
  readonly time: string;
  /** "Tue 1 Dec, 10:00". */
  readonly label: string;
  /** "EST", "EDT". */
  readonly zoneName: string;
  readonly state: StatusLabel;
  readonly busy: boolean;
  /** Lane and format in words: "Native carousel". */
  readonly kindLabel: string;
  readonly movable: boolean;
  /** Removing is for slots no live post fills. */
  readonly removable: boolean;
  /** Changing what the slot holds is for slots without a post. */
  readonly contentEditable: boolean;
  /** A quick-take slot still waiting for the owner's take (again, when its take is gone). */
  readonly needsTake: boolean;
  readonly findings: readonly Finding[];
}

/** The post page for a post: its composer while it is a draft, its go-live page after approval. */
export function postHref(post: Pick<PostSummary, 'id' | 'lane' | 'articleId' | 'status'>): string {
  const piece = post.lane === 'derivative' && post.articleId ? post.articleId : post.id;
  const live = ['approved', 'staged', 'publishing', 'kit', 'needs_attention', 'missed', 'posted'].includes(post.status);
  return live ? `/studio/${piece}/go-live?post=${post.id}` : `/studio/${piece}/posts${piece === post.id ? '' : `?post=${post.id}`}`;
}

export function articleHref(articleId: string): string {
  return `/studio/${articleId}`;
}

/** The ideas inbox, ready to take the owner's take for a quick-take slot. */
export function takeHref(slot: PlannedSlot, weekStart: string): string {
  const params = new URLSearchParams({ take: slot.id, week: weekStart });
  const ideaId = slot.ideaId ?? undefined;
  if (ideaId) params.set('idea', ideaId);
  return `/calendar/ideas?${params.toString()}`;
}

/**
 * The post that fills a slot: the one the slot names (`postId`), else a live one that names the
 * slot (`weekPlanId` and `planSlotId`: a quick take written for it). A post that is gone
 * (archived, expired, deleted) fills nothing: nothing clears the slot's `postId`, so the slot is
 * open again and a new take replaces it (`domain.golive.slot_post_gone`). Never a post that only
 * shares the slot's day.
 */
export function slotPost(slot: PlannedSlot, planId: string, posts: readonly PostSummary[]): PostSummary | undefined {
  if (slot.postId) {
    const named = posts.find((post) => post.id === slot.postId);
    if (named === undefined || !GONE_STATUSES.has(named.status)) return named;
  }
  return posts.find(
    (post) => post.weekPlanId === planId && post.planSlotId === slot.id && !GONE_STATUSES.has(post.status),
  );
}

function stateOf(slot: PlannedSlot, post: PostSummary | undefined, plan: WeekPlan): StatusLabel {
  if (post) return POST_STATUS[post.status];
  if (plan.status === 'proposed' || plan.status === 'generating') return { label: 'Planned', tone: 'neutral' };
  switch (slot.lane) {
    case 'quick_take':
      return { label: 'Needs your take', tone: 'warn' };
    case 'derivative':
      return { label: 'Waits for the flagship', tone: 'neutral' };
    case 'native':
    case 'repurpose':
    case null:
    case undefined:
      return { label: 'Not started', tone: 'neutral' };
  }
}

/** "Native carousel", "Quick take", "Derivative text post". */
export function kindLabel(slot: Pick<PlannedSlot, 'lane' | 'format'>): string {
  const lane = slot.lane ? LANE_LABEL[slot.lane] : 'Open lane';
  if (!slot.format) return lane;
  return `${lane} ${FORMAT_LABEL[slot.format].toLowerCase()}`;
}

export function slotModels(
  plan: WeekPlan,
  posts: readonly PostSummary[],
  nowMs: number,
): SlotModel[] {
  const zone = plan.timezone;
  const editable = plan.status === 'proposed' || plan.status === 'approved';
  const slots = sortedSlots(plan);
  return slots.map((slot) => {
    const post = slotPost(slot, plan.id, posts);
    const utcMs = slotUtcMs(slot);
    const { date, time } = utcToLocal(utcMs, zone);
    // The post the plan holds (`postId`), live or gone: what the slot's moves and removal follow.
    const linked = slot.postId ? posts.find((candidate) => candidate.id === slot.postId) : undefined;
    const live = linked !== undefined && !GONE_STATUSES.has(linked.status);
    return {
      slot,
      post,
      title: slotTitle(slot, post),
      date,
      time,
      label: slotLabel(date, time),
      zoneName: zoneAbbreviation(utcMs, zone),
      state: stateOf(slot, post, plan),
      busy: post?.status === 'generating',
      kindLabel: kindLabel(slot),
      // A slot moves with its post only when the plan holds that post (`postId`): a post that names
      // the slot but is not on it yet stays where it is, so the slot does too. A slot whose post is
      // gone stays put until a new take fills it.
      movable: editable && (linked !== undefined ? MOVABLE_STATUSES.has(linked.status) : post === undefined),
      removable: editable && !live,
      contentEditable: editable && !slot.postId,
      needsTake: plan.status === 'approved' && slot.lane === 'quick_take' && post === undefined && utcMs > nowMs,
      findings: (plan.warnings ?? []).filter((finding) => findingSlot(finding, slots)?.id === slot.id),
    };
  });
}

/** The plan as lines of text, for the conflict dialog's comparison. */
export function describePlan(plan: WeekPlan | undefined, pillars: readonly PillarView[]): string {
  if (!plan) return '';
  const pillarName = (id: string | null | undefined) =>
    id ? (pillars.find((pillar) => pillar.id === id)?.name ?? id) : 'no pillar';
  const lines: string[] = [];
  if (plan.flagship) lines.push(`Flagship: ${plan.flagship.title} (${pillarName(plan.flagship.pillar)})`);
  else lines.push('No flagship article');
  for (const slot of sortedSlots(plan)) {
    lines.push(
      `${slotLabel(slot.slot.localDate, slot.slot.localTime)}: ${kindLabel(slot)}, ${pillarName(slot.pillar)}. ${slotTitle(slot)}`,
    );
  }
  return lines.join('\n\n');
}
