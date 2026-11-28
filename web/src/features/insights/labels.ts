import type { components } from '@/lib/api/schema';

/** Owner-facing names for the insight dimensions, their values and the metrics. */

type Schemas = components['schemas'];
export type InsightDimension = Schemas['InsightDimension'];
export type RateKey = keyof Schemas['MetricRates'];

export const DIMENSIONS: readonly { value: InsightDimension; label: string }[] = [
  { value: 'pillar', label: 'Pillar' },
  { value: 'format', label: 'Format' },
  { value: 'hook_type', label: 'Hook' },
  { value: 'lane', label: 'Lane' },
  { value: 'go_live_mode', label: 'Go-live' },
  { value: 'owner_signal', label: 'Your story' },
];

export function dimensionLabel(dimension: InsightDimension): string {
  return DIMENSIONS.find((item) => item.value === dimension)?.label ?? dimension;
}

export const RATE_KEYS: readonly { value: RateKey; label: string; unit: string }[] = [
  { value: 'engagement', label: 'All engagement', unit: 'engagements' },
  { value: 'reactions', label: 'Reactions', unit: 'reactions' },
  { value: 'comments', label: 'Comments', unit: 'comments' },
  { value: 'reposts', label: 'Reposts', unit: 'reposts' },
  { value: 'saves', label: 'Saves', unit: 'saves' },
  { value: 'sends', label: 'Sends', unit: 'sends' },
];

export function rateLabel(key: RateKey): string {
  return RATE_KEYS.find((item) => item.value === key)?.label ?? key;
}

export function rateUnit(key: RateKey): string {
  return RATE_KEYS.find((item) => item.value === key)?.unit ?? key;
}

const VALUE_LABELS: Readonly<Record<string, Readonly<Record<string, string>>>> = {
  format: { carousel: 'Carousel', text: 'Text', image_card: 'Image card', poll: 'Poll' },
  hook_type: {
    result_metric: 'Result or metric',
    mistake_lesson: 'Mistake and lesson',
    decision_tradeoff: 'Decision and trade-off',
    counter_evidence: 'Counter-evidence',
    checklist_promise: 'Checklist promise',
    story_in_motion: 'Story in motion',
    question: 'Question',
    none: 'No hook type',
  },
  lane: { derivative: 'From an article', native: 'Native', quick_take: 'Quick take', repurpose: 'Repurposed' },
  go_live_mode: { one_click: 'One tap', kit: 'Ready-to-post kit' },
  owner_signal: { with: 'With your story or take', without: 'Without' },
};

/** A dimension value in words (`pillars` gives pillar names). */
export function valueLabel(
  dimension: InsightDimension,
  value: string,
  pillars: ReadonlyMap<string, string> = new Map(),
): string {
  if (dimension === 'pillar') return pillars.get(value) ?? value;
  return VALUE_LABELS[dimension]?.[value] ?? value.replace(/_/g, ' ');
}

/** The experiment questions (`analytics.experiments.ExperimentQuestion`) with their usual arms. */
export const QUESTIONS: readonly {
  value: Schemas['Experiment']['question'];
  label: string;
  arms: readonly [string, string];
}[] = [
  { value: 'links_vs_none', label: 'Links against no links', arms: ['No link', 'One link'] },
  { value: 'slot_timing', label: 'Slot timing', arms: ['Morning slot', 'Midday slot'] },
  { value: 'carousel_vs_image_card', label: 'Carousel against image card', arms: ['Carousel', 'Image card'] },
  { value: 'custom', label: 'Something else', arms: ['A', 'B'] },
];

export function questionLabel(question: string): string {
  return QUESTIONS.find((item) => item.value === question)?.label ?? question;
}

export const METRICS: readonly { value: Schemas['Experiment']['metric']; label: string }[] = [
  { value: 'engagement_rate', label: 'All engagement per 1K impressions' },
  { value: 'reactions', label: 'Reactions per 1K impressions' },
  { value: 'comments', label: 'Comments per 1K impressions' },
  { value: 'saves', label: 'Saves per 1K impressions' },
  { value: 'sends', label: 'Sends per 1K impressions' },
  { value: 'impressions', label: 'Impressions' },
];

export function metricLabel(metric: string): string {
  return METRICS.find((item) => item.value === metric)?.label ?? metric;
}

/** The AI tasks, as the spend breakdown names them. */
const TASK_LABELS: Readonly<Record<string, string>> = {
  draft: 'Drafting',
  revise: 'Revisions',
  outline: 'Outlines',
  plan: 'Planning',
  interview: 'Interview questions',
  synthesis: 'Synthesis',
  critique: 'Critique',
  fact_check: 'Fact checks',
  anonymize: 'Anonymizing',
  research: 'Research',
  quick_search: 'Quick search',
  triage: 'Triage',
  engagement: 'Engagement',
  embed: 'Embeddings',
  image: 'Images',
};

export function taskLabel(task: string): string {
  return TASK_LABELS[task] ?? task.replace(/_/g, ' ');
}
