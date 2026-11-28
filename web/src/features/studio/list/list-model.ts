import { nonEmpty } from '../piece/format';
import {
  ARTICLE_STATUS,
  ARTICLE_STATUSES,
  firstLine,
  POST_STATUS,
  POST_STATUSES,
  STAGE_GROUPS,
  type PieceFacts,
  type StageGroup,
} from '../piece/piece-status';
import type { ArticleStatus, ArticleSummary, Lane, PostFormat, PostStatus, PostSummary } from '../piece/types';

/**
 * The Studio list as data: which statuses each filter asks the server for, one shape for articles
 * and standalone posts, search, and the groups the list shows. Pure, so it is tested in Node.
 */

/** Which pieces the list shows. */
export type ShowFilter = 'active' | 'live' | 'archived' | 'all';
/** Articles, posts or both. */
export type KindFilter = 'all' | 'article' | 'post';

export const SHOW_OPTIONS: readonly { readonly id: ShowFilter; readonly label: string }[] = [
  { id: 'active', label: 'In progress' },
  { id: 'live', label: 'Live' },
  { id: 'archived', label: 'Archived' },
  { id: 'all', label: 'All' },
];

export const KIND_OPTIONS: readonly { readonly id: KindFilter; readonly label: string }[] = [
  { id: 'all', label: 'All pieces' },
  { id: 'article', label: 'Articles' },
  { id: 'post', label: 'Posts' },
];

/** Standalone posts are Studio pieces; derivatives live in their article's Posts tab. */
export const STANDALONE_LANES: readonly Lane[] = ['native', 'quick_take', 'repurpose'];

const ACTIVE_GROUPS: ReadonlySet<StageGroup> = new Set(['needs-you', 'working', 'planned', 'scheduled']);

export function parseShow(value: string | null): ShowFilter {
  return SHOW_OPTIONS.some((option) => option.id === value) ? (value as ShowFilter) : 'active';
}

export function parseKind(value: string | null): KindFilter {
  return KIND_OPTIONS.some((option) => option.id === value) ? (value as KindFilter) : 'all';
}

/** The article statuses a filter asks `GET /api/articles?status=` for; `undefined` is every status. */
export function articleStatusesFor(show: ShowFilter): ArticleStatus[] | undefined {
  switch (show) {
    case 'active':
      return ARTICLE_STATUSES.filter((status) => ACTIVE_GROUPS.has(ARTICLE_STATUS[status].group));
    case 'live':
      return ['published'];
    case 'archived':
      return ['archived', 'unpublished'];
    case 'all':
      return undefined;
  }
}

/** The post statuses a filter asks `GET /api/posts?status=` for; `undefined` is every status. */
export function postStatusesFor(show: ShowFilter): PostStatus[] | undefined {
  switch (show) {
    case 'active':
      return POST_STATUSES.filter((status) => ACTIVE_GROUPS.has(POST_STATUS[status].group));
    case 'live':
      return ['posted'];
    case 'archived':
      return ['archived', 'expired', 'deleted'];
    case 'all':
      return undefined;
  }
}

/** One row of the Studio list, from an article or a post summary. */
export interface ListPiece {
  readonly kind: 'article' | 'post';
  readonly id: string;
  /** The title to show: the article's title or the post's first line, else "Untitled …". */
  readonly title: string;
  readonly untitled: boolean;
  readonly group: StageGroup;
  readonly pillar: string | null;
  /** Article: the format pack id. Post: its format. */
  readonly format: string | null;
  readonly postFormat: PostFormat | null;
  readonly lane: Lane | null;
  readonly updatedAt: string;
  readonly reviewOnly: boolean;
  readonly wordCount: number;
  readonly facts: PieceFacts;
}

export function fromArticle(summary: ArticleSummary): ListPiece {
  const title = nonEmpty(summary.meta.title);
  return {
    kind: 'article',
    id: summary.id,
    title: title ?? 'Untitled piece',
    untitled: title === undefined,
    group: ARTICLE_STATUS[summary.status].group,
    pillar: summary.meta.pillar,
    format: summary.meta.format,
    postFormat: null,
    lane: null,
    updatedAt: summary.updatedAt,
    reviewOnly: summary.reviewOnly,
    wordCount: summary.wordCount,
    facts: {
      kind: 'article',
      id: summary.id,
      status: summary.status,
      publishAt: summary.publish.publishAt,
      liveUrl: summary.publish.url,
    },
  };
}

export function fromPost(summary: PostSummary): ListPiece {
  const title = firstLine(summary.hook);
  return {
    kind: 'post',
    id: summary.id,
    title: title ?? 'Untitled post',
    untitled: title === undefined,
    group: POST_STATUS[summary.status].group,
    pillar: summary.pillar,
    format: summary.format,
    postFormat: summary.format,
    lane: summary.lane,
    updatedAt: summary.updatedAt,
    reviewOnly: summary.reviewOnly,
    wordCount: 0,
    facts: { kind: 'post', id: summary.id, status: summary.status, slot: summary.slot, postedAt: summary.postedAt },
  };
}

/** Lower case, without accents, so "Entra" finds "entra" and "Café" finds "cafe". */
export function normalizeText(text: string): string {
  return text.normalize('NFKD').replace(/\p{Diacritic}/gu, '').toLowerCase();
}

/** True when every word of `query` appears in the piece's title. */
export function matchesSearch(piece: Pick<ListPiece, 'title'>, query: string): boolean {
  const words = normalizeText(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const title = normalizeText(piece.title);
  return words.every((word) => title.includes(word));
}

export interface PieceGroup {
  readonly group: StageGroup;
  readonly label: string;
  readonly description: string;
  readonly pieces: readonly ListPiece[];
}

/** The pieces in the list's groups (Needs you first), newest change first within each; empty groups left out. */
export function groupPieces(pieces: readonly ListPiece[]): PieceGroup[] {
  return STAGE_GROUPS.flatMap(({ id, label, description }) => {
    const members = pieces
      .filter((piece) => piece.group === id)
      .toSorted((a, b) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0));
    return members.length > 0 ? [{ group: id, label, description, pieces: members }] : [];
  });
}

/** Merges the two lists (an id appears once) in newest-first order. */
export function mergePieces(articles: readonly ArticleSummary[], posts: readonly PostSummary[]): ListPiece[] {
  const seen = new Set<string>();
  const merged: ListPiece[] = [];
  for (const piece of [...articles.map(fromArticle), ...posts.map(fromPost)]) {
    if (seen.has(piece.id)) continue;
    seen.add(piece.id);
    merged.push(piece);
  }
  return merged;
}
