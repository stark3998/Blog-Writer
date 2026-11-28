import {
  Archive,
  CalendarCheck,
  CalendarClock,
  CalendarX,
  CircleAlert,
  CircleCheck,
  CircleX,
  ClipboardList,
  Clock,
  CloudOff,
  Eye,
  EyeOff,
  FileText,
  Globe,
  Hourglass,
  ListChecks,
  MessageCircleQuestion,
  PenLine,
  Search,
  Send,
  Trash2,
  type LucideIcon,
} from 'lucide-react';

import type { StudioTab } from '@/app/nav';
import { PROBLEM_MESSAGES } from '@/lib/api/problems';
import { ownerTakes, writerIn } from '@/lib/query/machines';

import { formatDateTime, formatSlot } from './format';
import type { ArticleStatus, Lane, Machines, MachineName, PostFormat, PostStatus, Slot } from './types';

/**
 * What a piece's status means to the owner: the glossary label (contract section 10), a tone and
 * icon (never colour alone), the list group it belongs to, and the next step. Pure, so the list,
 * the header and the other Studio tabs agree.
 */

/** How a status reads: `attention` and `danger` need the owner; `progress` means a job works on it. */
export type StatusTone = 'neutral' | 'progress' | 'attention' | 'success' | 'danger' | 'muted';

/** The Studio list's groups, in the order they are shown. */
export type StageGroup = 'needs-you' | 'working' | 'planned' | 'scheduled' | 'live' | 'closed';

export interface StatusMeta {
  readonly label: string;
  readonly tone: StatusTone;
  readonly icon: LucideIcon;
  readonly group: StageGroup;
}

export const ARTICLE_STATUS: Readonly<Record<ArticleStatus, StatusMeta>> = {
  planned: { label: 'Planned', tone: 'neutral', icon: CalendarClock, group: 'planned' },
  researching: { label: 'Researching', tone: 'progress', icon: Search, group: 'working' },
  interviewing: { label: 'Interview', tone: 'attention', icon: MessageCircleQuestion, group: 'needs-you' },
  drafting: { label: 'Drafting', tone: 'progress', icon: PenLine, group: 'working' },
  gating: { label: 'Checking', tone: 'progress', icon: ListChecks, group: 'working' },
  in_review: { label: 'In review', tone: 'attention', icon: Eye, group: 'needs-you' },
  approved: { label: 'Approved', tone: 'success', icon: CalendarCheck, group: 'scheduled' },
  publishing: { label: 'Publishing', tone: 'progress', icon: Send, group: 'working' },
  published: { label: 'Live', tone: 'success', icon: Globe, group: 'live' },
  publish_failed: { label: 'Publish failed', tone: 'danger', icon: CircleX, group: 'needs-you' },
  unpublishing: { label: 'Unpublishing', tone: 'progress', icon: CloudOff, group: 'working' },
  unpublished: { label: 'Unpublished', tone: 'muted', icon: EyeOff, group: 'closed' },
  needs_attention: { label: 'Needs you', tone: 'danger', icon: CircleAlert, group: 'needs-you' },
  archived: { label: 'Archived', tone: 'muted', icon: Archive, group: 'closed' },
};

export const POST_STATUS: Readonly<Record<PostStatus, StatusMeta>> = {
  generating: { label: 'Writing', tone: 'progress', icon: PenLine, group: 'working' },
  draft: { label: 'Draft', tone: 'attention', icon: FileText, group: 'needs-you' },
  approved: { label: 'Approved', tone: 'success', icon: CalendarCheck, group: 'scheduled' },
  staged: { label: 'Ready', tone: 'success', icon: Clock, group: 'scheduled' },
  publishing: { label: 'Publishing', tone: 'progress', icon: Send, group: 'working' },
  posted: { label: 'Posted', tone: 'success', icon: CircleCheck, group: 'live' },
  kit: { label: 'Kit', tone: 'attention', icon: ClipboardList, group: 'needs-you' },
  needs_attention: { label: 'Needs you', tone: 'danger', icon: CircleAlert, group: 'needs-you' },
  missed: { label: 'Missed', tone: 'danger', icon: CalendarX, group: 'needs-you' },
  expired: { label: 'Expired', tone: 'muted', icon: Hourglass, group: 'closed' },
  deleted: { label: 'Deleted', tone: 'muted', icon: Trash2, group: 'closed' },
  archived: { label: 'Archived', tone: 'muted', icon: Archive, group: 'closed' },
};

export const STAGE_GROUPS: readonly { readonly id: StageGroup; readonly label: string; readonly description: string }[] =
  [
    { id: 'needs-you', label: 'Needs you', description: 'Waiting for your answers, a review or a decision.' },
    { id: 'working', label: 'Working on it', description: 'Research, writing, checks or publishing are running.' },
    { id: 'planned', label: 'Planned', description: 'Not started yet.' },
    { id: 'scheduled', label: 'Scheduled', description: 'Approved and waiting for its time.' },
    { id: 'live', label: 'Live', description: 'Published on your site or posted on LinkedIn.' },
    { id: 'closed', label: 'Closed', description: 'Archived, unpublished, expired or deleted.' },
  ];

export const ARTICLE_STATUSES = Object.keys(ARTICLE_STATUS) as ArticleStatus[];
export const POST_STATUSES = Object.keys(POST_STATUS) as PostStatus[];

/** Statuses in which a job works on the piece: screens that show one keep the event stream open. */
export function isWorkingStatus(status: ArticleStatus | PostStatus, kind: 'article' | 'post'): boolean {
  const meta = kind === 'article' ? ARTICLE_STATUS[status as ArticleStatus] : POST_STATUS[status as PostStatus];
  return meta.tone === 'progress';
}

export const POST_FORMAT_LABEL: Readonly<Record<PostFormat, string>> = {
  text: 'Text post',
  carousel: 'Carousel',
  image_card: 'Image card',
  poll: 'Poll',
};

export const LANE_LABEL: Readonly<Record<Lane, string>> = {
  derivative: 'From an article',
  native: 'Native post',
  quick_take: 'Quick take',
  repurpose: 'Repurposed',
};

/** Who writes an article's Markdown and meta in a status (`writers` of `GET /api/meta/machines`). */
export type ArticleWriter = 'owner' | 'pipeline' | 'publish' | 'nobody';

/**
 * Who writes the article in `status`, as the server says (`writers`: its `ARTICLE_WRITER`; `none`
 * reads `nobody`). `undefined` while the machines are unknown: the editor stays read-only then.
 */
export function articleWriter(machines: Machines | undefined, status: ArticleStatus): ArticleWriter | undefined {
  switch (writerIn(machines, 'article', status)) {
    case 'owner':
      return 'owner';
    case 'pipeline':
      return 'pipeline';
    case 'publish':
      return 'publish';
    case 'none':
      return 'nobody';
    case undefined:
    default:
      return undefined;
  }
}

/**
 * True when `machine` has the edge `from -> to` in `GET /api/meta/machines`. While the machines
 * are unknown the answer is `fallback`: false (buttons wait for the server) unless they could not
 * be loaded at all, when the caller passes true and the route itself is the judge.
 */
export function machineAllows(
  machines: Machines | undefined,
  machine: MachineName,
  from: string,
  to: string,
  fallback = false,
): boolean {
  if (!machines) return fallback;
  return machines[machine]?.edges[from]?.includes(to) ?? false;
}

/**
 * True when the owner moves an article `from -> to` (an edge of the server's `ownerEdges`: the
 * transition route, the pipeline's start and stop, approve, unpublish). Same `fallback` rule as
 * `machineAllows`.
 */
export function ownerCanMoveArticle(
  machines: Machines | undefined,
  from: ArticleStatus,
  to: ArticleStatus,
  fallback = false,
): boolean {
  if (!machines) return fallback;
  return ownerTakes(machines, 'article', from, to) ?? false;
}

// ------------------------------------------------------------------------------------------------
// Errors a piece can carry (`lastError`)
// ------------------------------------------------------------------------------------------------

const LAST_ERROR: Readonly<Record<string, string>> = {
  research_failed: 'Research didn’t produce a brief.',
  research_unavailable: 'Research isn’t available right now.',
  cancelled: 'The pipeline was cancelled.',
  refused: 'The model refused to write the draft.',
  empty_draft: 'The model returned an empty draft.',
  draft_missing: 'There is no draft to work on yet.',
  outline_failed: 'The outline couldn’t be written.',
  outline_missing: 'The outline went missing before drafting.',
  interview_missing: 'The interview went missing before drafting.',
  brief_missing: 'There is no research brief: start from research.',
  blocklist_hit: 'Something matched your blocklist, so the work stopped.',
  policy_violation: 'A model call broke the AI-use policy, so the work stopped.',
  invalid_input: 'The job got input it couldn’t use.',
  publish_late: 'The scheduled publish time passed while the app was down. Approve it again.',
  build_failed: 'The portfolio build failed.',
  enqueue_failed: 'The publish job couldn’t be queued.',
};

/** One owner-facing sentence for a piece's `lastError` code. */
export function lastErrorSentence(code: string | null | undefined): string | undefined {
  if (!code) return undefined;
  return LAST_ERROR[code] ?? PROBLEM_MESSAGES[code] ?? 'Something stopped the work on it.';
}

// ------------------------------------------------------------------------------------------------
// The next step
// ------------------------------------------------------------------------------------------------

export type StudioTabId = StudioTab['id'];

/** What the next-step button does. */
export type NextActionTarget =
  | { readonly type: 'tab'; readonly tab: StudioTabId; readonly label: string; readonly search?: string }
  | { readonly type: 'start-research'; readonly label: string }
  | { readonly type: 'restore'; readonly label: string }
  | { readonly type: 'external'; readonly href: string; readonly label: string };

export interface NextAction {
  /** One or two sentences: what happens now, or what the owner does next. */
  readonly sentence: string;
  /** True when the owner is the one who has to act. */
  readonly needsOwner: boolean;
  readonly primary?: NextActionTarget;
  readonly secondary?: NextActionTarget;
}

/** The facts of an article the next step depends on (from the document or its list summary). */
export interface ArticleFacts {
  readonly kind: 'article';
  readonly id: string;
  readonly status: ArticleStatus;
  readonly publishAt?: string | null;
  readonly autoPublish?: boolean;
  readonly liveUrl?: string | null;
  readonly lastError?: string | null;
  /** When a plan-approved research start is queued for later (`scheduledFor` of the pipeline job). */
  readonly researchStartsAt?: string | null;
}

export interface PostFacts {
  readonly kind: 'post';
  readonly id: string;
  readonly status: PostStatus;
  readonly slot?: Pick<Slot, 'utc' | 'timezone'> | null;
  readonly postedAt?: string | null;
  readonly postUrl?: string | null;
  readonly lastError?: string | null;
}

export type PieceFacts = ArticleFacts | PostFacts;

function isHttps(url: string | null | undefined): url is string {
  return typeof url === 'string' && url.startsWith('https://');
}

function articleNext(facts: ArticleFacts): NextAction {
  switch (facts.status) {
    case 'planned': {
      const startsAt = formatDateTime(facts.researchStartsAt);
      return startsAt
        ? {
            sentence: `Research starts ${startsAt}, as the weekly plan set it. You can start it now instead.`,
            needsOwner: false,
            primary: { type: 'start-research', label: 'Start research now' },
          }
        : {
            sentence: 'Start research when you’re ready. The interview and the draft follow on their own.',
            needsOwner: true,
            primary: { type: 'start-research', label: 'Start research' },
          };
    }
    case 'researching':
      return {
        sentence: 'Research is running. The interview questions come next.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'brief', label: 'Follow the research' },
      };
    case 'interviewing':
      return {
        sentence: 'Answer the interview. Drafting starts after your answers, or when the interview expires.',
        needsOwner: true,
        primary: { type: 'tab', tab: 'interview', label: 'Answer the interview' },
      };
    case 'drafting':
      return {
        sentence: 'The draft is being written from the brief and your answers.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'draft', label: 'Watch the draft' },
      };
    case 'gating':
      return {
        sentence: 'The draft is going through the seven checks.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'draft', label: 'See the checks' },
      };
    case 'in_review':
      return {
        sentence: 'The draft is ready for your review.',
        needsOwner: true,
        primary: { type: 'tab', tab: 'draft', label: 'Review the draft' },
      };
    case 'approved': {
      const at = formatDateTime(facts.publishAt);
      const when = at ? ` It publishes ${at}` : ' It publishes at its slot';
      return {
        sentence: facts.autoPublish
          ? `Approved by the checks.${when} unless you veto it.`
          : `Approved.${when}.`,
        needsOwner: false,
        primary: { type: 'tab', tab: 'draft', label: 'See the schedule' },
      };
    }
    case 'publishing':
      return {
        sentence: 'Publishing to your site now.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'draft', label: 'Follow publishing' },
      };
    case 'published':
      return {
        sentence: 'Live on your site. Its LinkedIn posts come next.',
        needsOwner: true,
        primary: { type: 'tab', tab: 'posts', label: 'Write the posts' },
        ...(isHttps(facts.liveUrl)
          ? { secondary: { type: 'external', href: facts.liveUrl, label: 'Open the live page' } as const }
          : {}),
      };
    case 'publish_failed':
      return {
        sentence: `Publishing failed. ${lastErrorSentence(facts.lastError) ?? 'Open the draft to see why.'}`,
        needsOwner: true,
        primary: { type: 'tab', tab: 'draft', label: 'See what failed' },
      };
    case 'unpublishing':
      return {
        sentence: 'Taking the page down from your site.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'draft', label: 'Follow it' },
      };
    case 'unpublished':
      return {
        sentence: 'Unpublished: the page is down. You can correct it and publish again.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'draft', label: 'Open the draft' },
      };
    case 'needs_attention': {
      const reason = lastErrorSentence(facts.lastError) ?? 'Something stopped the work on it.';
      const research = facts.lastError === 'research_failed' || facts.lastError === 'research_unavailable';
      return {
        sentence: `It needs you. ${reason}`,
        needsOwner: true,
        primary: research
          ? { type: 'tab', tab: 'brief', label: 'See the research' }
          : { type: 'tab', tab: 'draft', label: 'See what’s wrong' },
      };
    }
    case 'archived':
      return {
        sentence: 'Archived. Restore it to plan it again.',
        needsOwner: false,
        primary: { type: 'restore', label: 'Restore' },
      };
  }
}

function postNext(facts: PostFacts): NextAction {
  const goLive = (label: string): NextActionTarget => ({
    type: 'tab',
    tab: 'go-live',
    label,
    search: `?post=${encodeURIComponent(facts.id)}`,
  });
  const slot = facts.slot ? formatSlot(facts.slot) : undefined;
  switch (facts.status) {
    case 'generating':
      return {
        sentence: 'The post is being written.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'posts', label: 'Follow the writing' },
      };
    case 'draft':
      return {
        sentence: 'The post is ready for your review.',
        needsOwner: true,
        primary: { type: 'tab', tab: 'posts', label: 'Review the post' },
      };
    case 'approved':
      return {
        sentence: slot ? `Approved for ${slot}.` : 'Approved. Pick a slot on the go-live page.',
        needsOwner: !slot,
        primary: goLive('Open go-live'),
      };
    case 'staged':
      return {
        sentence: slot ? `Ready to post at ${slot}.` : 'Ready to post.',
        needsOwner: true,
        primary: goLive('Open go-live'),
      };
    case 'publishing':
      return { sentence: 'Posting to LinkedIn now.', needsOwner: false, primary: goLive('Follow it') };
    case 'posted': {
      const at = formatDateTime(facts.postedAt);
      return {
        sentence: at ? `Posted ${at}.` : 'Posted.',
        needsOwner: false,
        ...(isHttps(facts.postUrl)
          ? { primary: { type: 'external', href: facts.postUrl, label: 'Open on LinkedIn' } as const }
          : {}),
      };
    }
    case 'kit':
      return {
        sentence: 'Post it yourself with the ready-to-post kit, then mark it posted.',
        needsOwner: true,
        primary: goLive('Open the kit'),
      };
    case 'needs_attention':
      return {
        sentence: `It needs you. ${lastErrorSentence(facts.lastError) ?? 'Posting didn’t finish cleanly.'}`,
        needsOwner: true,
        primary: goLive('Resolve it'),
      };
    case 'missed':
      return { sentence: 'It missed its slot.', needsOwner: true, primary: goLive('Pick a new slot') };
    case 'expired':
      return {
        sentence: 'It expired: the news it reacts to is too old now.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'posts', label: 'Open the post' },
      };
    case 'deleted':
      return { sentence: 'Deleted from LinkedIn.', needsOwner: false };
    case 'archived':
      return {
        sentence: 'Archived.',
        needsOwner: false,
        primary: { type: 'tab', tab: 'posts', label: 'Open the post' },
      };
  }
}

/** The piece's next step: what happens now and the one action that moves it on. */
export function nextActionFor(facts: PieceFacts): NextAction {
  return facts.kind === 'article' ? articleNext(facts) : postNext(facts);
}

/**
 * Where a piece opens from the list: the tab of its next step (`/studio/<id>/<tab>`), or its first
 * tab when the next step is not a tab.
 */
export function piecePath(facts: PieceFacts): string {
  const base = `/studio/${encodeURIComponent(facts.id)}`;
  const target = nextActionFor(facts).primary;
  if (target?.type !== 'tab') return `${base}/brief`;
  return `${base}/${target.tab}${target.search ?? ''}`;
}

/** The status meta of a piece. */
export function statusMeta(facts: Pick<PieceFacts, 'kind' | 'status'>): StatusMeta {
  return facts.kind === 'article'
    ? ARTICLE_STATUS[facts.status as ArticleStatus]
    : POST_STATUS[facts.status as PostStatus];
}

/** A readable form of an id-like code: "deep-explainer" -> "Deep explainer". */
export function humanize(code: string): string {
  const words = code.replace(/[._-]+/g, ' ').trim();
  return words === '' ? code : words.charAt(0).toUpperCase() + words.slice(1);
}

/** The first line of a post's text, as its title in lists and headers. */
export function firstLine(text: string | null | undefined, max = 140): string | undefined {
  const line = text
    ?.split('\n')
    .map((part) => part.trim())
    .find((part) => part !== '');
  if (!line) return undefined;
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
}
