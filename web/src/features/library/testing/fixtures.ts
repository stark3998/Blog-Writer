import type { components } from '@/lib/api/schema';

/**
 * Library fixtures shaped exactly like the server's answers (`schema.ts`). Colours are built with
 * `hex()`: source files outside `src/test` and `src/theme` may not hold colour literals.
 */
type Schemas = components['schemas'];

export const T0 = '2026-11-20T09:00:00.000000Z';
export const T1 = '2026-11-24T15:30:00.000000Z';
export const T2 = '2026-11-26T10:00:00.000000Z';

/** A colour from its six hex digits. */
export function hex(digits: string): string {
  return `#${digits}`;
}

export function versioned<T>(item: T, etag: string | null = '"etag-1"'): { item: T; etag: string | null } {
  return { item, etag };
}

export function page<E extends { item: unknown; etag: string | null }>(
  items: readonly E[],
  nextCursor: string | null = null,
): { items: E[]; nextCursor: string | null } {
  return { items: [...items], nextCursor };
}

// ------------------------------------------------------------------------------------------------
// Pillars, lanes, formats
// ------------------------------------------------------------------------------------------------

export function pillar(overrides: Partial<Schemas['Pillar-Output']> = {}): Schemas['Pillar-Output'] {
  return {
    id: 'agents',
    name: 'AI agents & security',
    topics: 'MCP, multi-agent SecOps, prompt injection, agent identity',
    description: 'Running AI agents against real systems without opening a new way in for attackers.',
    audience: 'Security architects and platform engineers connecting agents to production.',
    vocabulary: ['Model Context Protocol (MCP)', 'prompt injection'],
    renames: { 'Azure Sentinel': 'Microsoft Sentinel' },
    hashtags: ['AISecurity'],
    researchDomains: ['modelcontextprotocol.io', 'genai.owasp.org'],
    targetShare: 0.34,
    ...overrides,
  };
}

export const PILLARS: Schemas['Pillar-Output'][] = [
  pillar(),
  pillar({
    id: 'identity',
    name: 'Identity modernization',
    topics: 'Okta to Entra, a 2,000-app migration, CIAM',
    description: 'Moving a large estate between identity providers without breaking sign-in.',
    audience: 'IAM leads and architects.',
    vocabulary: ['Microsoft Entra ID'],
    renames: { 'Azure AD': 'Microsoft Entra ID' },
    hashtags: ['IAM'],
    researchDomains: ['learn.microsoft.com'],
    targetShare: 0.33,
  }),
  pillar({
    id: 'governance',
    name: 'AI governance',
    topics: 'Risk-tiered agent lifecycles, NIST AI RMF',
    description: 'Governing agents with risk tiers.',
    audience: 'CISOs and risk leads.',
    vocabulary: ['NIST AI RMF'],
    renames: {},
    hashtags: [],
    researchDomains: ['nist.gov'],
    targetShare: 0.33,
  }),
];

export function pillarSettings(pillars: Schemas['Pillar-Output'][] = PILLARS): Schemas['PillarSettings'] {
  return { id: 'pillars', schemaVersion: 1, createdAt: T0, updatedAt: T0, pillars };
}

export function laneSettings(overrides: Partial<Schemas['LaneSettings']> = {}): Schemas['LaneSettings'] {
  return {
    id: 'lanes',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    derivativesMaxPerWeek: 2,
    nativeMinPerWeek: 1,
    nativeOtherPillar: true,
    onPillarMinShare: 0.8,
    hookVariants: 3,
    quickTake: { minChars: 1200, maxChars: 1500, maxAgeHours: 72, ownerTakeRequired: true },
    formats: {
      carouselMinSlides: 8,
      carouselMaxSlides: 12,
      textMinChars: 1200,
      textMaxChars: 2000,
      imageCardEnabled: true,
      pollMaxPerMonth: 1,
    },
    repurpose: { minWeeks: 8, maxWeeks: 12, requiresNoveltyCheck: true },
    series: [{ id: 'okta-to-entra', pillar: 'identity', title: 'Okta to Entra field notes', active: true }],
    ...overrides,
  };
}

export function formatPacks(): Schemas['FormatPacks'] {
  return {
    id: 'format-packs',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    packs: [
      {
        id: 'case-study',
        name: 'Case study / how I did it',
        minWords: 1500,
        maxWords: 2500,
        mustContain: ['anonymized context', 'constraints', 'decisions and trade-offs'],
        ownerInput: 'full_interview',
        minQuestions: 5,
        maxQuestions: 8,
        banned: [],
      },
      {
        id: 'news-analysis',
        name: 'News analysis',
        minWords: 900,
        maxWords: 1500,
        mustContain: ['what changed (cited)', 'what to do Monday'],
        ownerInput: 'micro_interview',
        minQuestions: 2,
        maxQuestions: 2,
        banned: [],
      },
    ],
  };
}

export function source(overrides: Partial<Schemas['Source']> = {}): Schemas['Source'] {
  return {
    id: 'feed-0001',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    type: 'feed',
    title: 'OWASP GenAI blog',
    url: 'https://genai.owasp.org/feed/',
    pillar: 'agents',
    status: 'active',
    statusChangedAt: T0,
    consecutiveFailures: 0,
    itemsSeen: 12,
    lastSuccessAt: T1,
    ...overrides,
  };
}

// ------------------------------------------------------------------------------------------------
// Brand kit and policy
// ------------------------------------------------------------------------------------------------

/** The renderer's placeholder palette (passes AA). */
export function brandColors(): Schemas['BrandColors'] {
  return {
    ink: hex('0E2A24'),
    paper: hex('F3F5F2'),
    muted: hex('4A5D56'),
    mutedOnInk: hex('A8C1B8'),
    signal: hex('F2A93B'),
    signalText: hex('8A4F00'),
    rule: hex('C5D1CB'),
    ruleOnInk: hex('2E4A43'),
  };
}

export function brandKit(overrides: Partial<Schemas['BrandKit']> = {}): Schemas['BrandKit'] {
  return {
    id: 'brand-kit',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    authorName: 'Jordan Writer',
    authorHeadline: 'Identity and AI security',
    siteName: null,
    colors: brandColors(),
    font: 'inter',
    headshot: null,
    nameMark: null,
    artBriefs: {},
    ...overrides,
  };
}

export function mediaRef(overrides: Partial<Schemas['MediaRef']> = {}): Schemas['MediaRef'] {
  return {
    role: 'headshot',
    container: 'media',
    name: `uploads/2026-11/${'a'.repeat(64)}.png`,
    sha256: 'a'.repeat(64),
    contentType: 'image/png',
    sizeBytes: 2048,
    width: 256,
    height: 256,
    ...overrides,
  };
}

export function employerPolicy(overrides: Partial<Schemas['EmployerPolicy']> = {}): Schemas['EmployerPolicy'] {
  return {
    id: 'employer-policy',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    disclaimerShort: 'Views are my own.',
    disclaimerLong: null,
    forbiddenTopics: [],
    noEmployerBranding: true,
    aiUseConfirmed: true,
    clearanceStatement: 'I confirm this piece is anonymized and cleared for publication.',
    configured: true,
    ...overrides,
  };
}

export function postSummary(overrides: Partial<Schemas['PostSummary']> = {}): Schemas['PostSummary'] {
  return {
    id: 'post-0001',
    status: 'draft',
    statusChangedAt: T1,
    lane: 'native',
    pillar: 'agents',
    format: 'carousel',
    hook: 'Three checks before an agent touches production',
    slot: null,
    goLiveMode: 'kit',
    kitState: null,
    approvalCurrent: false,
    postedAt: null,
    reviewOnly: false,
    articleId: null,
    updatedAt: T1,
    ...overrides,
  };
}

// ------------------------------------------------------------------------------------------------
// Stories and positions
// ------------------------------------------------------------------------------------------------

export function story(overrides: Partial<Schemas['StoryEntry']> = {}): Schemas['StoryEntry'] {
  return {
    id: 'story-0001',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T1,
    status: 'approved',
    statusChangedAt: T1,
    origin: 'owner',
    title: 'Moving 2,000+ apps without a sign-in outage',
    problem: 'Two identity providers, one weekend change window.',
    actions: 'Waves by risk tier, with rollback per app.',
    decisions: 'Kept legacy federation for six weeks.',
    metrics: [{ label: 'Apps migrated', value: '2,000+', bucketed: true }],
    lessons: 'Start with the noisiest apps.',
    pillars: ['identity'],
    confidentiality: 'anonymized',
    tags: ['migration'],
    classifier: { passed: true, findings: [] },
    approvedAt: T1,
    ...overrides,
  };
}

export function pov(overrides: Partial<Schemas['PovEntry']> = {}): Schemas['PovEntry'] {
  return {
    id: 'pov-0001',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T1,
    status: 'approved',
    statusChangedAt: T1,
    origin: 'owner',
    pillar: 'agents',
    statement: 'An MCP server is a production integration and needs the same review as one.',
    context: null,
    confidentiality: 'public',
    classifier: { passed: true, findings: [] },
    approvedAt: T1,
    ...overrides,
  };
}

export function ownerInputs(storiesHave = 4, povPillars = 1): Schemas['OwnerInputs'] {
  return {
    items: [
      { code: 'linkedin_samples', label: 'Past LinkedIn posts you wrote by hand', done: false, have: 3, need: 5, href: '/library/voice' },
      { code: 'stories_approved', label: 'Approved stories', done: storiesHave >= 10, have: storiesHave, need: 10, href: '/library/stories' },
      {
        code: 'povs_per_pillar',
        label: 'Pillars with 3 or more approved POVs',
        done: povPillars >= 3,
        have: povPillars,
        need: 3,
        href: '/library/stories',
      },
    ],
  };
}

// ------------------------------------------------------------------------------------------------
// Voice
// ------------------------------------------------------------------------------------------------

export const SAMPLE_TEXT = 'Let us delve into why agent identity matters. I moved our agents to managed identities first.';

export function voiceSample(overrides: Partial<Schemas['VoiceSample']> = {}): Schemas['VoiceSample'] {
  const start = SAMPLE_TEXT.indexOf('delve');
  return {
    id: 'sample-0001',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T1,
    status: 'active',
    statusChangedAt: T0,
    sourceType: 'linkedin_post',
    ownerUnassisted: true,
    title: 'Agent identity first',
    text: SAMPLE_TEXT,
    wordCount: 16,
    weight: 4,
    aiTellScore: 6.25,
    flags: [{ code: 'ai-tell', severity: 'warn', message: 'AI tell: ‘delve’.', start, end: start + 5, overridable: false }],
    exemplarEligible: false,
    ...overrides,
  };
}

export function styleGuide(overrides: Partial<Schemas['StyleGuide']> = {}): Schemas['StyleGuide'] {
  return {
    id: 'style-guide-0002',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T1,
    version: 2,
    status: 'active',
    statusChangedAt: T1,
    body: 'Short sentences. Lead with the decision.\n\nNo hype words.',
    rules: { prefer: ['Lead with the decision'], avoid: ['Rhetorical questions'], bannedPhrases: ['game changer'], vocabulary: {} },
    exemplars: [{ sampleId: 'sample-0001', excerpt: 'I moved our agents to managed identities first.' }],
    sourceSampleIds: ['sample-0001'],
    basedOnVersion: null,
    servedBy: [],
    approvedAt: T1,
    editedByOwner: false,
    provisional: false,
    ...overrides,
  };
}

export function guideView(overrides: Partial<Schemas['GuideView']> = {}): Schemas['GuideView'] {
  return {
    active: null,
    versions: [],
    linkedinSamples: 3,
    required: 5,
    canBuild: false,
    ...overrides,
  };
}

export function digest(overrides: Partial<Schemas['VoiceDigest']> = {}): Schemas['VoiceDigest'] {
  return {
    id: 'digest-2026-11-16',
    schemaVersion: 1,
    createdAt: T2,
    updatedAt: T2,
    periodStart: '2026-11-16T00:00:00.000000Z',
    periodEnd: '2026-11-22T23:59:59.000000Z',
    guideVersion: 2,
    comparisons: [{ targetKind: 'post', targetId: 'post-0001', editRatio: 0.34, notableEdits: ['Cut the opening question.'] }],
    suggestions: [
      {
        id: 'sug-1',
        text: 'Open with the outcome, not the question.',
        rationale: 'You removed the opening question in 3 of 4 posts.',
        kind: 'prefer',
        status: 'proposed',
      },
    ],
    servedBy: [],
    ...overrides,
  };
}

/** `GET /api/meta/machines` for the machines the Library acts on (contract section 2). */
export const MACHINES: Record<string, Record<string, string[]>> = {
  review: { proposed: ['approved', 'rejected'], approved: ['archived'], rejected: ['proposed'], archived: ['approved'] },
  voice_sample: { active: ['excluded'], excluded: ['active'] },
  style_guide: { proposed: ['active', 'rejected'], active: ['superseded'], superseded: ['active'], rejected: [] },
  suggestion: { proposed: ['accepted', 'dismissed'], accepted: [], dismissed: [] },
};
