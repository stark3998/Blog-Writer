import { queryKeys } from '@/lib/query/keys';

import type { ErrorMap } from '../lib/errors';
import { isSlug, repeatedIndexes } from '../lib/rules';
import type { ClassifierCheck, Confidentiality, Entry, PovEntry, PovIn, StoryEntry, StoryIn } from '../lib/types';

/**
 * `GET /api/kb/inputs` (the owner-input checklist: approved stories, positions per pillar). It
 * counts across kinds, so it sits under Today's key, which every `entity.changed` refreshes.
 */
export const OWNER_INPUTS_KEY = [...queryKeys.today, 'owner-inputs'] as const;

/** `StoryIn` / `PovIn` limits (`blogwriter.stories.models`). */
export const STORY_LIMITS = {
  title: 120,
  text: 2000,
  metrics: 10,
  metricLabel: 120,
  metricValue: 60,
  pillars: 3,
  tags: 10,
  tag: 40,
  statement: 1000,
  context: 1000,
} as const;

/** `BW_STORIES_SEED_MAX_CHARS`. */
export const SEED_MAX_CHARS = 60_000;

export interface MetricDraft {
  readonly label: string;
  readonly value: string;
  readonly bucketed: boolean;
  readonly publicSource: string;
}

export interface StoryForm {
  readonly title: string;
  readonly problem: string;
  readonly actions: string;
  readonly decisions: string;
  readonly metrics: readonly MetricDraft[];
  readonly lessons: string;
  readonly pillars: readonly string[];
  readonly confidentiality: Confidentiality;
  readonly tags: readonly string[];
}

export const EMPTY_STORY: StoryForm = {
  title: '',
  problem: '',
  actions: '',
  decisions: '',
  metrics: [],
  lessons: '',
  pillars: [],
  confidentiality: 'anonymized',
  tags: [],
};

export function storyToForm(story: StoryEntry): StoryForm {
  return {
    title: story.title,
    problem: story.problem,
    actions: story.actions,
    decisions: story.decisions,
    metrics: (story.metrics ?? []).map((metric) => ({
      label: metric.label,
      value: metric.value,
      bucketed: metric.bucketed,
      publicSource: metric.publicSource ?? '',
    })),
    lessons: story.lessons,
    pillars: story.pillars ?? [],
    confidentiality: story.confidentiality,
    tags: story.tags ?? [],
  };
}

export function formToStoryIn(form: StoryForm): StoryIn {
  return {
    title: form.title.trim(),
    problem: form.problem.trim(),
    actions: form.actions.trim(),
    decisions: form.decisions.trim(),
    metrics: form.metrics
      .filter((metric) => metric.label.trim() !== '' || metric.value.trim() !== '')
      .map((metric) => ({
        label: metric.label.trim(),
        value: metric.value.trim(),
        bucketed: metric.bucketed,
        publicSource: metric.bucketed || metric.publicSource.trim() === '' ? null : metric.publicSource.trim(),
      })),
    lessons: form.lessons.trim(),
    pillars: [...form.pillars],
    confidentiality: form.confidentiality,
    tags: [...form.tags],
  };
}

/** What `POST/PUT /api/stories` would refuse, by request path (`title`, `metrics.1.value`). */
export function validateStory(form: StoryForm): ErrorMap {
  const errors: Record<string, string> = {};
  const set = (path: string, message: string) => {
    if (!(path in errors)) errors[path] = message;
  };
  if (form.title.trim() === '') set('title', 'Give the story a title.');
  else if (form.title.trim().length > STORY_LIMITS.title) set('title', `At most ${String(STORY_LIMITS.title)} characters.`);
  for (const field of ['problem', 'actions', 'decisions', 'lessons'] as const) {
    if (form[field].trim().length > STORY_LIMITS.text) set(field, `At most ${String(STORY_LIMITS.text)} characters.`);
  }
  if (form.pillars.length === 0) set('pillars', 'Pick at least one pillar.');
  if (form.pillars.length > STORY_LIMITS.pillars) set('pillars', `At most ${String(STORY_LIMITS.pillars)} pillars.`);
  if (form.metrics.length > STORY_LIMITS.metrics) set('metrics', `At most ${String(STORY_LIMITS.metrics)} metrics.`);
  form.metrics.forEach((metric, index) => {
    const base = `metrics.${String(index)}`;
    const label = metric.label.trim();
    const value = metric.value.trim();
    if (label === '' && value === '') return;
    if (label === '') set(`${base}.label`, 'Say what the number measures.');
    if (value === '') set(`${base}.value`, 'Give the value, rounded unless it is public.');
    if (label.length > STORY_LIMITS.metricLabel) set(`${base}.label`, `At most ${String(STORY_LIMITS.metricLabel)} characters.`);
    if (value.length > STORY_LIMITS.metricValue) set(`${base}.value`, `At most ${String(STORY_LIMITS.metricValue)} characters.`);
    if (!metric.bucketed && metric.publicSource.trim() !== '' && !metric.publicSource.trim().startsWith('https://')) {
      set(`${base}.publicSource`, 'Use the public page’s https address.');
    }
  });
  if (form.tags.length > STORY_LIMITS.tags) set('tags', `At most ${String(STORY_LIMITS.tags)} tags.`);
  const repeated = repeatedIndexes(form.tags);
  form.tags.forEach((tag, index) => {
    if (!isSlug(tag, STORY_LIMITS.tag)) set(`tags.${String(index)}`, 'Tags are lower-case words joined by hyphens.');
    else if (repeated.has(index)) set(`tags.${String(index)}`, 'Listed twice.');
  });
  return errors;
}

export interface PovForm {
  readonly pillar: string;
  readonly statement: string;
  readonly context: string;
  readonly confidentiality: Confidentiality;
}

export function povToForm(pov: PovEntry): PovForm {
  return { pillar: pov.pillar, statement: pov.statement, context: pov.context ?? '', confidentiality: pov.confidentiality };
}

/** `PovIn`: the origin is kept by the server from the first save. */
export function formToPovIn(form: PovForm, current?: PovEntry): PovIn {
  const ideaTake = current?.origin === 'idea_take';
  return {
    pillar: form.pillar,
    statement: form.statement.trim(),
    context: form.context.trim() === '' ? null : form.context.trim(),
    confidentiality: form.confidentiality,
    origin: ideaTake ? 'idea_take' : 'owner',
    ideaId: current?.ideaId ?? null,
  };
}

export function validatePov(form: PovForm): ErrorMap {
  const errors: Record<string, string> = {};
  if (form.pillar === '') errors.pillar = 'Pick the pillar this position is about.';
  if (form.statement.trim() === '') errors.statement = 'Write the position.';
  else if (form.statement.trim().length > STORY_LIMITS.statement) errors.statement = `At most ${String(STORY_LIMITS.statement)} characters.`;
  if (form.context.trim().length > STORY_LIMITS.context) errors.context = `At most ${String(STORY_LIMITS.context)} characters.`;
  return errors;
}

/** What the confidentiality classifier said about an entry, for its badge. */
export type Verdict = 'passed' | 'flagged' | 'unchecked';

export function verdictOf(check: ClassifierCheck | undefined): Verdict {
  if (check?.passed === true) return 'passed';
  if (check?.passed === false) return 'flagged';
  return 'unchecked';
}

/** Approved and passed by the classifier: the only entries drafts use. */
export function isUsable(entry: { status: string; classifier?: ClassifierCheck | undefined }): boolean {
  return entry.status === 'approved' && entry.classifier?.passed === true;
}

/** A proposed story or position, for the review queue (newest change first). */
export type QueueEntry =
  | { readonly kind: 'story'; readonly entry: Entry<StoryEntry> }
  | { readonly kind: 'pov'; readonly entry: Entry<PovEntry> };

export function mergeQueue(stories: readonly Entry<StoryEntry>[], povs: readonly Entry<PovEntry>[]): QueueEntry[] {
  const all: QueueEntry[] = [
    ...stories.map((entry) => ({ kind: 'story' as const, entry })),
    ...povs.map((entry) => ({ kind: 'pov' as const, entry })),
  ];
  return all.sort((a, b) => (a.entry.item.updatedAt < b.entry.item.updatedAt ? 1 : a.entry.item.updatedAt > b.entry.item.updatedAt ? -1 : 0));
}

/** Plain text of a story, for the conflict dialog's comparison. */
export function describeStory(form: StoryForm): string {
  return [
    form.title,
    form.problem && `Problem: ${form.problem}`,
    form.actions && `Actions: ${form.actions}`,
    form.decisions && `Decisions and trade-offs: ${form.decisions}`,
    form.metrics.length > 0 && `Metrics: ${form.metrics.map((metric) => `${metric.label} ${metric.value}`).join('; ')}`,
    form.lessons && `Lessons: ${form.lessons}`,
    `Pillars: ${form.pillars.join(', ')}. ${form.confidentiality === 'public' ? 'Public' : 'Anonymized'}.`,
  ]
    .filter(Boolean)
    .join('\n\n');
}

export function describePov(form: PovForm): string {
  return [form.statement, form.context && `Context: ${form.context}`, `Pillar: ${form.pillar}`].filter(Boolean).join('\n\n');
}
