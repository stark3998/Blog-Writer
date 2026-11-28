import type { components } from '@/lib/api/schema';

/** Server-shaped documents for the Settings tests, typed from the generated schema. */

type S = components['schemas'];

export const NOW = '2026-11-27T15:00:00.000000Z';
export const NOW_MS = Date.parse(NOW);

/** `NOW` moved by `hours` (negative: earlier). */
export function hoursFrom(hours: number): string {
  return new Date(NOW_MS + hours * 3_600_000).toISOString().replace('Z', '000Z');
}

const doc = (id: string) => ({ id, schemaVersion: 1, createdAt: hoursFrom(-24 * 30), updatedAt: hoursFrom(-24) });

const service = (state: S['ServiceState'], reason: string | null = null): S['ServiceView'] => ({ state, reason });

export function systemStatus(overrides: Partial<S['SystemStatus']> = {}): S['SystemStatus'] {
  return {
    safeMode: false,
    boot: { boots: 1, safeMode: false, reason: null },
    runner: { running: true, claimed: 1, schedulerRunning: true },
    schedules: { state: 'current', reason: null, seededAt: hoursFrom(-2), retrying: false },
    brake: { spendTodayUsd: 4.12, thresholdUsd: 20, engaged: false },
    services: {
      ai: service('on'),
      storage: service('on'),
      render: service('degraded', 'chromium_missing'),
      fetcher: service('on'),
      webiq: service('degraded', 'circuit_open'),
      vault: service('on'),
      push: service('on'),
      portfolio: service('on'),
      embeddings: service('on'),
      linkedin: service('on'),
    },
    expiry: { linkedinTokenDay: 12, patExpiresAt: null },
    buildSha: '0123456789abcdef0123456789abcdef01234567',
    ...overrides,
  };
}

export function modelsView(overrides: Partial<S['ModelsView']> = {}): S['ModelsView'] {
  return {
    preferences: {
      item: {
        ...doc('model-preferences'),
        imagesEnabled: false,
        noveltyMaxSimilarity: 0.92,
        nearDuplicateMaxSimilarity: 0.5,
      },
      etag: '"prefs-1"',
    },
    routes: [
      {
        task: 'draft',
        maxClass: 'anonymized',
        tools: false,
        chain: [
          { model: 'claude-opus-5-5', deployment: 'opus', provider: 'anthropic', effort: 'high' },
          { model: 'gpt-6.2-sol', deployment: 'sol', provider: 'openai', effort: null },
        ],
      },
      {
        task: 'research',
        maxClass: 'public',
        tools: true,
        chain: [{ model: 'gpt-6.2-sol', deployment: 'sol', provider: 'openai', effort: 'medium' }],
      },
    ],
    prices: [
      { model: 'claude-opus-5-5', input: 15, output: 75, cachedInput: 1.5, cacheWrite: 18.75 },
      { model: 'gpt-6.2-sol', input: 2.5, output: 10, cachedInput: null, cacheWrite: null },
    ],
    webSearchUsdPer1k: 14,
    ...overrides,
  };
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

export function cadence(etag: string | null = '"cadence-1"'): S['Versioned_CadenceSettings_'] {
  return {
    item: {
      ...doc('cadence'),
      timezone: 'America/New_York',
      planWeek: { day: 'sun', time: '18:00' },
      planLeadMinutes: 60,
      researchStart: { day: 'mon', time: '08:00' },
      interviewDue: { day: 'wed', time: '18:00' },
      draftDay: { day: 'thu', time: '09:00' },
      vetoNotice: { day: 'fri', time: '12:00' },
      articlePublish: { day: 'mon', time: '09:00' },
      minVetoLeadHours: 48,
      businessHoursStart: '09:00',
      businessHoursEnd: '17:00',
      postSlots: [
        { day: 'tue', time: '10:00', kind: 'primary', windowMinutes: 120 },
        { day: 'wed', time: '10:00', kind: 'primary', windowMinutes: 120 },
        { day: 'thu', time: '10:00', kind: 'primary', windowMinutes: 120 },
        { day: 'mon', time: '12:00', kind: 'optional', windowMinutes: 120 },
        { day: 'fri', time: '15:00', kind: 'optional', windowMinutes: 120 },
      ],
      postsPerWeek: { normal: 3, light: 2, away: 0, max: 5 },
      sameDayBlock: true,
      minGapHoursWarn: 24,
      missedSlotGraceMinutes: 120,
      stageLeadMinutes: 60,
      reminders: { replyMinutes: [15, 60, 180], snapshotHours: [48, 168] },
      crawlIntervalHours: 6,
      scout: { day: 'sun', time: '15:00' },
      digest: { day: 'mon', time: '08:00' },
      backupTime: '03:30',
      defaultMode: 'normal',
    },
    etag,
  };
}

export function schedule(overrides: Partial<S['ScheduleView']> = {}): S['ScheduleView'] {
  return {
    id: 'sched-signals-crawl',
    name: 'Crawl feeds and triage new items',
    jobType: 'signals.crawl',
    rule: { kind: 'interval', seconds: 21_600, anchor: null, at: null, days: null },
    timezone: 'America/New_York',
    status: 'active',
    nextRunAt: hoursFrom(3),
    pendingRunAt: null,
    lastRunAt: hoursFrom(-3),
    skippedRuns: 0,
    ...overrides,
  };
}

export function schedules(): S['ScheduleView'][] {
  return [
    schedule(),
    schedule({
      id: 'sched-backups-dump',
      name: 'Daily backup of the durable containers',
      jobType: 'backups.dump',
      rule: { kind: 'daily', at: '03:30', days: null, seconds: null, anchor: null },
      status: 'needs_confirmation',
      pendingRunAt: hoursFrom(-12),
    }),
    schedule({
      id: 'sched-voice-digest',
      name: 'Voice digest',
      jobType: 'voice.digest',
      rule: { kind: 'weekly', at: '08:00', days: ['mon'], seconds: null, anchor: null },
      status: 'paused',
    }),
  ];
}

export function pillars(): S['Versioned_PillarSettings_'] {
  const pillar = (id: string, name: string) => ({
    id,
    name,
    topics: '',
    description: '',
    audience: '',
    vocabulary: [],
    renames: {},
    hashtags: [],
    researchDomains: [],
    targetShare: 0.33,
  });
  return {
    item: {
      ...doc('pillars'),
      pillars: [
        pillar('agents', 'AI agents & security'),
        pillar('identity', 'Identity modernization'),
        pillar('governance', 'AI governance'),
      ],
    },
    etag: '"pillars-1"',
  };
}

export function formats(): S['Versioned_FormatPacks_'] {
  const pack = (id: string, name: string) => ({
    id,
    name,
    minWords: 800,
    maxWords: 2000,
    mustContain: [],
    ownerInput: 'micro_interview' as const,
    minQuestions: 2,
    maxQuestions: 4,
    banned: [],
  });
  return {
    item: {
      ...doc('format-packs'),
      packs: [pack('case-study', 'Case study'), pack('framework', 'Framework'), pack('explainer', 'Deep explainer'), pack('news', 'News analysis')],
    },
    etag: '"formats-1"',
  };
}

export function source(overrides: Partial<S['Source']> = {}): S['Versioned_Source_'] {
  const item: S['Source'] = {
    ...doc('feed-0001'),
    type: 'feed',
    title: 'Identity weekly',
    url: 'https://news.example.com/feed.xml',
    siteUrl: 'https://news.example.com',
    query: null,
    pillar: 'identity',
    status: 'active',
    statusChangedAt: hoursFrom(-200),
    lastCrawledAt: hoursFrom(-2),
    lastSuccessAt: hoursFrom(-2),
    consecutiveFailures: 0,
    lastError: null,
    itemsSeen: 132,
    ...overrides,
  };
  return { item, etag: `"${item.id}-1"` };
}

export function connection(overrides: Partial<S['ConnectionView']> = {}): S['ConnectionView'] {
  return {
    status: 'connected',
    memberUrn: 'urn:li:person:abc123',
    connectedAt: hoursFrom(-24 * 12),
    expiresAt: hoursFrom(24 * 48),
    tokenDay: 12,
    bannerDue: false,
    lastIntrospectedAt: hoursFrom(-6),
    lastError: null,
    scopes: ['openid', 'profile', 'w_member_social'],
    configured: true,
    apiVersion: '202611',
    ...overrides,
  };
}

export function profileChecklist(etag: string | null = '"checklist-1"'): S['Versioned_ProfileChecklist_'] {
  return {
    item: {
      ...doc('profile-checklist'),
      items: [
        { id: 'headline', label: 'Headline names your three pillars', done: true, doneAt: hoursFrom(-48), note: null },
        { id: 'about', label: 'About section matches the pillars and your voice', done: false, doneAt: null, note: null },
        { id: 'featured', label: 'Featured section shows your best flagship pieces', done: false, doneAt: null, note: null },
      ],
    },
    etag,
  };
}

export function blocklist(terms: S['BlocklistTerm'][] = [], configured = true): S['Versioned_Blocklist_'] {
  return { item: { ...doc('blocklist'), terms, configured }, etag: configured ? '"blocklist-1"' : null };
}

export function policy(overrides: Partial<S['EmployerPolicy']> = {}, etag: string | null = '"policy-1"'): S['Versioned_EmployerPolicy_'] {
  return {
    item: {
      ...doc('employer-policy'),
      disclaimerShort: 'Views are my own.',
      disclaimerLong: null,
      forbiddenTopics: ['Unreleased products'],
      noEmployerBranding: true,
      aiUseConfirmed: false,
      aiUseConfirmedAt: null,
      clearanceStatement: 'I confirm this piece is anonymized and cleared for publication.',
      configured: true,
      ...overrides,
    },
    etag,
  };
}

export function autonomyView(
  overrides: Partial<S['AutonomySettings']> = {},
  decision: S['DecisionView'] = { allowed: false, reasons: ['not_enabled', 'calibrating', 'judge_uncalibrated'] },
): S['AutonomyView'] {
  return {
    settings: {
      item: {
        ...doc('autonomy'),
        autoPublishEnabled: false,
        paused: false,
        pausedAt: null,
        calibration: {
          cleanStreak: 2,
          required: 6,
          events: [
            { articleId: 'art-1', at: hoursFrom(-200), outcome: 'clean' },
            { articleId: 'art-2', at: hoursFrom(-100), outcome: 'clean' },
          ],
        },
        judge: { asIsRate: null, sampleSize: 0, checkedAt: null, threshold: 0.9, maxAgeDays: 31 },
        lastCorrectionAt: null,
        ...overrides,
      },
      etag: '"autonomy-1"',
    },
    decision,
  };
}

export function ownerInputs(): S['OwnerInputs'] {
  return {
    items: [
      { code: 'linkedin_samples', label: 'Past LinkedIn posts you wrote by hand', done: false, have: 2, need: 5, href: '/library/voice' },
      { code: 'blocklist', label: 'Blocklist of client and internal project names saved', done: true, href: '/settings/policy' },
      { code: 'ai_use_confirmed', label: 'Employer AI-use policy confirmed', done: false, href: '/settings/policy' },
    ],
  };
}

export function jobView(overrides: Partial<S['JobView']> = {}): S['JobView'] {
  return {
    jobId: 'job-crawl-1',
    type: 'signals.crawl',
    status: 'succeeded',
    attempt: 1,
    maxAttempts: 3,
    seq: 4,
    cancelRequested: false,
    createdAt: hoursFrom(-2),
    updatedAt: hoursFrom(-1),
    startedAt: hoursFrom(-2),
    finishedAt: hoursFrom(-1),
    scheduledFor: null,
    steps: [
      { name: 'fetch', status: 'succeeded', runs: 1, startedAt: hoursFrom(-2), finishedAt: hoursFrom(-1.5) },
      { name: 'triage', status: 'succeeded', runs: 1, startedAt: hoursFrom(-1.5), finishedAt: hoursFrom(-1) },
    ],
    awaiting: null,
    error: null,
    events: null,
    ...overrides,
  };
}

export function backupsView(overrides: Partial<S['BackupsView']> = {}): S['BackupsView'] {
  const file = (container: string, size: number) => ({
    container,
    blob: `2026-11-27/${container}.jsonl.gz`,
    sizeBytes: size,
    sha256: 'f'.repeat(64),
  });
  return {
    status: {
      item: {
        ...doc('backup-status'),
        lastSuccess: {
          date: '2026-11-27',
          startedAt: hoursFrom(-12),
          finishedAt: hoursFrom(-11.9),
          files: [
            { container: 'articles', blob: '2026-11-27/articles.jsonl.gz', documents: 40, sizeBytes: 1_200_000, sha256: 'a'.repeat(64) },
            { container: 'posts', blob: '2026-11-27/posts.jsonl.gz', documents: 120, sizeBytes: 840_000, sha256: 'b'.repeat(64) },
          ],
          appSha: null,
        },
        lastAttemptAt: hoursFrom(-12),
        lastError: null,
        consecutiveFailures: 0,
        lastRestoreDrillAt: null,
      },
      etag: '"backup-1"',
    },
    dumps: [
      { date: '2026-11-27', complete: true, files: [file('articles', 1_200_000), file('posts', 840_000)] },
      { date: '2026-11-26', complete: true, files: [file('articles', 1_100_000)] },
    ],
    restoreDrillDue: true,
    ...overrides,
  };
}

export function evalTopic(overrides: Partial<S['EvalTopic']> = {}): S['Versioned_EvalTopic_'] {
  const item: S['EvalTopic'] = {
    ...doc('topic-1'),
    pillar: 'agents',
    format: 'case-study',
    topic: 'Locking down an MCP server in a week',
    briefId: null,
    active: true,
    ...overrides,
  };
  return { item, etag: `"${item.id}-1"` };
}

export function evalResult(overrides: Partial<S['EvalResult']> = {}): S['Versioned_EvalResult_'] {
  const item: S['EvalResult'] = {
    ...doc('evr-1'),
    topicId: 'topic-1',
    runId: 'analytics-eval-1',
    articleId: 'art-eval-1',
    judgePassed: true,
    judgeScore: 4.2,
    ownerLabel: null,
    labelledAt: null,
    ...overrides,
  };
  return { item, etag: `"${item.id}-1"` };
}

/** A page of entries, as list routes answer. */
export function page<T>(items: T[], nextCursor: string | null = null): { items: T[]; nextCursor: string | null } {
  return { items, nextCursor };
}
