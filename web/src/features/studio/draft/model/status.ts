import { ownerTakes, writerIn, type MachineViews } from '@/lib/query/machines';

import type { Article, ArticleStatus } from './types';

/**
 * The article's rules as the Draft tab uses them, all from `GET /api/meta/machines` (`article`):
 * its edges, the edges the owner takes (`ownerEdges`) and who writes in each status (`writers`,
 * the server's `ARTICLE_WRITER`). Nothing here mirrors the server's tables: while the machines are
 * unknown (loading, or a failed read) the editor stays read-only and no status action is offered,
 * and the server refuses anything else anyway.
 */

/** Who may change the Markdown and the details in a status (`writers`). */
export type Writer = 'owner' | 'pipeline' | 'publish' | 'none';

const WRITERS: ReadonlySet<string> = new Set<Writer>(['owner', 'pipeline', 'publish', 'none']);

function isWriter(value: string | undefined): value is Writer {
  return value !== undefined && WRITERS.has(value);
}

/** Who writes the article in `status`; `undefined` while the machines are unknown. */
export function writerOf(status: ArticleStatus, machines: MachineViews | undefined): Writer | undefined {
  const writer = writerIn(machines, 'article', status);
  return isWriter(writer) ? writer : undefined;
}

/** Status labels from the contract's glossary (section 10). */
export const STATUS_LABELS: Readonly<Record<ArticleStatus, string>> = {
  planned: 'Planned',
  researching: 'Researching',
  interviewing: 'Interview',
  drafting: 'Drafting',
  gating: 'Checking',
  in_review: 'In review',
  approved: 'Approved',
  publishing: 'Publishing',
  published: 'Live',
  publish_failed: 'Publish failed',
  unpublishing: 'Unpublishing',
  unpublished: 'Unpublished',
  needs_attention: 'Needs you',
  archived: 'Archived',
};

/**
 * Why the text cannot be edited right now, in the owner's words; `undefined` when it can (the
 * owner writes in this status) or while that is not known yet.
 */
export function readOnlyReason(status: ArticleStatus, writer: Writer | undefined): string | undefined {
  if (writer === undefined || writer === 'owner') return undefined;
  switch (status) {
    case 'planned':
    case 'in_review':
    case 'needs_attention':
      return 'Read-only in this status.';
    case 'researching':
    case 'interviewing':
    case 'drafting':
    case 'gating':
      return 'Read-only while the pipeline works on it. It comes back to you for review when the run ends.';
    case 'publishing':
    case 'unpublishing':
      return 'Read-only while publishing runs: the text being published can’t change.';
    case 'approved':
      return 'Read-only while approved. Pull it back to review to change the text.';
    case 'published':
      return 'Read-only while live. Start a correction to change the text; the live page stays up meanwhile.';
    case 'publish_failed':
      return 'Read-only after a failed publishing run. Retry it, or move it back to review to change the text.';
    case 'unpublished':
      return 'Read-only while unpublished. Move it back to review to change the text.';
    case 'archived':
      return 'Read-only while archived. Restore it to plan it again.';
  }
}

/** True when the article machine has the edge `from -> to` (false while the machines are unknown). */
export function hasEdge(machines: MachineViews | undefined, from: ArticleStatus, to: ArticleStatus): boolean {
  return machines?.article?.edges[from]?.includes(to) ?? false;
}

/** True when the owner takes `from -> to` (`ownerEdges`; false while the machines are unknown). */
export function ownerMove(machines: MachineViews | undefined, from: ArticleStatus, to: ArticleStatus): boolean {
  return ownerTakes(machines, 'article', from, to) ?? false;
}

/** What the owner can do with the article now; each flag is a button. */
export interface ArticleActions {
  /** `POST .../pipeline` from planned, in review or needs you. */
  readonly startPipeline: boolean;
  /** `POST .../pipeline/cancel` while the pipeline runs. */
  readonly cancelPipeline: boolean;
  /** `POST .../revise`: in review or needs you, with a draft to revise. */
  readonly revise: boolean;
  /** `POST .../approve`: in review -> approved. */
  readonly approve: boolean;
  /** `POST .../publish-now`: in review, approved, or a failed publish to retry. */
  readonly publishNow: boolean;
  /** `POST .../unpublish`: live, or a failed unpublish to retry. */
  readonly unpublish: boolean;
  /** approved -> in review (the scheduled publish is cancelled). */
  readonly pullBack: boolean;
  /** published -> in review (a correction; the live page stays up). */
  readonly correct: boolean;
  /** needs you, unpublished or a failed publish -> in review. */
  readonly backToReview: boolean;
  /** -> archived (refused while live). */
  readonly archive: boolean;
  /** archived -> planned. */
  readonly unarchive: boolean;
  /** `POST .../veto`: an auto-publish approval. */
  readonly veto: boolean;
  /** `POST .../gates/run`: not while the pipeline or publishing owns the article. */
  readonly runGates: boolean;
  /** Restore a version, attest clearance: owner states only. */
  readonly ownerEdits: boolean;
}

export const NO_ACTIONS: ArticleActions = {
  startPipeline: false,
  cancelPipeline: false,
  revise: false,
  approve: false,
  publishNow: false,
  unpublish: false,
  pullBack: false,
  correct: false,
  backToReview: false,
  archive: false,
  unarchive: false,
  veto: false,
  runGates: false,
  ownerEdits: false,
};

const PIPELINE_START_STATES: readonly ArticleStatus[] = ['planned', 'in_review', 'needs_attention'];

export function articleActions(article: Article, machines: MachineViews | undefined): ArticleActions {
  const { status } = article;
  const writer = writerOf(status, machines);
  if (!machines || writer === undefined) return NO_ACTIONS;
  const live = article.publish?.live ?? false;
  const lastOperation = article.publish?.lastOperation ?? null;
  const hasDraft = article.markdown.trim() !== '';
  const publishFailedRetry = status === 'publish_failed' && lastOperation !== 'unpublish';
  const unpublishRetry = status === 'publish_failed' && (lastOperation === 'unpublish' || live);
  return {
    startPipeline: PIPELINE_START_STATES.includes(status) && ownerMove(machines, status, 'researching'),
    cancelPipeline: writer === 'pipeline' && ownerMove(machines, status, 'needs_attention'),
    revise: (status === 'in_review' || status === 'needs_attention') && hasDraft && ownerMove(machines, status, 'drafting'),
    approve: status === 'in_review' && ownerMove(machines, status, 'approved') && article.purpose !== 'eval',
    publishNow:
      article.purpose !== 'eval' &&
      ((status === 'in_review' && ownerMove(machines, status, 'approved')) ||
        (status === 'approved' && hasEdge(machines, status, 'publishing')) ||
        (publishFailedRetry && hasEdge(machines, status, 'publishing'))),
    unpublish:
      (status === 'published' && ownerMove(machines, status, 'unpublishing')) ||
      (unpublishRetry && ownerMove(machines, status, 'unpublishing')),
    pullBack: status === 'approved' && ownerMove(machines, status, 'in_review'),
    correct: status === 'published' && ownerMove(machines, status, 'in_review'),
    backToReview:
      (status === 'needs_attention' || status === 'unpublished' || status === 'publish_failed') &&
      ownerMove(machines, status, 'in_review'),
    archive: !live && status !== 'archived' && ownerMove(machines, status, 'archived'),
    unarchive: status === 'archived' && ownerMove(machines, status, 'planned'),
    veto: status === 'approved' && (article.publish?.auto ?? false),
    runGates: writer === 'owner' || (writer === 'none' && status !== 'archived'),
    ownerEdits: writer === 'owner',
  };
}

/** The `lastError` codes the article carries, as sentences (unknown codes get a general one). */
export function lastErrorMessage(code: string | null | undefined): string | undefined {
  if (!code) return undefined;
  switch (code) {
    case 'cancelled':
      return 'The pipeline run was cancelled.';
    case 'refused':
      return 'The model declined to write this draft. Revise the brief or restart from the draft.';
    case 'research_failed':
      return 'Research failed. Restart the pipeline from research.';
    case 'publish_late':
      return 'Publishing was held: it would have gone out more than an hour late. Approve it again.';
    case 'build_failed':
      return 'The portfolio build failed. Retry publishing once the site builds again.';
    case 'verify_timeout':
      return 'The page didn’t show the new version in time. Retry publishing.';
    case 'slug_taken':
      return 'Another published page already uses this slug. Change the slug, then retry.';
    case 'portfolio_not_configured':
      return 'Publishing to the portfolio isn’t set up yet.';
    case 'ai_unavailable':
      return 'The AI models weren’t available. Restart the pipeline when they are.';
    case 'ai_use_not_confirmed':
      return 'Confirm your employer’s AI-use policy in Settings, then restart the pipeline.';
    case 'blocklist_hit':
      return 'The run stopped on a blocklisted term. Rephrase it, then restart the pipeline.';
    case 'blocklist_not_configured':
      return 'Save the blocklist once in Settings, then restart the pipeline.';
    default:
      return 'The last run stopped with an error.';
  }
}
