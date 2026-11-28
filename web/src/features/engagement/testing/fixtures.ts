import type { components } from '@/lib/api/schema';

type Schemas = components['schemas'];
export type EngagementItem = Schemas['EngagementItem'];
export type ItemEntry = Schemas['Versioned_EngagementItem_'];
export type WatchEntry = Schemas['WatchEntry'];
export type WatchEntryVersioned = Schemas['Versioned_WatchEntry_'];
export type EngagementRecord = Schemas['EngagementRecord'];
export type Angle = Schemas['Angle'];
export type Finding = Schemas['Finding'];

const HOUR = 60 * 60 * 1000;

function iso(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

export const THEIR_COMMENT =
  'How do you stop agents from sharing one service principal? We tried scoped tokens and still saw drift.';

export function item(overrides: Partial<EngagementItem> = {}): EngagementItem {
  return {
    id: 'eng-0001',
    schemaVersion: 1,
    createdAt: iso(-17 * HOUR),
    updatedAt: iso(-17 * HOUR),
    expiresAt: iso(31 * HOUR),
    itemKind: 'reply',
    status: 'open',
    statusChangedAt: iso(-17 * HOUR),
    postId: 'post-9',
    targetUrl: 'https://www.linkedin.com/feed/update/urn:li:activity:1/',
    pastedText: THEIR_COMMENT,
    angles: [],
    servedBy: [],
    ...overrides,
  };
}

export function itemEntry(overrides: Partial<EngagementItem> = {}, etag = '"eng-1"'): ItemEntry {
  return { item: item(overrides), etag };
}

export const ANGLES: Angle[] = [
  {
    id: 'a1',
    text: 'We gave every agent its own workload identity and expired its token after each task. Drift stopped once nothing outlived the job.',
    groundedIn: ['story-0001'],
  },
  {
    id: 'a2',
    text: 'Scoped tokens fix the blast radius, not the ownership question. Who approves a new agent is the part we had to change.',
    groundedIn: ['pov-0001', 'brief-0001'],
  },
];

export function watchEntry(overrides: Partial<WatchEntry> = {}, etag = '"watch-1"'): WatchEntryVersioned {
  return {
    item: {
      id: 'watch-0001',
      schemaVersion: 1,
      createdAt: iso(-10 * 24 * HOUR),
      updatedAt: iso(-10 * 24 * HOUR),
      name: 'Dana Reyes',
      profileUrl: 'https://www.linkedin.com/in/dana-reyes',
      pillar: 'identity',
      note: 'Writes about CIAM migrations',
      lastEngagedAt: null,
      active: true,
      ...overrides,
    },
    etag,
  };
}

export function record(overrides: Partial<EngagementRecord> = {}): EngagementRecord {
  return {
    id: 'rec-0001',
    schemaVersion: 1,
    createdAt: iso(-2 * HOUR),
    updatedAt: iso(-2 * HOUR),
    itemKind: 'comment',
    finalText: 'We moved approvals for new agents into the same review as new apps. It cut the sprawl in half.',
    url: 'https://www.linkedin.com/feed/update/urn:li:activity:2/',
    postedAt: iso(-2 * HOUR),
    substantive: true,
    ...overrides,
  };
}
