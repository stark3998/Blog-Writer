import type { components } from '@/lib/api/schema';
import type { MachineViews } from '@/lib/query/machines';
import { SERVER_MACHINES } from '@/test/machines';

import type { Illustration, ModelsView } from '../model/illustrations';
import type {
  Article,
  ArticleVersion,
  Brief,
  Finding,
  GateResult,
  GatesView,
  MediaRef,
  VersionedArticle,
  VersionSummary,
} from '../model/types';

/** Fixtures for the Draft tab's tests, typed from the generated schema. */

export const ARTICLE_ID = 'art-0193a0b2c3d4e5f60718293a4b5c6d7e';
export const BRIEF_ID = 'brief-run-0193a0b2c3d4e5f6';
export const T0 = '2026-11-26T15:00:00.000000Z';

export const SAMPLE_MARKDOWN = [
  '## Why agents need identity',
  '',
  'Agents call tools on our behalf, so every call needs an owner.[^nist-ai-rmf]',
  '',
  '- Least privilege per tool call',
  '- Short-lived tokens',
  '',
  '## What to do first',
  '',
  'Start with an inventory of the agents you already run.',
  '',
  '[^nist-ai-rmf]: NIST. *AI Risk Management Framework*. [nist.gov](https://www.nist.gov/itl/ai-risk-management-framework)',
  '',
].join('\n');

export function articleFixture(overrides: Partial<Article> = {}): Article {
  return {
    id: ARTICLE_ID,
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'in_review',
    statusChangedAt: T0,
    purpose: 'flagship',
    updatedBy: 'pipeline',
    meta: {
      title: 'Agent identity in practice',
      slug: 'agent-identity-in-practice',
      excerpt: 'How to give an agent exactly the access a single tool call needs.',
      pillar: 'agents',
      format: 'deep-explainer',
      tags: ['agents', 'identity'],
      series: null,
    },
    markdown: SAMPLE_MARKDOWN,
    version: 7,
    contentSha256: 'a'.repeat(64),
    wordCount: 40,
    readingTimeMinutes: 1,
    briefId: BRIEF_ID,
    pipelineJobId: 'pipeline-job-1',
    pipelineRuns: 1,
    reviewOnly: false,
    clearance: { required: false },
    publish: { live: false, auto: false },
    ...overrides,
  };
}

export function versioned(article: Article, etag = '"etag-7"'): VersionedArticle {
  return { item: article, etag };
}

export function finding(overrides: Partial<Finding> = {}): Finding {
  return { code: 'slop-over-threshold', severity: 'warn', message: 'Three rule-of-three lists in a row.', overridable: true, ...overrides };
}

export function gateResult(gate: GateResult['gate'], overrides: Partial<GateResult> = {}): GateResult {
  return { gate, verdict: 'pass', truncated: 0, ranAt: T0, findings: [], ...overrides };
}

/** A report where every gate passed, except what `results` replaces. */
export function gatesViewFixture(results: readonly GateResult[] = [], overrides: Partial<GatesView> = {}): GatesView {
  const gates: GateResult['gate'][] = ['rubric', 'fact_check', 'confidentiality', 'originality', 'lint', 'owner_signal', 'outbound'];
  const byGate = new Map(results.map((result) => [result.gate, result]));
  return {
    report: {
      contentSha256: 'a'.repeat(64),
      version: 7,
      ranAt: T0,
      jobId: 'gates-job-1',
      results: gates.map((gate) => byGate.get(gate) ?? gateResult(gate, gate === 'rubric' ? { score: 8.6, dimensions: { clarity: 9, first_hand_specificity: 7.5 } } : {})),
    },
    stale: false,
    autoPublishReady: results.length === 0,
    manualPublishReady: results.length === 0,
    blocking: [],
    ...overrides,
  };
}

export function briefFixture(): Brief {
  return {
    id: BRIEF_ID,
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    topic: 'Agent identity',
    pillar: 'agents',
    executiveSummary: 'Agents need owners.',
    webiqFallback: false,
    editedByOwner: false,
    citations: [
      {
        id: 'nist-ai-rmf',
        url: 'https://www.nist.gov/itl/ai-risk-management-framework',
        title: 'AI Risk Management Framework',
        publisher: 'NIST',
        domain: 'nist.gov',
        allowlisted: true,
      },
      {
        id: 'owasp-llm-top-10',
        url: 'https://owasp.org/www-project-top-10-for-large-language-model-applications/',
        title: 'OWASP Top 10 for LLM Applications',
        publisher: 'OWASP',
        domain: 'owasp.org',
        allowlisted: true,
      },
    ],
  };
}

/** `GET /api/meta/machines` as the server answers it for articles (edges, owner edges, writers). */
export const MACHINES: MachineViews = { article: SERVER_MACHINES.article };

export const PILLARS: components['schemas']['Versioned_PillarSettings_'] = {
  etag: '"pillars-1"',
  item: {
    id: 'pillars',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    pillars: [
      { id: 'agents', name: 'AI agents & security', topics: '', description: '', audience: '', targetShare: 0.34 },
      { id: 'identity', name: 'Identity modernization', topics: '', description: '', audience: '', targetShare: 0.33 },
    ],
  },
};

export const FORMATS: components['schemas']['Versioned_FormatPacks_'] = {
  etag: '"formats-1"',
  item: {
    id: 'format-packs',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    packs: [
      {
        id: 'deep-explainer',
        name: 'Deep explainer',
        minWords: 1500,
        maxWords: 2500,
        ownerInput: 'micro_interview',
        minQuestions: 2,
        maxQuestions: 3,
      },
    ],
  },
};

export function versionSummary(version: number, overrides: Partial<VersionSummary> = {}): VersionSummary {
  return {
    version,
    source: 'pipeline',
    author: 'pipeline',
    statusAtSave: 'in_review',
    createdAt: T0,
    contentSha256: 'b'.repeat(64),
    note: null,
    pinned: false,
    ...overrides,
  };
}

export function articleVersion(version: number, markdown: string): ArticleVersion {
  return {
    id: `${ARTICLE_ID}-v${String(version).padStart(5, '0')}`,
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    articleId: ARTICLE_ID,
    version,
    meta: articleFixture().meta ?? {},
    markdown,
    contentSha256: 'c'.repeat(64),
    source: 'pipeline',
    author: 'pipeline',
    statusAtSave: 'in_review',
  };
}

// ------------------------------------------------------------------------------------------------
// AI illustrations
// ------------------------------------------------------------------------------------------------

export const ILLUSTRATION_JOB_ID = `illustrations-${ARTICLE_ID}-a1b2c3`;
const T1 = '2026-11-26T16:00:00.000000Z';

/** A 64-hex SHA-256 made of one repeated digit (`'1'` -> `111…1`). */
function sha(digit: string): string {
  return digit.repeat(64);
}

export function mediaFixture(overrides: Partial<MediaRef> = {}): MediaRef {
  const hash = overrides.sha256 ?? sha('9');
  return {
    role: 'image',
    container: 'media',
    name: `articles/${ARTICLE_ID}/${hash}.png`,
    sha256: hash,
    contentType: 'image/png',
    sizeBytes: 1_200_000,
    width: 1536,
    height: 1024,
    altText: 'An image.',
    uploadedAt: T1,
    ...overrides,
  };
}

const CANDIDATES = {
  '1': {
    role: 'hero',
    altText: 'Two hands pass a glowing key across a desk to a small robot.',
    promptSummary: 'A key handed from a person to an agent',
    section: null,
    costUsd: 0.27,
    size: [1536, 1024],
  },
  '2': {
    role: 'inline',
    altText: 'A ring of tool icons, each behind its own small lock.',
    promptSummary: 'Every tool behind its own lock',
    section: 'Why agents need identity',
    costUsd: 0.19,
    size: [1024, 1024],
  },
  '3': {
    role: 'inline',
    altText: 'A checklist on a clipboard beside a row of sleeping robots.',
    promptSummary: 'An inventory of the agents you run',
    section: 'What to do first',
    costUsd: 0.18,
    size: [1024, 1024],
  },
} as const;

/** Candidate `1` (the planned hero), `2` or `3` (planned for the text) of one run. */
export function illustrationFixture(which: keyof typeof CANDIDATES, overrides: Partial<Illustration> = {}): Illustration {
  const candidate = CANDIDATES[which];
  const hash = sha(which);
  return {
    id: `ill-${hash.slice(0, 16)}`,
    role: candidate.role,
    media: mediaFixture({
      sha256: hash,
      width: candidate.size[0],
      height: candidate.size[1],
      sizeBytes: 1_800_000,
      altText: candidate.altText,
    }),
    altText: candidate.altText,
    promptSummary: candidate.promptSummary,
    section: candidate.section,
    servedBy: [],
    costUsd: candidate.costUsd,
    jobId: ILLUSTRATION_JOB_ID,
    createdAt: T1,
    ...overrides,
  };
}

/** The title card the pipeline renders (`og_image`) and its copy as the default hero. */
export function titleCardMedia(): MediaRef[] {
  const card = mediaFixture({ role: 'og_image', sha256: sha('c'), width: 1200, height: 630, sizeBytes: 90_000, altText: 'Agent identity in practice' });
  return [card, { ...card, role: 'hero_image' }];
}

/** `GET /api/kb/models` with illustrations switched on, an image route and its prices. */
export function modelsViewFixture({
  imagesEnabled = true,
  imageRoute = true,
}: { imagesEnabled?: boolean; imageRoute?: boolean } = {}): ModelsView {
  return {
    preferences: {
      item: {
        id: 'model-preferences',
        schemaVersion: 1,
        createdAt: T0,
        updatedAt: T0,
        imagesEnabled,
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
        chain: [{ model: 'claude-opus-5-5', deployment: 'opus', provider: 'anthropic', effort: 'high' }],
      },
      ...(imageRoute
        ? [
            {
              task: 'image',
              maxClass: 'public',
              tools: false,
              chain: [{ model: 'gpt-image-2', deployment: 'gpt-image-2', provider: 'openai', effort: null }],
            },
          ]
        : []),
    ],
    prices: [
      { model: 'claude-opus-5-5', input: 15, output: 75, cachedInput: 1.5, cacheWrite: 18.75 },
      ...(imageRoute
        ? [{ model: 'gpt-image-2', input: 0, output: 0, cachedInput: null, cacheWrite: null, imageUsd: { '1536x1024': 0.25, '1024x1024': 0.17 } }]
        : []),
    ],
    webSearchUsdPer1k: 14,
  };
}
