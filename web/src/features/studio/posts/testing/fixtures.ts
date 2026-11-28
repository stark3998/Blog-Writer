import type { components } from '@/lib/api/schema';

import { CONTRACT_KIT_MACHINE, CONTRACT_POST_MACHINE } from '../model/machines';
import { foldIndex } from '../model/text';
import type { Article, GoLiveView, HookVariant, KitView, MediaRef, Post, PostSummary, Versioned } from '../model/types';

/**
 * Server-shaped fixtures for the Posts and Go-live tabs, typed from `schema.ts`. Ids follow the
 * server's shapes (`post-<28 hex>`, `art-<28 hex>`); times are fixed ISO-8601 UTC.
 */

type Schemas = components['schemas'];

export const POST_ID = 'post-0193a1b2c3d4e5f60718293a4b5c';
export const SECOND_POST_ID = 'post-0193a1b2c3d4e5f60718293a4b5d';
export const ARTICLE_ID = 'art-0193a1b2c3d4e5f60718293a4b5c';
export const SHA256 = '9b74c9897bac770ffc029102a200c5de9b74c9897bac770ffc029102a200c5de';
export const CONFIRM_TOKEN = `v1.1764250200.${'Q'.repeat(43)}`;
export const T0 = '2026-11-27T13:00:00.000000Z';
export const SLOT_UTC = '2030-12-03T14:00:00.000000Z';

export const HOOKS: HookVariant[] = [
  {
    id: 'h1',
    text: 'We moved 2,000 apps off Okta in 14 months without a single sign-in outage.',
    hookType: 'result_metric',
    concreteDetail: '2,000 apps in 14 months',
    usesOwnerWording: true,
  },
  {
    id: 'h2',
    text: 'Our first migration wave broke single sign-on for 40 apps. Here is what we changed.',
    hookType: 'mistake_lesson',
    concreteDetail: '40 apps in wave one',
    usesOwnerWording: false,
  },
  {
    id: 'h3',
    text: 'We migrated by risk tier, not by business unit.',
    hookType: 'decision_tradeoff',
    concreteDetail: 'risk tiers before org charts',
    usesOwnerWording: false,
  },
];

export const BODY =
  'The trick was boring: an inventory first, then waves of 50 apps, each with a rollback plan.\n\nThree things mattered most: an owner for every app, a cutover window per wave, and one dashboard everyone trusted.';

export const DISPLAY_TEXT = `${HOOKS[0]?.text ?? ''}\n\n${BODY}\n\n#IdentityModernization #Entra`;

const CLEAN_CHECK = { ranAt: T0, contentSha256: SHA256, passed: true, findings: [], truncated: 0 };

export function makePost(overrides: Partial<Post> = {}): Post {
  return {
    id: POST_ID,
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'draft',
    statusChangedAt: T0,
    lane: 'derivative',
    pillar: 'identity',
    format: 'text',
    hookType: 'result_metric',
    hookVariants: HOOKS,
    selectedHookId: 'h1',
    displayText: DISPLAY_TEXT,
    payload: DISPLAY_TEXT,
    hashtags: ['IdentityModernization', 'Entra'],
    media: [],
    attributions: [],
    storyIds: [],
    povIds: [],
    articleId: ARTICLE_ID,
    goLiveMode: 'one_click',
    timeSensitive: false,
    lint: CLEAN_CHECK,
    derivedClaims: CLEAN_CHECK,
    nearDuplicate: CLEAN_CHECK,
    snapshots: [],
    servedBy: [],
    reviewOnly: false,
    reviewOnlyReasons: [],
    approvalCurrent: Boolean(overrides.approval),
    ...overrides,
  };
}

/** A post approved for a slot, as the go-live page sees it. */
export function approvedPost(overrides: Partial<Post> = {}): Post {
  return makePost({
    status: 'approved',
    approval: { sha256: SHA256, approvedAt: '2026-11-27T12:00:00.000000Z' },
    slot: { localDate: '2030-12-03', localTime: '09:00', timezone: 'America/New_York', utc: SLOT_UTC, kind: 'primary' },
    ...overrides,
  });
}

export function versioned<T>(item: T, etag: string | null = '"1"'): Versioned<T> {
  return { item, etag };
}

export function summaryOf(post: Post): PostSummary {
  return {
    id: post.id,
    status: post.status,
    statusChangedAt: post.statusChangedAt,
    lane: post.lane,
    pillar: post.pillar,
    format: post.format,
    hook: post.displayText.split('\n')[0] ?? '',
    slot: post.slot ?? null,
    goLiveMode: post.goLiveMode,
    kitState: post.kit?.state ?? null,
    approvalCurrent: Boolean(post.approval),
    postedAt: post.postedAt ?? null,
    reviewOnly: post.reviewOnly,
    articleId: post.articleId ?? null,
    updatedAt: post.updatedAt,
  };
}

export function makeArticle(overrides: Partial<Article> = {}): Article {
  return {
    id: ARTICLE_ID,
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'published',
    statusChangedAt: T0,
    purpose: 'flagship',
    updatedBy: 'owner',
    markdown: '',
    ...overrides,
  } as Article;
}

export const PILLARS: Versioned<Schemas['PillarSettings']> = versioned(
  {
    id: 'pillars',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    pillars: [
      {
        id: 'agents',
        name: 'AI agents & security',
        topics: '',
        description: '',
        audience: '',
        hashtags: ['AISecurity', 'AIAgents', 'MCP'],
        targetShare: 0.33,
      },
      {
        id: 'identity',
        name: 'Identity modernization',
        topics: '',
        description: '',
        audience: '',
        hashtags: ['IdentityModernization', 'Entra', 'CIAM'],
        targetShare: 0.33,
      },
    ],
  },
  '"pillars-1"',
);

export const BRAND: Versioned<Schemas['BrandKit']> = versioned(
  {
    id: 'brand-kit',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    authorName: 'Alex Rivera',
    authorHeadline: 'Identity architect',
    font: 'inter',
  },
  '"brand-1"',
);

export const CADENCE = versioned({ id: 'cadence', schemaVersion: 1, createdAt: T0, updatedAt: T0, timezone: 'America/New_York' }, '"c1"');

export const MACHINES: Record<string, Record<string, string[]>> = {
  post: Object.fromEntries(Object.entries(CONTRACT_POST_MACHINE).map(([state, targets]) => [state, [...targets]])),
  kit: Object.fromEntries(Object.entries(CONTRACT_KIT_MACHINE).map(([state, targets]) => [state, [...targets]])),
};

/** A rendered carousel's two files. */
export const CAROUSEL_MEDIA: MediaRef[] = [
  {
    role: 'carousel_pdf',
    container: 'media',
    name: `posts/${POST_ID}/${SHA256}.pdf`,
    sha256: SHA256,
    contentType: 'application/pdf',
    sizeBytes: 2_400_000,
    pageCount: 9,
    filename: 'carousel.pdf',
  },
  {
    role: 'carousel_cover',
    container: 'media',
    name: `posts/${POST_ID}/${SHA256.slice(0, 63)}0.png`,
    sha256: `${SHA256.slice(0, 63)}0`,
    contentType: 'image/png',
    sizeBytes: 180_000,
    width: 1080,
    height: 1350,
    altText: '2,000 apps, zero outages',
    filename: 'carousel-cover.png',
  },
];

/** A valid 9-slide deck as the server stores it. */
export const CAROUSEL_SPEC: Record<string, unknown> = {
  title: '2,000 apps off Okta: the wave plan',
  lang: 'en',
  slides: [
    { type: 'cover', hook: '2,000 apps, zero sign-in outages', kicker: 'Identity modernization' },
    { type: 'statement', statement: 'Inventory first. Everything else follows from it.' },
    { type: 'list', title: 'What every wave had', items: ['An owner per app', 'A cutover window', 'A rollback plan'] },
    {
      type: 'steps',
      title: 'One wave, start to end',
      steps: [{ title: 'Inventory' }, { title: 'Test sign-in', detail: 'With the app owner watching' }],
    },
    { type: 'stat', value: '2,000', label: 'apps moved in 14 months' },
    {
      type: 'framework',
      title: 'Risk tiers',
      layout: '2x2',
      cells: [
        { label: 'Low', text: 'Move first' },
        { label: 'Medium', text: 'Move with a window' },
        { label: 'High', text: 'Move with a rehearsal' },
        { label: 'Critical', text: 'Move last' },
      ],
    },
    { type: 'quote', quote: 'Nobody noticed the cutover.', attribution: 'An app owner' },
    { type: 'recap', title: 'Do this Monday', items: ['Start the inventory', 'Name an owner per app'] },
    { type: 'sources', title: 'Sources', sources: [{ publisher: 'Microsoft Learn', title: 'Plan an Entra migration' }] },
  ],
};

export function goLiveView(post: Post, etag: string | null = '"1"', overrides: Partial<GoLiveView> = {}): GoLiveView {
  const approved = post.approval !== null && post.approval !== undefined;
  return {
    post: { item: post, etag },
    foldIndex: foldIndex(post.displayText),
    cover: null,
    documentTitle: post.documentTitle ?? null,
    altTexts: [],
    approvalSha256: post.approval?.sha256 ?? null,
    approvalCurrent: approved,
    connection: { status: 'connected', tokenDay: 12 },
    confirmToken: approved ? CONFIRM_TOKEN : null,
    confirmExpiresAt: approved ? '2030-12-03T13:10:00.000000Z' : null,
    canPublish: approved && (post.status === 'approved' || post.status === 'staged'),
    reasons: [],
    ...overrides,
  };
}

export const CHECKLIST = [
  'Start a post in LinkedIn and paste the text exactly as copied.',
  'Add a document, upload the PDF and paste the document title.',
  'Check the first three lines in the preview before you post or schedule.',
  'Post it or schedule it in LinkedIn, then mark it here with the post’s link.',
];

export function kitView(post: Post, overrides: Partial<KitView> = {}): KitView {
  return {
    displayText: post.displayText,
    documentTitle: post.documentTitle ?? null,
    altTexts: [],
    downloads: [],
    checklist: CHECKLIST,
    openLinkedInUrl: 'https://www.linkedin.com/feed/',
    state: post.kit?.state ?? null,
    scheduledAt: post.kit?.scheduledAt ?? null,
    ...overrides,
  };
}
