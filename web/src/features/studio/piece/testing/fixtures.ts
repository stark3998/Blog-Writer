import type { components } from '@/lib/api/schema';

/**
 * Server-shaped documents for Studio tests, typed from `schema.ts`. Every builder takes overrides,
 * so a test states only what it is about.
 */

type Schemas = components['schemas'];

export const T0 = '2026-11-27T10:00:00.000000Z';

/** A timestamp `minutes` after T0. */
export function minutesAfter(minutes: number): string {
  return new Date(Date.parse(T0) + minutes * 60_000).toISOString().replace('Z', '000Z');
}

export function versioned<T>(item: T, etag: string | null = '"etag-1"'): { item: T; etag: string | null } {
  return { item, etag };
}

export function page<T>(items: readonly T[], nextCursor: string | null = null, etag = '"etag-1"') {
  return { items: items.map((item) => ({ item, etag })), nextCursor };
}

export function articleDoc(overrides: Partial<Schemas['Article']> = {}): Schemas['Article'] {
  return {
    id: 'art-1',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'planned',
    statusChangedAt: T0,
    purpose: 'flagship',
    updatedBy: 'owner',
    meta: {
      title: 'The MCP threat model nobody ships',
      slug: null,
      excerpt: null,
      pillar: 'agents',
      format: 'deep-explainer',
      tags: [],
      series: null,
    },
    markdown: '',
    version: 0,
    wordCount: 0,
    readingTimeMinutes: 0,
    pipelineRuns: 0,
    reviewOnly: false,
    publish: { live: false, auto: false },
    ...overrides,
  };
}

export function articleSummary(overrides: Partial<Schemas['ArticleSummary']> = {}): Schemas['ArticleSummary'] {
  return {
    id: 'art-1',
    status: 'planned',
    statusChangedAt: T0,
    purpose: 'flagship',
    meta: { title: 'The MCP threat model nobody ships', slug: null, pillar: 'agents', format: 'deep-explainer' },
    version: 0,
    wordCount: 0,
    updatedAt: T0,
    updatedBy: 'owner',
    reviewOnly: false,
    gates: null,
    publish: { live: false, publishAt: null, url: null },
    ideaId: null,
    weekPlanId: null,
    ...overrides,
  };
}

export function postDoc(overrides: Partial<Schemas['Post']> = {}): Schemas['Post'] {
  return {
    id: 'post-1',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'draft',
    statusChangedAt: T0,
    lane: 'quick_take',
    pillar: 'identity',
    format: 'carousel',
    displayText: 'Okta to Entra in 90 days: what broke first\n\nThe rest of the post.',
    payload: '',
    approvalCurrent: false,
    goLiveMode: 'one_click',
    timeSensitive: false,
    reviewOnly: false,
    ...overrides,
  };
}

export function postSummary(overrides: Partial<Schemas['PostSummary']> = {}): Schemas['PostSummary'] {
  return {
    id: 'post-1',
    status: 'draft',
    statusChangedAt: T0,
    lane: 'quick_take',
    pillar: 'identity',
    format: 'carousel',
    hook: 'Okta to Entra in 90 days: what broke first',
    slot: null,
    goLiveMode: 'one_click',
    kitState: null,
    approvalCurrent: false,
    postedAt: null,
    reviewOnly: false,
    articleId: null,
    updatedAt: T0,
    ...overrides,
  };
}

export function servedBy(overrides: Partial<Schemas['ServedByRecord']> = {}): Schemas['ServedByRecord'] {
  return {
    step: 'draft',
    at: T0,
    task: 'draft',
    model: 'claude-opus-5-5',
    deployment: 'claude-opus',
    provider: 'anthropic',
    reviewOnly: false,
    repaired: false,
    refused: false,
    costUsd: 0.42,
    ...overrides,
  };
}

function pillar(id: string, name: string): Schemas['Pillar-Output'] {
  return { id, name, topics: '', description: '', audience: 'Security architects', targetShare: 0.33 };
}

export const PILLAR_SETTINGS: Schemas['PillarSettings'] = {
  id: 'pillars',
  schemaVersion: 1,
  createdAt: T0,
  updatedAt: T0,
  pillars: [
    pillar('agents', 'AI agents & security'),
    pillar('identity', 'Identity modernization'),
    pillar('governance', 'AI governance'),
  ],
};

function pack(id: string, name: string, minWords: number, maxWords: number, full: boolean): Schemas['FormatPack'] {
  return {
    id,
    name,
    minWords,
    maxWords,
    ownerInput: full ? 'full_interview' : 'micro_interview',
    minQuestions: full ? 5 : 2,
    maxQuestions: full ? 8 : 2,
  };
}

export const FORMAT_PACKS: Schemas['FormatPacks'] = {
  id: 'format-packs',
  schemaVersion: 1,
  createdAt: T0,
  updatedAt: T0,
  packs: [
    pack('case-study', 'Case study / how I did it', 1500, 2500, true),
    pack('framework', 'Framework / checklist', 1200, 2000, true),
    pack('deep-explainer', 'Deep explainer', 1800, 3000, false),
    pack('news-analysis', 'News analysis', 900, 1500, false),
  ],
};

/** The machines of contract section 2 the Studio reads (`GET /api/meta/machines`). */
export const MACHINES: Record<string, Record<string, string[]>> = {
  article: {
    planned: ['researching', 'interviewing', 'drafting', 'in_review', 'archived'],
    researching: ['interviewing', 'drafting', 'needs_attention', 'archived'],
    interviewing: ['drafting', 'needs_attention', 'archived'],
    drafting: ['gating', 'in_review', 'needs_attention', 'archived'],
    gating: ['in_review', 'approved', 'needs_attention'],
    in_review: ['researching', 'interviewing', 'drafting', 'gating', 'approved', 'archived'],
    approved: ['in_review', 'publishing'],
    publishing: ['published', 'publish_failed'],
    publish_failed: ['publishing', 'unpublishing', 'in_review'],
    published: ['in_review', 'publishing', 'unpublishing'],
    unpublishing: ['unpublished', 'publish_failed'],
    unpublished: ['in_review', 'archived'],
    needs_attention: ['researching', 'interviewing', 'drafting', 'in_review', 'archived'],
    archived: ['planned'],
  },
  post: {
    draft: ['generating', 'approved', 'archived', 'expired'],
    generating: ['draft', 'needs_attention', 'archived'],
    approved: ['draft', 'staged', 'publishing', 'kit', 'missed', 'expired', 'archived'],
    staged: ['draft', 'approved', 'publishing', 'kit', 'missed', 'expired'],
    publishing: ['posted', 'approved', 'needs_attention'],
    needs_attention: ['posted', 'approved', 'kit', 'draft', 'archived'],
    kit: ['posted', 'approved', 'missed', 'expired', 'archived'],
    missed: ['approved', 'kit', 'expired', 'archived'],
    posted: ['deleted'],
    expired: ['draft', 'archived'],
    archived: ['draft'],
    deleted: [],
  },
  research_run: {
    queued: ['running', 'cancelled'],
    running: ['succeeded', 'failed', 'quarantined', 'cancelled'],
    failed: ['queued'],
    succeeded: [],
    quarantined: [],
    cancelled: [],
  },
};

export function ideaDoc(overrides: Partial<Schemas['Idea']> = {}): Schemas['Idea'] {
  return {
    id: 'idea-1',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'new',
    statusChangedAt: T0,
    origin: 'feed',
    title: 'Entra adds passkey sync for workforce accounts',
    pillar: 'identity',
    suggestedLane: 'flagship',
    suggestedFormat: 'news-analysis',
    angle: 'What it changes for an Okta-to-Entra migration',
    timeSensitive: false,
    ...overrides,
  };
}

export function povDoc(overrides: Partial<Schemas['PovEntry']> = {}): Schemas['PovEntry'] {
  return {
    id: 'pov-1',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'approved',
    statusChangedAt: T0,
    origin: 'idea_take',
    pillar: 'identity',
    statement: 'Passkey sync is a convenience feature until you can revoke a synced key centrally.',
    confidentiality: 'anonymized',
    ideaId: 'idea-1',
    classifier: { passed: true },
    ...overrides,
  };
}
