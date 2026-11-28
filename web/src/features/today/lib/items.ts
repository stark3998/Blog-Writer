import {
  CalendarClock,
  CalendarX2,
  ChartNoAxesColumn,
  ClipboardCheck,
  DatabaseBackup,
  FilePen,
  Hourglass,
  Lightbulb,
  ListChecks,
  MessageSquareReply,
  Mic,
  Send,
  Stamp,
  TriangleAlert,
  Unplug,
  type LucideIcon,
} from 'lucide-react';
import { parsePath } from 'react-router';

import type { components } from '@/lib/api/schema';

/**
 * What each kind of Today item means to the owner (contract W-B4, `TodayItem.kind`): the word
 * that names it on its card, the one action its button takes, and the group it counts in for the
 * page's lead sentence. Titles, links and order come from the server (`GET /api/today`, sorted by
 * priority, then due time).
 */

export type Today = components['schemas']['Today'];
export type TodayItem = components['schemas']['TodayItem'];
export type TodayKind = components['schemas']['TodayKind'];
export type TodayWeek = components['schemas']['TodayWeek'];
export type Priority = components['schemas']['Priority'];
export type Scorecard = components['schemas']['Scorecard'];

/** The groups of the lead sentence ("1 post to publish, 2 to review and approve"). */
export type TodayGroup = 'publish' | 'approve' | 'answer' | 'take' | 'reply' | 'numbers' | 'attention' | 'setup';

export interface KindMeta {
  /** The word on the card: "Publish", "Answer", "Needs you". */
  readonly label: string;
  /** The card's one action, as its button says it. */
  readonly action: string;
  readonly icon: LucideIcon;
  readonly group: TodayGroup;
}

export const KIND_META: Readonly<Record<TodayKind, KindMeta>> = {
  publish_post: { label: 'Publish', action: 'Go live', icon: Send, group: 'publish' },
  approve_plan: { label: 'Approve', action: 'Review the plan', icon: Stamp, group: 'approve' },
  approve_post: { label: 'Approve', action: 'Review the post', icon: Stamp, group: 'approve' },
  review_article: { label: 'Review', action: 'Review the draft', icon: FilePen, group: 'approve' },
  veto_window: { label: 'Auto-publish', action: 'Review or veto', icon: Hourglass, group: 'approve' },
  answer_interview: { label: 'Answer', action: 'Answer', icon: Mic, group: 'answer' },
  take_needed: { label: 'Your take', action: 'Add your take', icon: Lightbulb, group: 'take' },
  reply: { label: 'Reply', action: 'Reply', icon: MessageSquareReply, group: 'reply' },
  snapshot: { label: 'Numbers', action: 'Log the numbers', icon: ChartNoAxesColumn, group: 'numbers' },
  kit_confirm: { label: 'Confirm', action: 'Confirm it went out', icon: ClipboardCheck, group: 'attention' },
  schedule_kit: { label: 'Away week', action: 'Schedule them in LinkedIn', icon: CalendarClock, group: 'attention' },
  missed_slot: { label: 'Missed slot', action: 'Pick a new time', icon: CalendarX2, group: 'attention' },
  needs_attention: { label: 'Needs you', action: 'Sort it out', icon: TriangleAlert, group: 'attention' },
  schedule_confirm: { label: 'Confirm', action: 'Confirm the run', icon: CalendarClock, group: 'attention' },
  reconnect_linkedin: { label: 'LinkedIn', action: 'Reconnect LinkedIn', icon: Unplug, group: 'setup' },
  owner_input: { label: 'Set up', action: 'Set it up', icon: ListChecks, group: 'setup' },
  restore_drill: { label: 'Upkeep', action: 'Run the drill', icon: DatabaseBackup, group: 'setup' },
};

/** Meta for a kind, with a safe default for one a newer server sends. */
export function kindMeta(kind: string): KindMeta {
  return (KIND_META as Readonly<Record<string, KindMeta | undefined>>)[kind] ?? {
    label: 'To do',
    action: 'Open',
    icon: ListChecks,
    group: 'attention',
  };
}

/** Items shown as notices above the inbox rather than as cards. */
export function isNoticeKind(kind: string): boolean {
  return kind === 'reconnect_linkedin';
}

/** Low-effort setup and upkeep items: a quiet list after the inbox. */
export function isSetupKind(kind: string): boolean {
  return kind === 'owner_input' || kind === 'restore_drill';
}

export interface InboxParts {
  /** The cards, in the server's order (priority, then due time). */
  readonly cards: readonly TodayItem[];
  /** Owner inputs and the restore drill. */
  readonly setup: readonly TodayItem[];
  /** LinkedIn reconnect banners. */
  readonly notices: readonly TodayItem[];
}

/** Splits the server's list into cards, setup items and notices, keeping its order. */
export function splitInbox(items: readonly TodayItem[]): InboxParts {
  const cards: TodayItem[] = [];
  const setup: TodayItem[] = [];
  const notices: TodayItem[] = [];
  for (const item of items) {
    if (isNoticeKind(item.kind)) notices.push(item);
    else if (isSetupKind(item.kind)) setup.push(item);
    else cards.push(item);
  }
  return { cards, setup, notices };
}

const NUMBER_WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];

/** "Three things need you." for the page's lead sentence. */
export function headline(count: number): string {
  if (count === 0) return 'Nothing needs you right now.';
  const number = NUMBER_WORDS[count] ?? String(count);
  return count === 1 ? 'One thing needs you.' : `${number} things need you.`;
}

const PHRASES: Readonly<Record<Exclude<TodayGroup, 'setup'>, (count: number) => string>> = {
  publish: (n) => (n === 1 ? '1 post to publish' : `${n} posts to publish`),
  approve: (n) => `${n} to review and approve`,
  answer: (n) => (n === 1 ? '1 interview to answer' : `${n} interviews to answer`),
  take: (n) => (n === 1 ? '1 take to add' : `${n} takes to add`),
  reply: (n) => (n === 1 ? '1 reply to write' : `${n} replies to write`),
  numbers: (n) => (n === 1 ? '1 set of numbers to log' : `${n} sets of numbers to log`),
  attention: (n) => `${n} to sort out`,
};

const GROUP_ORDER: readonly Exclude<TodayGroup, 'setup'>[] = [
  'publish',
  'approve',
  'answer',
  'take',
  'reply',
  'numbers',
  'attention',
];

/** "A, B and C." */
export function joinWords(parts: readonly string[]): string {
  if (parts.length <= 1) return parts[0] ?? '';
  return `${parts.slice(0, -1).join(', ')} and ${parts.at(-1) ?? ''}`;
}

/** "1 post to publish, 2 to review and approve and 1 interview to answer." (empty for no cards). */
export function groupSummary(cards: readonly TodayItem[]): string {
  const counts = new Map<TodayGroup, number>();
  for (const item of cards) {
    const { group } = kindMeta(item.kind);
    counts.set(group, (counts.get(group) ?? 0) + 1);
  }
  const parts = GROUP_ORDER.flatMap((group) => {
    const count = counts.get(group) ?? 0;
    return count > 0 ? [PHRASES[group](count)] : [];
  });
  if (parts.length === 0) return '';
  const sentence = joinWords(parts);
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`;
}

/** Where an item opens: Today itself (a sheet on this page) or another page. */
export type ItemTarget =
  | { readonly kind: 'engage'; readonly postId: string | null }
  | { readonly kind: 'snapshot'; readonly postId: string }
  | { readonly kind: 'page'; readonly href: string };

/** Same-origin paths only: anything else (a full URL, `//host`, `javascript:`) opens Today. */
export function safeHref(href: string): string {
  return href.startsWith('/') && !href.startsWith('//') && !href.includes('\\') ? href : '/today';
}

/**
 * Reminders link back into Today (`/today?engage=<postId>`, `/today?snapshot=<postId>`): those open
 * the Engage panel or the snapshot form in place; every other link opens its page.
 */
export function itemTarget(item: Pick<TodayItem, 'href'>): ItemTarget {
  const href = safeHref(item.href);
  const { pathname, search } = parsePath(href);
  if (pathname !== '/today') return { kind: 'page', href };
  const params = new URLSearchParams(search);
  const snapshot = params.get('snapshot');
  if (snapshot) return { kind: 'snapshot', postId: snapshot };
  const engage = params.get('engage');
  if (engage !== null) return { kind: 'engage', postId: engage === '' ? null : engage };
  return { kind: 'page', href };
}

/** "Urgent" for the server's top priority; the order already says the rest. */
export function priorityWord(priority: Priority | undefined): string | undefined {
  return priority === 'urgent' ? 'Urgent' : undefined;
}
