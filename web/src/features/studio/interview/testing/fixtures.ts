import type { components } from '@/lib/api/schema';

import type { Interview, InterviewEntry } from '../interview-model';

/** Interview fixtures shaped like the server's documents (`schema.ts`). */

type Article = components['schemas']['Article'];

export const ARTICLE_ID = 'art-0193f2a1b2c3d4e5f60718293a4b';
export const INTERVIEW_ID = 'intv-0193f2a1b2c3d4e5f60718293a4c';
export const PIPELINE_JOB_ID = 'job-pipeline-art-1';
export const T0 = '2026-11-24T15:00:00.000000Z';
export const DUE = '2026-11-26T23:00:00.000000Z';

export const QUESTIONS: Interview['questions'] = [
  { id: 'q1', text: 'What’s your take on moving from Okta to Entra?', purpose: 'Your position in your own words.' },
  { id: 'q2', text: 'What would you tell a client to do first on Monday?', purpose: 'The concrete first step.' },
];

export const FULL_QUESTIONS: Interview['questions'] = [
  ...QUESTIONS,
  { id: 'q3', text: 'Which decision mattered most, and what trade-off did you accept?', purpose: null },
  { id: 'q4', text: 'What did you measure along the way?', purpose: null },
  { id: 'q5', text: 'What would you change if you did it again?', purpose: null },
];

export function interviewFixture(overrides: Partial<Interview> = {}): Interview {
  return {
    id: INTERVIEW_ID,
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'pending',
    statusChangedAt: T0,
    mode: 'micro',
    articleId: ARTICLE_ID,
    postId: null,
    questions: QUESTIONS,
    answers: [],
    dueAt: DUE,
    jobId: PIPELINE_JOB_ID,
    servedBy: [],
    ...overrides,
  };
}

export function interviewEntry(overrides: Partial<Interview> = {}, etag = '"etag-1"'): InterviewEntry {
  return { item: interviewFixture(overrides), etag };
}

export function articleFixture(overrides: Partial<Article> = {}): Article {
  return {
    id: ARTICLE_ID,
    schemaVersion: 1,
    createdAt: T0,
    updatedAt: T0,
    status: 'interviewing',
    statusChangedAt: T0,
    purpose: 'flagship',
    updatedBy: 'pipeline',
    meta: { title: 'Okta to Entra field notes', pillar: 'identity', format: 'news-analysis', tags: [] },
    markdown: '',
    version: 0,
    wordCount: 0,
    readingTimeMinutes: 0,
    interviewId: INTERVIEW_ID,
    pipelineRuns: 1,
    reviewOnly: false,
    ...overrides,
  };
}

/** `GET /api/meta/machines`, as the server answers it for the interview machine. */
export const MACHINES: Record<string, Record<string, string[]>> = {
  interview: { pending: ['answered', 'skipped', 'expired'], expired: ['answered', 'skipped'], answered: [], skipped: [] },
};
