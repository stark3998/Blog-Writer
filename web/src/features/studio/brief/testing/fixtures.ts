import type { components } from '@/lib/api/schema';

import { T0 } from '../../piece/testing/fixtures';

/** Research documents for Brief tab tests, typed from `schema.ts`. */

type Schemas = components['schemas'];

export function citation(overrides: Partial<Schemas['Citation']> = {}): Schemas['Citation'] {
  return {
    id: 'src-learn-microsoft-com-a1b2c3',
    url: 'https://learn.microsoft.com/entra/identity/authentication/passkeys',
    title: 'Passkeys in Microsoft Entra ID',
    publisher: 'Microsoft Learn',
    date: '2025-03',
    domain: 'learn.microsoft.com',
    allowlisted: true,
    ...overrides,
  };
}

export const CITATION_A = citation();
export const CITATION_B = citation({
  id: 'src-fidoalliance-org-d4e5f6',
  url: 'https://fidoalliance.org/passkeys-report',
  title: 'The state of passkeys',
  publisher: 'FIDO Alliance',
  date: '2025-10-02',
  domain: 'fidoalliance.org',
  allowlisted: false,
});

export function briefDoc(overrides: Partial<Schemas['Brief']> = {}): Schemas['Brief'] {
  return {
    id: 'brief-run-1',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    runId: 'run-1',
    articleId: 'art-1',
    topic: 'Passkeys for workforce identity in Entra',
    pillar: 'identity',
    audience: 'Identity architects',
    executiveSummary: 'Passkeys are ready for workforce sign-in in Entra, with sync as the open question.',
    findings: [
      { id: 'f-1', text: 'Entra supports device-bound passkeys for every user.', citationIds: [CITATION_A.id] },
      { id: 'f-2', text: 'Synced passkeys raise recovery questions.', citationIds: [CITATION_A.id, CITATION_B.id] },
    ],
    stats: [
      { id: 's-1', value: '87%', label: 'of sign-ins phishing-resistant at early adopters', key: true, citationIds: [CITATION_A.id, CITATION_B.id], asOf: '2025' },
      { id: 's-2', value: '3x', label: 'faster sign-in than passwords', key: false, citationIds: [CITATION_B.id] },
    ],
    counterpoints: ['Hardware keys stay cheaper to govern.'],
    openQuestions: ['Can a synced passkey be revoked centrally?'],
    angles: ['What breaks first in an Okta-to-Entra move'],
    hookSeeds: ['We turned off passwords for 2,000 people.'],
    claims: [
      { id: 'cl-1', text: 'Entra supports passkeys in Authenticator.', citationId: CITATION_A.id, status: 'verified' },
      {
        id: 'cl-2',
        text: 'Sync coverage differs by platform.',
        citationId: CITATION_B.id,
        status: 'conflict',
        conflictNote: 'Sources disagree on Android.',
      },
    ],
    quotes: [{ id: 'q-1', citationId: CITATION_B.id, text: 'Passkeys are the future of sign-in.' }],
    citations: [CITATION_A, CITATION_B],
    webiqFallback: false,
    editedByOwner: false,
    ...overrides,
  };
}

export function runDoc(overrides: Partial<Schemas['ResearchRun']> = {}): Schemas['ResearchRun'] {
  return {
    id: 'run-1',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'succeeded',
    statusChangedAt: T0,
    topic: 'Passkeys for workforce identity in Entra',
    pillar: 'identity',
    recencyDays: 365,
    articleId: 'art-1',
    briefId: 'brief-run-1',
    jobId: 'research-run-1',
    webiqFallback: false,
    blocklistConfigured: true,
    costUsd: 0.84,
    subQuestions: ['Which passkey types does Entra support?'],
    queries: [{ text: 'entra passkeys workforce', phase: 'allowlist' }],
    sources: [
      { url: CITATION_A.url, domain: CITATION_A.domain, phase: 'allowlist', allowlisted: true, fetched: true },
      { url: CITATION_B.url, domain: CITATION_B.domain, phase: 'open', allowlisted: false, fetched: false },
    ],
    ...overrides,
  };
}

export function quickSearchDoc(overrides: Partial<Schemas['QuickSearch']> = {}): Schemas['QuickSearch'] {
  return {
    id: 'qs-1',
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    query: 'Does Entra support passkey sync?',
    pillar: 'identity',
    answer: `Yes, for Authenticator on iOS and Android. [^${CITATION_A.id}][^${CITATION_B.id}]`,
    claims: [{ id: 'cl-1', text: 'Sync arrived in 2025.', citationId: CITATION_A.id, status: 'unverified' }],
    citations: [CITATION_A, CITATION_B],
    articleId: 'art-1',
    ...overrides,
  };
}
