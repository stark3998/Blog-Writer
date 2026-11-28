import { wordCount } from './text';

/**
 * The carousel slide spec as the composer edits it: a typed copy of `blogwriter.render.spec`
 * (`CarouselSpec`), which the server stores opaque on the post (`carouselSpec`) and validates on
 * `PUT /api/posts/{id}/carousel` (422 `invalid_spec` with `errors[{loc, msg, type}]`).
 *
 * Field limits are the server's layout limits (`maxLength` on the inputs). The editorial rules
 * (title <= 58, 8-12 slides, cover first with <= 10 words, <= 35 words a slide, recap last) block
 * approval, not saving: the server's lint reports them; `slideHints` mirrors them as you type.
 */

export const SLIDE_TYPES = [
  'cover',
  'statement',
  'list',
  'steps',
  'framework',
  'diagram',
  'stat',
  'quote',
  'recap',
  'sources',
] as const;

export type SlideType = (typeof SLIDE_TYPES)[number];

export const SLIDE_TYPE_LABELS: Readonly<Record<SlideType, string>> = {
  cover: 'Cover',
  statement: 'Statement',
  list: 'List',
  steps: 'Steps',
  framework: 'Framework',
  diagram: 'Diagram',
  stat: 'Stat',
  quote: 'Quote',
  recap: 'Recap',
  sources: 'Sources',
};

export const SLIDE_TYPE_HINTS: Readonly<Record<SlideType, string>> = {
  cover: 'The hook in 10 words or fewer.',
  statement: 'One claim, said plainly.',
  list: 'Two to five short items.',
  steps: 'Two to four steps in order.',
  framework: 'A 2×2 or 3 to 5 layers.',
  diagram: 'A Mermaid diagram with alt text.',
  stat: 'One number and what it means.',
  quote: 'A quote and who said it.',
  recap: 'What to do on Monday. The last content slide.',
  sources: 'Up to five sources, after the recap.',
};

export interface CoverSlide {
  type: 'cover';
  kicker?: string | null;
  hook: string;
  subtitle?: string | null;
}
export interface StatementSlide {
  type: 'statement';
  statement: string;
  note?: string | null;
}
export interface ListSlide {
  type: 'list';
  title: string;
  items: string[];
}
export interface Step {
  title: string;
  detail?: string | null;
}
export interface StepsSlide {
  type: 'steps';
  title: string;
  steps: Step[];
}
export interface Cell {
  label: string;
  text: string;
}
export interface FrameworkSlide {
  type: 'framework';
  title: string;
  layout: '2x2' | 'layered';
  cells: Cell[];
  x_axis?: string | null;
  y_axis?: string | null;
}
export interface DiagramSlide {
  type: 'diagram';
  title: string;
  mermaid: string;
  alt: string;
  caption?: string | null;
}
export interface StatSlide {
  type: 'stat';
  value: string;
  label: string;
  context?: string | null;
  source?: string | null;
}
export interface QuoteSlide {
  type: 'quote';
  quote: string;
  attribution: string;
  role?: string | null;
}
export interface RecapSlide {
  type: 'recap';
  title: string;
  items: string[];
}
export interface Source {
  publisher: string;
  title: string;
  date?: string | null;
}
export interface SourcesSlide {
  type: 'sources';
  title: string;
  sources: Source[];
}

export type Slide =
  | CoverSlide
  | StatementSlide
  | ListSlide
  | StepsSlide
  | FrameworkSlide
  | DiagramSlide
  | StatSlide
  | QuoteSlide
  | RecapSlide
  | SourcesSlide;

/** A slide the composer cannot edit (a newer server type): kept exactly as it was. */
export interface OpaqueSlide {
  type: 'opaque';
  raw: Record<string, unknown>;
}

export interface CarouselSpec {
  title: string;
  lang: string;
  slides: (Slide | OpaqueSlide)[];
}

/** The server's layout limits per field (`render.spec`). */
export const LIMITS = {
  title: 100,
  titleRecommended: 58,
  coverWords: 10,
  slideWords: 35,
  slidesMin: 8,
  slidesMax: 12,
  slidesHardMax: 20,
  kicker: 40,
  hook: 80,
  subtitle: 110,
  statement: 200,
  note: 140,
  slideTitle: 70,
  listItem: 100,
  listItemsMin: 2,
  listItemsMax: 5,
  stepTitle: 48,
  stepDetail: 90,
  stepsMin: 2,
  stepsMax: 4,
  cellLabel: 28,
  cellText: 64,
  cellsMin: 3,
  cellsMax: 5,
  axis: 28,
  mermaid: 3000,
  alt: 250,
  caption: 120,
  statValue: 10,
  statLabel: 90,
  statContext: 140,
  statSource: 90,
  quote: 200,
  attribution: 48,
  role: 64,
  recapTitle: 60,
  recapItem: 90,
  sourcesTitle: 40,
  sourcesMax: 5,
  publisher: 40,
  sourceTitle: 100,
  sourceDate: 16,
} as const;

// ------------------------------------------------------------------------------------------------
// Reading the stored spec
// ------------------------------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function optional(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text) : [];
}

/** One stored slide as a typed slide, or an opaque one when its shape is unknown. */
export function readSlide(raw: unknown): Slide | OpaqueSlide {
  if (!isRecord(raw)) return { type: 'opaque', raw: {} };
  switch (raw.type) {
    case 'cover':
      return { type: 'cover', kicker: optional(raw.kicker), hook: text(raw.hook), subtitle: optional(raw.subtitle) };
    case 'statement':
      return { type: 'statement', statement: text(raw.statement), note: optional(raw.note) };
    case 'list':
      return { type: 'list', title: text(raw.title), items: texts(raw.items) };
    case 'steps':
      return {
        type: 'steps',
        title: text(raw.title),
        steps: Array.isArray(raw.steps)
          ? raw.steps.map((step: unknown) => ({
              title: isRecord(step) ? text(step.title) : '',
              detail: isRecord(step) ? optional(step.detail) : null,
            }))
          : [],
      };
    case 'framework':
      return {
        type: 'framework',
        title: text(raw.title),
        layout: raw.layout === 'layered' ? 'layered' : '2x2',
        cells: Array.isArray(raw.cells)
          ? raw.cells.map((cell: unknown) => ({
              label: isRecord(cell) ? text(cell.label) : '',
              text: isRecord(cell) ? text(cell.text) : '',
            }))
          : [],
        x_axis: optional(raw.x_axis),
        y_axis: optional(raw.y_axis),
      };
    case 'diagram':
      return {
        type: 'diagram',
        title: text(raw.title),
        mermaid: text(raw.mermaid),
        alt: text(raw.alt),
        caption: optional(raw.caption),
      };
    case 'stat':
      return {
        type: 'stat',
        value: text(raw.value),
        label: text(raw.label),
        context: optional(raw.context),
        source: optional(raw.source),
      };
    case 'quote':
      return { type: 'quote', quote: text(raw.quote), attribution: text(raw.attribution), role: optional(raw.role) };
    case 'recap':
      return { type: 'recap', title: text(raw.title) || 'Do this Monday', items: texts(raw.items) };
    case 'sources':
      return {
        type: 'sources',
        title: text(raw.title) || 'Sources',
        sources: Array.isArray(raw.sources)
          ? raw.sources.map((source: unknown) => ({
              publisher: isRecord(source) ? text(source.publisher) : '',
              title: isRecord(source) ? text(source.title) : '',
              date: isRecord(source) ? optional(source.date) : null,
            }))
          : [],
      };
    default:
      return { type: 'opaque', raw };
  }
}

/** The stored spec (`post.carouselSpec`) as a typed spec, or `undefined` when there is none. */
export function readSpec(stored: Record<string, unknown> | null | undefined): CarouselSpec | undefined {
  if (!isRecord(stored)) return undefined;
  return {
    title: text(stored.title),
    lang: typeof stored.lang === 'string' && stored.lang !== '' ? stored.lang : 'en',
    slides: Array.isArray(stored.slides) ? stored.slides.map(readSlide) : [],
  };
}

// ------------------------------------------------------------------------------------------------
// Writing it back
// ------------------------------------------------------------------------------------------------

/** Optional text: trimmed, and left out when empty (the server refuses empty plain text). */
function opt(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim() ?? '';
  return trimmed === '' ? undefined : trimmed;
}

function req(value: string): string {
  return value.trim();
}

function withOptional(
  base: Readonly<Record<string, unknown>>,
  extras: Readonly<Record<string, string | undefined>>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [name, value] of Object.entries(extras)) if (value !== undefined) out[name] = value;
  return out;
}

/** One slide as the server's JSON (`CarouselSpec.slides[i]`). */
export function slideJson(slide: Slide | OpaqueSlide): Record<string, unknown> {
  switch (slide.type) {
    case 'opaque':
      return slide.raw;
    case 'cover':
      return withOptional({ type: 'cover', hook: req(slide.hook) }, { kicker: opt(slide.kicker), subtitle: opt(slide.subtitle) });
    case 'statement':
      return withOptional({ type: 'statement', statement: req(slide.statement) }, { note: opt(slide.note) });
    case 'list':
      return { type: 'list', title: req(slide.title), items: slide.items.map(req) };
    case 'steps':
      return {
        type: 'steps',
        title: req(slide.title),
        steps: slide.steps.map((step) => withOptional({ title: req(step.title) }, { detail: opt(step.detail) })),
      };
    case 'framework':
      return withOptional(
        {
          type: 'framework',
          title: req(slide.title),
          layout: slide.layout,
          cells: slide.cells.map((cell) => ({ label: req(cell.label), text: req(cell.text) })),
        },
        slide.layout === '2x2' ? { x_axis: opt(slide.x_axis), y_axis: opt(slide.y_axis) } : {},
      );
    case 'diagram':
      return withOptional(
        { type: 'diagram', title: req(slide.title), mermaid: slide.mermaid, alt: req(slide.alt) },
        { caption: opt(slide.caption) },
      );
    case 'stat':
      return withOptional(
        { type: 'stat', value: req(slide.value), label: req(slide.label) },
        { context: opt(slide.context), source: opt(slide.source) },
      );
    case 'quote':
      return withOptional(
        { type: 'quote', quote: req(slide.quote), attribution: req(slide.attribution) },
        { role: opt(slide.role) },
      );
    case 'recap':
      return { type: 'recap', title: req(slide.title) || 'Do this Monday', items: slide.items.map(req) };
    case 'sources':
      return {
        type: 'sources',
        title: req(slide.title) || 'Sources',
        sources: slide.sources.map((source) =>
          withOptional({ publisher: req(source.publisher), title: req(source.title) }, { date: opt(source.date) }),
        ),
      };
  }
}

/** The whole spec as `PUT /api/posts/{id}/carousel` takes it (`{spec}`). */
export function specJson(spec: CarouselSpec): Record<string, unknown> {
  return { title: spec.title.trim(), lang: spec.lang, slides: spec.slides.map(slideJson) };
}

/** True when two specs would be stored the same. */
export function sameSpec(a: CarouselSpec | undefined, b: CarouselSpec | undefined): boolean {
  if (a === undefined || b === undefined) return a === b;
  return JSON.stringify(specJson(a)) === JSON.stringify(specJson(b));
}

// ------------------------------------------------------------------------------------------------
// New slides
// ------------------------------------------------------------------------------------------------

/** An empty slide of `type`, ready to fill in. */
export function newSlide(type: SlideType): Slide {
  switch (type) {
    case 'cover':
      return { type, kicker: null, hook: '', subtitle: null };
    case 'statement':
      return { type, statement: '', note: null };
    case 'list':
      return { type, title: '', items: ['', ''] };
    case 'steps':
      return { type, title: '', steps: [{ title: '', detail: null }, { title: '', detail: null }] };
    case 'framework':
      return {
        type,
        title: '',
        layout: '2x2',
        cells: Array.from({ length: 4 }, () => ({ label: '', text: '' })),
        x_axis: null,
        y_axis: null,
      };
    case 'diagram':
      return { type, title: '', mermaid: 'flowchart LR\n  A --> B', alt: '', caption: null };
    case 'stat':
      return { type, value: '', label: '', context: null, source: null };
    case 'quote':
      return { type, quote: '', attribution: '', role: null };
    case 'recap':
      return { type, title: 'Do this Monday', items: ['', ''] };
    case 'sources':
      return { type, title: 'Sources', sources: [{ publisher: '', title: '', date: null }] };
  }
}

/**
 * Changes a slide's type, keeping the words that fit the new type: a cover's hook becomes a
 * statement, list items become recap items, and so on.
 */
export function convertSlide(slide: Slide | OpaqueSlide, type: SlideType): Slide {
  const next = newSlide(type);
  if (slide.type === 'opaque') return next;
  const lead = primaryText(slide);
  const items = itemTexts(slide);
  switch (next.type) {
    case 'cover':
      return { ...next, hook: lead };
    case 'statement':
      return { ...next, statement: lead };
    case 'list':
    case 'recap':
      return {
        ...next,
        title: slide.type === 'cover' || slide.type === 'statement' ? next.title : lead || next.title,
        items: items.length >= 2 ? items.slice(0, 5) : next.items,
      };
    case 'steps':
      return {
        ...next,
        title: lead,
        steps: items.length >= 2 ? items.slice(0, 4).map((title) => ({ title, detail: null })) : next.steps,
      };
    case 'stat':
      return { ...next, label: lead };
    case 'quote':
      return { ...next, quote: lead };
    case 'framework':
    case 'diagram':
    case 'sources':
      return { ...next, title: slide.type === 'cover' || slide.type === 'statement' ? '' : lead };
  }
}

function primaryText(slide: Slide): string {
  switch (slide.type) {
    case 'cover':
      return slide.hook;
    case 'statement':
      return slide.statement;
    case 'stat':
      return slide.label;
    case 'quote':
      return slide.quote;
    case 'list':
    case 'steps':
    case 'framework':
    case 'diagram':
    case 'recap':
    case 'sources':
      return slide.title;
  }
}

function itemTexts(slide: Slide): string[] {
  switch (slide.type) {
    case 'list':
    case 'recap':
      return slide.items.filter((item) => item.trim() !== '');
    case 'steps':
      return slide.steps.map((step) => step.title).filter((item) => item.trim() !== '');
    case 'cover':
    case 'statement':
    case 'framework':
    case 'diagram':
    case 'stat':
    case 'quote':
    case 'sources':
      return [];
  }
}

/**
 * A deck to start from when a carousel has no slides yet: cover first, recap last, 8 slides, the
 * hook as the cover. The owner fills in the rest (or asks the writer for slides).
 */
export function starterSpec(title: string, hook: string): CarouselSpec {
  const cover: CoverSlide = { type: 'cover', kicker: null, hook: firstWords(hook, LIMITS.coverWords), subtitle: null };
  return {
    title: title.slice(0, LIMITS.title),
    lang: 'en',
    slides: [
      cover,
      newSlide('statement'),
      newSlide('list'),
      newSlide('steps'),
      newSlide('stat'),
      newSlide('framework'),
      newSlide('quote'),
      newSlide('recap'),
    ],
  };
}

/** The single slide of a new image card. */
export function starterCard(title: string, hook: string): CarouselSpec {
  return {
    title: title.slice(0, LIMITS.title),
    lang: 'en',
    slides: [{ type: 'statement', statement: hook.slice(0, LIMITS.statement), note: null } satisfies StatementSlide],
  };
}

function firstWords(value: string, count: number): string {
  return value.trim().split(/\s+/u).filter(Boolean).slice(0, count).join(' ');
}

// ------------------------------------------------------------------------------------------------
// Counting and hints
// ------------------------------------------------------------------------------------------------

/** Every visible text of a slide (`render.lint.slide_text`; a diagram counts title and caption). */
export function slideTexts(slide: Slide | OpaqueSlide): string[] {
  switch (slide.type) {
    case 'opaque':
      return [];
    case 'cover':
      return [slide.kicker, slide.hook, slide.subtitle].filter(nonEmpty);
    case 'statement':
      return [slide.statement, slide.note].filter(nonEmpty);
    case 'list':
    case 'recap':
      return [slide.title, ...slide.items].filter(nonEmpty);
    case 'steps':
      return [slide.title, ...slide.steps.flatMap((step) => [step.title, step.detail])].filter(nonEmpty);
    case 'framework':
      return [slide.title, ...slide.cells.flatMap((cell) => [cell.label, cell.text]), slide.x_axis, slide.y_axis].filter(
        nonEmpty,
      );
    case 'diagram':
      return [slide.title, slide.caption].filter(nonEmpty);
    case 'stat':
      return [slide.value, slide.label, slide.context, slide.source].filter(nonEmpty);
    case 'quote':
      return [slide.quote, slide.attribution, slide.role].filter(nonEmpty);
    case 'sources':
      return [slide.title, ...slide.sources.flatMap((source) => [source.publisher, source.title, source.date])].filter(
        nonEmpty,
      );
  }
}

function nonEmpty(value: string | null | undefined): value is string {
  return typeof value === 'string' && value.trim() !== '';
}

/** Words on a slide, as the 35-word rule counts them. */
export function slideWords(slide: Slide | OpaqueSlide): number {
  return slideTexts(slide).reduce((sum, value) => sum + wordCount(value), 0);
}

/** The word limit that applies to a slide (`undefined`: none, for the sources slide). */
export function slideWordLimit(slide: Slide | OpaqueSlide): number | undefined {
  if (slide.type === 'sources' || slide.type === 'opaque') return undefined;
  return LIMITS.slideWords;
}

/** One rule a deck breaks, as the composer sees it before the server's lint does. */
export interface SlideHint {
  /** 0-based; `undefined` for the whole deck. */
  readonly slide?: number;
  readonly message: string;
}

/**
 * The plan's carousel rules (`render.lint.lint_carousel`) as hints. They block approval, not
 * saving or previewing. `card`: an image card, where only the word limit and the one-slide rule
 * apply.
 */
export function slideHints(spec: CarouselSpec, { card = false }: { card?: boolean } = {}): SlideHint[] {
  const hints: SlideHint[] = [];
  const count = spec.slides.length;
  if (card) {
    if (count !== 1) hints.push({ message: 'An image card has exactly one slide.' });
  } else {
    if (spec.title.trim().length > LIMITS.titleRecommended) {
      hints.push({ message: `The document title is ${spec.title.trim().length} characters; keep it to ${LIMITS.titleRecommended}.` });
    }
    if (count < LIMITS.slidesMin || count > LIMITS.slidesMax) {
      hints.push({ message: `${count} ${count === 1 ? 'slide' : 'slides'}; carousels use ${LIMITS.slidesMin} to ${LIMITS.slidesMax}.` });
    }
    const first = spec.slides[0];
    if (first !== undefined && first.type !== 'cover') hints.push({ slide: 0, message: 'The first slide must be the cover.' });
    if (first?.type === 'cover' && wordCount(first.hook) > LIMITS.coverWords) {
      hints.push({ slide: 0, message: `The cover’s hook is ${wordCount(first.hook)} words; keep it to ${LIMITS.coverWords}.` });
    }
    const last = spec.slides.at(-1);
    const lastContent = last?.type === 'sources' ? spec.slides.at(-2) : last;
    if (lastContent !== undefined && lastContent.type !== 'recap') {
      hints.push({ message: 'End with the recap slide (a sources slide may follow it).' });
    }
    spec.slides.forEach((slide, index) => {
      if (slide.type === 'sources' && index !== count - 1) {
        hints.push({ slide: index, message: 'A sources slide can only come last, after the recap.' });
      }
    });
  }
  spec.slides.forEach((slide, index) => {
    const limit = slideWordLimit(slide);
    const words = slideWords(slide);
    if (limit !== undefined && words > limit) {
      hints.push({ slide: index, message: `${words} words on this slide; keep it to ${limit}.` });
    }
  });
  return hints;
}

// ------------------------------------------------------------------------------------------------
// Server validation errors
// ------------------------------------------------------------------------------------------------

/** One `invalid_spec` error: the slide (0-based) and field it names, and the message. */
export interface SpecError {
  readonly slide?: number;
  readonly field?: string;
  readonly message: string;
}

/**
 * The `errors[]` of a 422 `invalid_spec` (`{loc, msg, type}`; never the values) as slide errors.
 * Locations look like `["slides", 2, "cover", "hook"]` (the union's tag names the type).
 */
export function specErrors(errors: unknown): SpecError[] {
  if (!Array.isArray(errors)) return [];
  return errors.flatMap((entry: unknown): SpecError[] => {
    if (!isRecord(entry)) return [];
    const loc = Array.isArray(entry.loc) ? entry.loc : [];
    const message = typeof entry.msg === 'string' ? sentence(entry.msg) : 'This value isn’t valid.';
    if (loc[0] !== 'slides' || typeof loc[1] !== 'number') {
      return [{ field: loc.filter((part): part is string => typeof part === 'string').join('.') || undefined, message }];
    }
    const rest = loc.slice(2).filter((part, index) => !(index === 0 && typeof part === 'string' && isSlideType(part)));
    const field = rest.map(String).join('.') || undefined;
    return [{ slide: loc[1], field, message }];
  });
}

function isSlideType(value: string): value is SlideType {
  return (SLIDE_TYPES as readonly string[]).includes(value);
}

function sentence(message: string): string {
  const text = message.trim();
  if (text === '') return 'This value isn’t valid.';
  const capital = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

// ------------------------------------------------------------------------------------------------
// Moving slides
// ------------------------------------------------------------------------------------------------

/** `items` with the item at `from` moved to `to` (both clamped). */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  const out = [...items];
  if (from < 0 || from >= out.length) return out;
  const target = Math.min(Math.max(to, 0), out.length - 1);
  const [moved] = out.splice(from, 1);
  if (moved === undefined) return [...items];
  out.splice(target, 0, moved);
  return out;
}
