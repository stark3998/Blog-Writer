import { FRONTMATTER_LIMITS, SLUG_PATTERN } from '@/features/editor';

import type { ArticleMeta, ArticleMetaIn } from './types';

/**
 * The article details (frontmatter, dialect section 2) as the owner edits them: strings in form
 * fields, checked here with the dialect's rules so a bad value never stops the text from saving.
 * A field that does not pass keeps its last saved value in the save until it is fixed.
 */

export interface SeriesDraft {
  readonly id: string;
  readonly title: string;
  readonly part: string;
  readonly total: string;
}

export interface MetaDraft {
  readonly title: string;
  readonly slug: string;
  readonly excerpt: string;
  readonly pillar: string;
  readonly format: string;
  /** Comma-separated, as typed. */
  readonly tags: string;
  /** `null` when the article is not part of a series. */
  readonly series: SeriesDraft | null;
}

export type MetaField = 'title' | 'slug' | 'excerpt' | 'pillar' | 'format' | 'tags' | 'series.id' | 'series.title' | 'series.part' | 'series.total';

export type MetaErrors = Partial<Record<MetaField, string>>;

/** Pillar and format ids are short slugs on the server (`_ShortSlug`, 40 characters). */
const SHORT_SLUG_MAX = 40;
const SERIES_PART_MAX = 9999;

export const EMPTY_META_DRAFT: MetaDraft = {
  title: '',
  slug: '',
  excerpt: '',
  pillar: '',
  format: '',
  tags: '',
  series: null,
};

export function draftFromMeta(meta: ArticleMeta | undefined): MetaDraft {
  if (!meta) return EMPTY_META_DRAFT;
  const series = meta.series ?? null;
  return {
    title: meta.title ?? '',
    slug: meta.slug ?? '',
    excerpt: meta.excerpt ?? '',
    pillar: meta.pillar ?? '',
    format: meta.format ?? '',
    tags: (meta.tags ?? []).join(', '),
    series: series
      ? {
          id: series.id,
          title: series.title,
          part: String(series.part),
          total: series.total === null || series.total === undefined ? '' : String(series.total),
        }
      : null,
  };
}

/** The tags typed as a list: split on commas, trimmed, empty entries dropped. */
export function parseTags(value: string): string[] {
  return value
    .split(',')
    .map((tag) => tag.trim())
    .filter((tag) => tag !== '');
}

// eslint-disable-next-line no-control-regex -- the dialect forbids control characters in plain-text fields.
const CONTROL_OR_BREAK = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;

function plainTextError(value: string, max: number): string | undefined {
  if (value === '') return undefined;
  if (value !== value.trim()) return 'Remove the spaces at the start or end.';
  if (value.includes('<')) return 'Plain text only: remove the “<”.';
  if (CONTROL_OR_BREAK.test(value)) return 'Keep it on one line.';
  if (value.length > max) return `Use at most ${max} characters.`;
  return undefined;
}

function slugError(value: string, max: number): string | undefined {
  if (value === '') return undefined;
  if (!SLUG_PATTERN.test(value)) return 'Use lowercase letters, digits and single hyphens, like agent-identity.';
  if (value.length > max) return `Use at most ${max} characters.`;
  return undefined;
}

function partError(value: string, required: boolean): string | undefined {
  if (value.trim() === '') return required ? 'Give the part number.' : undefined;
  if (!/^\d+$/.test(value.trim())) return 'Use a whole number.';
  const number = Number(value.trim());
  if (number < 1 || number > SERIES_PART_MAX) return `Use a number from 1 to ${SERIES_PART_MAX}.`;
  return undefined;
}

/** Field errors, by the dialect's rules. Empty fields are fine while drafting. */
export function validateMetaDraft(draft: MetaDraft): MetaErrors {
  const errors: MetaErrors = {};
  const set = (field: MetaField, message: string | undefined) => {
    if (message) errors[field] = message;
  };
  set('title', plainTextError(draft.title, FRONTMATTER_LIMITS.title));
  set('excerpt', plainTextError(draft.excerpt, FRONTMATTER_LIMITS.excerpt));
  set('slug', slugError(draft.slug, FRONTMATTER_LIMITS.slug));
  set('pillar', slugError(draft.pillar, SHORT_SLUG_MAX));
  set('format', slugError(draft.format, SHORT_SLUG_MAX));

  const tags = parseTags(draft.tags);
  if (tags.length > FRONTMATTER_LIMITS.tags) {
    set('tags', `Use at most ${FRONTMATTER_LIMITS.tags} tags.`);
  } else {
    const bad = tags.find((tag) => slugError(tag, Math.min(FRONTMATTER_LIMITS.tag, SHORT_SLUG_MAX)) !== undefined);
    const duplicate = tags.find((tag, index) => tags.indexOf(tag) !== index);
    if (bad !== undefined) set('tags', `“${bad}” isn’t a tag: use lowercase letters, digits and hyphens, at most ${FRONTMATTER_LIMITS.tag} characters.`);
    else if (duplicate !== undefined) set('tags', `“${duplicate}” is listed twice.`);
  }

  if (draft.series) {
    const { id, title, part, total } = draft.series;
    set('series.id', id === '' ? 'Give the series an id.' : slugError(id, FRONTMATTER_LIMITS.slug));
    set('series.title', title === '' ? 'Give the series a title.' : plainTextError(title, FRONTMATTER_LIMITS.seriesTitle));
    set('series.part', partError(part, true));
    const totalError = partError(total, false);
    if (totalError) set('series.total', totalError);
    else if (total.trim() !== '' && part.trim() !== '' && Number(total) < Number(part)) {
      set('series.total', 'The total can’t be lower than the part.');
    }
  }
  return errors;
}

function seriesErrors(errors: MetaErrors): boolean {
  return Boolean(errors['series.id'] ?? errors['series.title'] ?? errors['series.part'] ?? errors['series.total']);
}

/**
 * The details to save: every valid field as typed, every invalid one as it was last saved, so
 * the text keeps saving while a detail is being fixed.
 */
export function metaForSave(draft: MetaDraft, errors: MetaErrors, saved: ArticleMeta | undefined): ArticleMetaIn {
  const text = (field: 'title' | 'excerpt' | 'slug' | 'pillar' | 'format'): string | null => {
    if (errors[field]) return saved?.[field] ?? null;
    const value = draft[field];
    return value === '' ? null : value;
  };
  const tags = errors.tags ? [...(saved?.tags ?? [])] : parseTags(draft.tags);
  let series: ArticleMetaIn['series'] = null;
  if (draft.series && !seriesErrors(errors)) {
    const { id, title, part, total } = draft.series;
    series = { id, title, part: Number(part), total: total.trim() === '' ? null : Number(total) };
  } else if (draft.series) {
    const previous = saved?.series ?? null;
    series = previous ? { id: previous.id, title: previous.title, part: previous.part, total: previous.total ?? null } : null;
  }
  return {
    title: text('title'),
    slug: text('slug'),
    excerpt: text('excerpt'),
    pillar: text('pillar'),
    format: text('format'),
    tags,
    series,
  };
}

/** A stable string for comparing two sets of details. */
export function metaKey(meta: ArticleMetaIn): string {
  return JSON.stringify([
    meta.title ?? null,
    meta.slug ?? null,
    meta.excerpt ?? null,
    meta.pillar ?? null,
    meta.format ?? null,
    meta.tags ?? [],
    meta.series ? [meta.series.id, meta.series.title, meta.series.part, meta.series.total ?? null] : null,
  ]);
}

/** The details a published article needs (`ArticleMeta.missing_for_publish`), by label. */
export const REQUIRED_FOR_PUBLISH: readonly { readonly field: 'title' | 'slug' | 'excerpt' | 'pillar' | 'format'; readonly label: string }[] = [
  { field: 'title', label: 'Title' },
  { field: 'slug', label: 'Slug' },
  { field: 'excerpt', label: 'Excerpt' },
  { field: 'pillar', label: 'Pillar' },
  { field: 'format', label: 'Format' },
];

export function missingForPublish(meta: ArticleMeta | undefined): string[] {
  return REQUIRED_FOR_PUBLISH.filter(({ field }) => !meta?.[field]).map(({ label }) => label);
}

/** A slug from a title: lowercase ASCII words joined by hyphens, cut at a word boundary. */
export function slugify(title: string, max: number = FRONTMATTER_LIMITS.slug): string {
  const words = title
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  let slug = '';
  for (const word of words) {
    const next = slug === '' ? word : `${slug}-${word}`;
    if (next.length > max) break;
    slug = next;
  }
  return slug === '' ? (words[0]?.slice(0, max) ?? '') : slug;
}
