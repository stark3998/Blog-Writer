import { decodeHTMLStrict } from 'entities';

import type { components } from '@/lib/api/schema';
import type { JobEventRecord } from '@/lib/events/events';

import type { Writer } from './status';
import type { Article, ArticleStatus, MediaRef } from './types';

/**
 * The AI illustrations as the Draft tab reads them (contract revision 5, `blogwriter.illustrations`):
 * the candidates on the article, the hero it publishes with, the headings an image can follow,
 * and when the owner may change any of it. Pure functions: the server has the last word on every
 * rule and the panel shows its refusals.
 */

type Schemas = components['schemas'];

export type Illustration = Schemas['Illustration'];
export type IllustrationRole = Schemas['IllustrationRole'];
export type ModelsView = Schemas['ModelsView'];
export type IllustrationsAccepted = Schemas['IllustrationsAccepted'];

/** `IllustrationsIn.count`: a hero plus up to two images for the text. */
export const ILLUSTRATION_COUNTS = [1, 2, 3] as const;
export type IllustrationCount = (typeof ILLUSTRATION_COUNTS)[number];
/** `IllustrationsIn.direction` (characters). */
export const DIRECTION_MAX = 300;
/** The sizes the plan asks for (`illustrations.briefs.HERO_SIZE`, `INLINE_SIZE`). */
export const HERO_SIZE = '1536x1024';
export const INLINE_SIZE = '1024x1024';

/**
 * The portfolio preview inlines an image only up to 2 MiB and only these types
 * (`articles.preview.MAX_INLINE_IMAGE_BYTES`, `INLINE_CONTENT_TYPES`); a larger hero is left out
 * of the frame and a larger body image shows its alt text.
 */
export const PREVIEW_INLINE_MAX_BYTES = 2 * 1024 * 1024;
const PREVIEW_INLINE_TYPES: ReadonlySet<string> = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml']);

/** The address the body's images use (`MediaRef.URL_PREFIX`), unencoded as the Markdown has it. */
const MEDIA_PREFIX = '/api/media/';

export const ROLE_LABELS: Readonly<Record<IllustrationRole, string>> = {
  hero: 'Planned as the hero',
  inline: 'Planned for the text',
};

// ------------------------------------------------------------------------------------------------
// The hero and the candidates
// ------------------------------------------------------------------------------------------------

export interface HeroView {
  /** The owner's hero image (a chosen illustration or an upload); `undefined` while the title card is the hero. */
  readonly image: MediaRef | undefined;
  /** The title card (`og_image`, 1200x630) once the pipeline rendered it. */
  readonly card: MediaRef | undefined;
}

/**
 * The hero the article publishes with. The pipeline keeps a copy of the title card as a
 * `hero_image`; any other `hero_image` is the owner's (`publish.assets._owner_hero`).
 */
export function heroOf(article: Pick<Article, 'media'>): HeroView {
  const media = article.media ?? [];
  const cards = media.filter((item) => item.role === 'og_image');
  const cardHashes = new Set(cards.map((item) => item.sha256));
  const image = media.find((item) => item.role === 'hero_image' && item.container === 'media' && !cardHashes.has(item.sha256));
  return { image, card: cards[0] };
}

/** Whether the body shows `/api/media/<name>` (`Article.references_media`, the body half). */
export function mediaInBody(markdown: string, name: string): boolean {
  return markdown.includes(`${MEDIA_PREFIX}${name}`);
}

export interface CandidateUse {
  /** It is the hero image. */
  readonly hero: boolean;
  /** The draft shows it. */
  readonly inBody: boolean;
}

export function candidateUse(article: Pick<Article, 'media' | 'markdown'>, illustration: Illustration): CandidateUse {
  const name = illustration.media.name;
  return { hero: heroOf(article).image?.name === name, inBody: mediaInBody(article.markdown, name) };
}

/** Newest run first; a run's own order (the hero first) is kept. */
export function candidatesNewestFirst(list: readonly Illustration[]): Illustration[] {
  return list
    .map((item, index) => ({ item, index }))
    .sort((a, b) => b.item.createdAt.localeCompare(a.item.createdAt) || a.index - b.index)
    .map(({ item }) => item);
}

/** True when the preview frame cannot inline this image (too large, or a type it skips). */
export function previewLeavesOut(media: Pick<MediaRef, 'sizeBytes' | 'contentType'>): boolean {
  return media.sizeBytes > PREVIEW_INLINE_MAX_BYTES || !PREVIEW_INLINE_TYPES.has(media.contentType);
}

/** The candidates the draft shows that the preview frame shows only as alt text. */
export function largeImagesInBody(article: Pick<Article, 'illustrations' | 'markdown'>): number {
  return (article.illustrations ?? []).filter(
    (item) => mediaInBody(article.markdown, item.media.name) && previewLeavesOut(item.media),
  ).length;
}

/** Images in the body (`![alt](src)`), which an approval covers with the text. */
export function bodyImageCount(markdown: string): number {
  return [...markdown.matchAll(/!\[(?:\\.|[^\]\\])*\]\(/g)].length;
}

// ------------------------------------------------------------------------------------------------
// Headings an image can follow
// ------------------------------------------------------------------------------------------------

export interface DraftHeading {
  readonly level: number;
  /** The heading as the Markdown has it: what `POST .../insert` matches (case and markup ignored). */
  readonly source: string;
  /** The heading as a reader sees it. */
  readonly label: string;
}

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const HEADING = /^ {0,3}(#{2,4})[ \t]+([^\n]+?)(?:[ \t]+#+)?[ \t]*$/;
const LINK = /!?\[((?:\\.|[^\]\\])*)\]\([^)]*\)/g;

/**
 * The body's h2-h4 headings in order, outside fenced code: the server's own reading
 * (`articles.illustrations.markdown_headings`), so the text sent back always matches.
 */
export function markdownHeadings(markdown: string): DraftHeading[] {
  const headings: DraftHeading[] = [];
  let fence: string | null = null;
  for (const line of markdown.split('\n')) {
    const opened = FENCE.exec(line);
    if (fence !== null) {
      const marker = opened?.[1] ?? '';
      if (marker !== '' && marker.startsWith(fence.charAt(0)) && marker.length >= fence.length && line.trim().slice(marker.length) === '') {
        fence = null;
      }
      continue;
    }
    if (opened?.[1] !== undefined) {
      fence = opened[1];
      continue;
    }
    const found = HEADING.exec(line);
    const hashes = found?.[1];
    const text = found?.[2]?.trim();
    if (hashes !== undefined && text !== undefined && text !== '') {
      headings.push({ level: hashes.length, source: text, label: headingLabel(text) });
    }
  }
  return headings;
}

/** A heading's Markdown as plain text: link text kept, markup dropped, escapes and entities undone. */
export function headingLabel(source: string): string {
  const text = source
    .replace(LINK, '$1')
    .replace(/(?<!\\)(?:\*+|`+|~~)/g, '')
    .replace(/(?<![\\\p{L}\p{N}])_+|(?<!\\)_+(?![\p{L}\p{N}])/gu, '')
    .replace(/\\(.)/g, '$1');
  const decoded = decodeHTMLStrict(text).replace(/\s+/g, ' ').trim();
  return decoded === '' ? source : decoded;
}

/** The server's comparison key (`_plain`): link text, escapes undone, markup and case ignored. */
function headingKey(text: string): string {
  return text
    .replace(LINK, '$1')
    .replace(/\\(.)/g, '$1')
    .replace(/[`*_~]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

/** The index of the heading the plan suggested (`Illustration.section`), when the draft still has it. */
export function suggestedHeading(headings: readonly DraftHeading[], section: string | null | undefined): number | undefined {
  if (!section) return undefined;
  const wanted = headingKey(section);
  if (wanted === '') return undefined;
  const index = headings.findIndex((heading) => headingKey(heading.source) === wanted);
  return index === -1 ? undefined : index;
}

// ------------------------------------------------------------------------------------------------
// How a run ended
// ------------------------------------------------------------------------------------------------

/** What a run kept (`illustrations.generate`'s `attach` step reports it as a `progress` event). */
export interface RunOutcome {
  /** Candidates the run added. */
  readonly kept: number;
  /** Images it planned. */
  readonly planned: number;
  /** Why the others were dropped: the checks' codes (`legible_text`, `content_filter`, …). */
  readonly reasons: readonly string[];
}

/** The `attach` step's `progress` event (`{done, total, label: "images kept", reasons}`). */
export function runOutcome(events: readonly JobEventRecord[]): RunOutcome | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event?.type !== 'progress' || event.step !== 'attach') continue;
    const { done, total, reasons } = event.data;
    if (typeof done !== 'number' || typeof total !== 'number' || done < 0 || total < done) return undefined;
    const codes = Array.isArray(reasons) ? reasons.filter((code): code is string => typeof code === 'string') : [];
    return { kept: done, planned: total, reasons: codes };
  }
  return undefined;
}

/** The checks' codes in the owner's words (`illustrations.models.VerdictOut.failures` and the job's own). */
const REASON_WORDS: Readonly<Record<string, string>> = {
  off_concept: 'not showing the idea',
  legible_text: 'text in the image',
  branding: 'a logo or branding',
  person: 'a recognisable person',
  unsafe: 'unsuitable content',
  content_filter: 'the image model’s safety filter',
  check_refused: 'a check that couldn’t finish',
  check_invalid: 'a check that couldn’t finish',
};

/** Why images were dropped, in words, each reason once (technical failures as one). */
export function droppedReasons(reasons: readonly string[]): string[] {
  return [...new Set(reasons.map((code) => REASON_WORDS[code] ?? 'a technical problem'))];
}

/** One sentence about the images a successful run dropped; `undefined` when it kept them all. */
export function droppedSentence(outcome: RunOutcome | undefined): string | undefined {
  if (!outcome || outcome.kept >= outcome.planned) return undefined;
  const dropped = outcome.planned - outcome.kept;
  const why = droppedReasons(outcome.reasons);
  const kept = `Kept ${String(outcome.kept)} of ${String(outcome.planned)}: ${dropped === 1 ? 'one didn’t' : `${String(dropped)} didn’t`} pass the checks`;
  return why.length > 0 ? `${kept} (${why.join(', ')}).` : `${kept}.`;
}

// ------------------------------------------------------------------------------------------------
// What the owner may do
// ------------------------------------------------------------------------------------------------

/** Illustrations can't be made here at all: no image model, or switched off in Settings > Models. */
export type ImagesAvailability = 'ready' | 'disabled' | 'unavailable';

export function imagesAvailability(view: Pick<ModelsView, 'routes' | 'preferences'>): ImagesAvailability {
  if (!view.routes.some((route) => route.task === 'image')) return 'unavailable';
  return view.preferences.item.imagesEnabled ? 'ready' : 'disabled';
}

export interface ImagePrices {
  /** USD for the landscape hero. */
  readonly hero: number;
  /** USD for each square image for the text. */
  readonly inline: number;
}

/** The ledger's prices per image (`PriceView.imageUsd`) of the image route's model, when listed. */
export function imagePrices(view: Pick<ModelsView, 'routes' | 'prices'>): ImagePrices | undefined {
  const model = view.routes.find((route) => route.task === 'image')?.chain[0]?.model;
  const listed = view.prices.filter((price) => price.imageUsd);
  const price = listed.find((candidate) => candidate.model === model) ?? listed[0];
  const hero = price?.imageUsd?.[HERO_SIZE];
  const inline = price?.imageUsd?.[INLINE_SIZE];
  if (typeof hero !== 'number' || typeof inline !== 'number') return undefined;
  return { hero, inline };
}

/** What generating `count` images costs before any retry (the checks and the plan add cents). */
export function estimateUsd(prices: ImagePrices, count: number): number {
  return prices.hero + Math.max(0, count - 1) * prices.inline;
}

/** The review states a run may start from (`illustrations.routes.REVIEW_STATES`). */
const GENERATE_STATES: readonly ArticleStatus[] = ['in_review', 'needs_attention'];

/**
 * Why the article's images can't change in this status, in the owner's words; `undefined` when
 * they can (the owner writes in it) or while that is not known yet.
 */
export function imagesLockedReason(status: ArticleStatus, writer: Writer | undefined): string | undefined {
  if (writer === undefined || writer === 'owner') return undefined;
  switch (status) {
    case 'researching':
    case 'interviewing':
    case 'drafting':
    case 'gating':
      return 'The pipeline is working on this article. Its images can change once it is back in review.';
    case 'approved':
      return 'Approved: your approval covers the text and its images. Pull it back to review to change them.';
    case 'publishing':
    case 'unpublishing':
      return 'A publishing run owns the article, so its images can’t change until it ends.';
    case 'published':
      return 'Live: start a correction to change its images. The live page stays up meanwhile.';
    case 'publish_failed':
    case 'unpublished':
      return 'Move the article back to review to change its images.';
    case 'archived':
      return 'Archived: restore it to change its images.';
    case 'planned':
    case 'in_review':
    case 'needs_attention':
      return 'The images can’t change in this status.';
  }
}

/** Why a new run can't start for this article (its status or details); `undefined` when it can. */
export function generateBlockedReason(article: Pick<Article, 'status' | 'meta'>, writer: Writer | undefined): string | undefined {
  if (writer === undefined) return 'Loading what can be done with this article…';
  if (article.status === 'planned') {
    return 'Draft the article first: the illustrations are planned from its title, summary and headings.';
  }
  if (!GENERATE_STATES.includes(article.status)) {
    return imagesLockedReason(article.status, writer) ?? 'Illustrations can be made while the article is in review.';
  }
  if (!article.meta?.title?.trim()) return 'Give the article a title first, under Details.';
  return undefined;
}

/**
 * Which copy of the article is newer: content versions first, then the server's fixed-width UTC
 * `updatedAt`. Answers that arrive out of order never put an older copy in the cache.
 */
export function isNewerArticle(a: Pick<Article, 'version' | 'updatedAt'>, b: Pick<Article, 'version' | 'updatedAt'>): boolean {
  if (a.version !== b.version) return a.version > b.version;
  return a.updatedAt > b.updatedAt;
}
