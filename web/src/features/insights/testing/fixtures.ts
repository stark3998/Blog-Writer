import type { components } from '@/lib/api/schema';

import { hoursFrom } from '../../settings/testing/fixtures';

/** Server-shaped documents for the Insights tests, typed from the generated schema. */

type S = components['schemas'];

const rates = (engagement: number | null, extra: Partial<S['MetricRates']> = {}): S['MetricRates'] => ({
  reactions: engagement === null ? null : engagement * 0.6,
  comments: engagement === null ? null : engagement * 0.2,
  reposts: null,
  saves: engagement === null ? null : engagement * 0.1,
  sends: null,
  engagement,
  ...extra,
});

export function overview(overrides: Partial<S['InsightsOverview']> = {}): S['InsightsOverview'] {
  return {
    weeks: 12,
    since: hoursFrom(-24 * 84),
    posts: 9,
    ownerMedian: rates(40),
    rows: [
      { dimension: 'pillar', value: 'agents', posts: 5, ratesPer1k: rates(50), vsMedian: rates(0.25) },
      { dimension: 'pillar', value: 'identity', posts: 3, ratesPer1k: rates(32), vsMedian: rates(-0.2) },
      { dimension: 'pillar', value: 'governance', posts: 1, ratesPer1k: rates(40), vsMedian: rates(0) },
      { dimension: 'format', value: 'carousel', posts: 4, ratesPer1k: rates(56), vsMedian: rates(0.4) },
      { dimension: 'format', value: 'text', posts: 5, ratesPer1k: rates(30), vsMedian: rates(-0.25) },
    ],
    ...overrides,
  };
}

export function postSummary(overrides: Partial<S['PostSummary']> = {}): S['Versioned_PostSummary_'] {
  const item: S['PostSummary'] = {
    id: 'post-1',
    status: 'posted',
    statusChangedAt: hoursFrom(-48),
    lane: 'native',
    pillar: 'agents',
    format: 'text',
    hook: 'We shipped an MCP server without a token store',
    slot: null,
    goLiveMode: 'one_click',
    kitState: null,
    approvalCurrent: true,
    postedAt: hoursFrom(-48),
    reviewOnly: false,
    articleId: null,
    updatedAt: hoursFrom(-48),
    ...overrides,
  };
  return { item, etag: `"${item.id}-summary"` };
}

export function post(overrides: Partial<S['Post']> = {}, etag = '"post-1-v3"'): S['Versioned_Post_'] {
  return {
    item: {
      id: 'post-1',
      schemaVersion: 1,
      createdAt: hoursFrom(-96),
      updatedAt: hoursFrom(-48),
      status: 'posted',
      statusChangedAt: hoursFrom(-48),
      lane: 'native',
      pillar: 'agents',
      format: 'text',
      displayText: 'We shipped an MCP server without a token store\n\nHere is what broke.',
      approvalCurrent: false,
      payload: 'We shipped an MCP server without a token store\n\nHere is what broke.',
      goLiveMode: 'one_click',
      timeSensitive: false,
      reviewOnly: false,
      postUrl: 'https://www.linkedin.com/feed/update/urn:li:share:1/',
      postedAt: hoursFrom(-48),
      snapshots: [],
      ...overrides,
    },
    etag,
  };
}

export function experiment(overrides: Partial<S['Experiment']> = {}): S['Versioned_Experiment_'] {
  const item: S['Experiment'] = {
    id: 'exp-1',
    schemaVersion: 1,
    createdAt: hoursFrom(-24 * 20),
    updatedAt: hoursFrom(-24 * 2),
    status: 'running',
    statusChangedAt: hoursFrom(-24 * 14),
    question: 'links_vs_none',
    hypothesis: 'A link in the post costs a third of its reach.',
    metric: 'engagement_rate',
    minPostsPerArm: 8,
    arms: [
      { id: 'no-link', label: 'No link', postIds: ['post-1', 'post-2'] },
      { id: 'one-link', label: 'One link', postIds: ['post-3'] },
    ],
    startedAt: hoursFrom(-24 * 14),
    concludedAt: null,
    conclusion: null,
    ...overrides,
  };
  return { item, etag: `"${item.id}-1"` };
}

export function suggestion(overrides: Partial<S['InsightSuggestion']> = {}): S['Versioned_InsightSuggestion_'] {
  const item: S['InsightSuggestion'] = {
    id: 'sug-1',
    schemaVersion: 1,
    createdAt: hoursFrom(-30),
    updatedAt: hoursFrom(-30),
    status: 'proposed',
    statusChangedAt: hoursFrom(-30),
    weekStart: '2026-11-23',
    dimension: 'format',
    text: 'Try one more carousel next week: they draw 40% more engagement than your median.',
    evidence: 'Carousels: 56 per 1K over 4 posts; your median is 40.',
    servedBy: null,
    decidedAt: null,
    value: 'carousel',
    ...overrides,
  };
  return { item, etag: `"${item.id}-1"` };
}

export function spendView(overrides: Partial<S['SpendView']> = {}): S['SpendView'] {
  return {
    days: [
      { date: '2026-11-25', costUsd: 2.5, byTask: { draft: 2, triage: 0.5 }, byModel: { 'claude-opus-5-5': 2, 'gpt-6-luna': 0.5 } },
      { date: '2026-11-26', costUsd: 3.1, byTask: { research: 3.1 }, byModel: { 'gpt-6.2-sol': 3.1 } },
      { date: '2026-11-27', costUsd: 4.12, byTask: { draft: 4.12 }, byModel: { 'claude-opus-5-5': 4.12 } },
    ],
    total: 9.72,
    trailingMedian: 2.8,
    brakeThreshold: 20,
    fallbackRate: 0.042,
    reviewOnlyRate: 0.1,
    timezone: 'America/New_York',
    ...overrides,
  };
}
