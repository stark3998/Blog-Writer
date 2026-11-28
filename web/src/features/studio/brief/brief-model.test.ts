// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/api/errors';

import {
  answerParts,
  citationUse,
  cleanOwnerUrls,
  defaultBriefId,
  defaultRecencyDays,
  failedLatestRun,
  fieldPath,
  isActiveRun,
  newestFirst,
  ownerUrlProblem,
  problemsByField,
  runErrorSentence,
  statSupport,
} from './brief-model';
import { briefDoc, CITATION_A, CITATION_B, runDoc } from './testing/fixtures';

function apiError(status: number, code: string, extensions: Record<string, unknown>): ApiError {
  return new ApiError({ kind: 'client', status, problem: { status, code, extensions } });
}

describe('runs', () => {
  const older = runDoc({ id: 'run-a', createdAt: '2026-11-26T10:00:00.000000Z', briefId: 'brief-a' });
  const newer = runDoc({ id: 'run-b', createdAt: '2026-11-27T10:00:00.000000Z', briefId: 'brief-b' });

  it('orders runs newest first and knows which are still working', () => {
    expect(newestFirst([older, newer]).map((run) => run.id)).toEqual(['run-b', 'run-a']);
    expect(isActiveRun({ status: 'queued' })).toBe(true);
    expect(isActiveRun({ status: 'running' })).toBe(true);
    expect(isActiveRun({ status: 'failed' })).toBe(false);
  });

  it('shows the brief the draft uses first, else the newest finished one', () => {
    expect(defaultBriefId('brief-pipeline', [older, newer])).toBe('brief-pipeline');
    expect(defaultBriefId(null, [older, newer])).toBe('brief-b');
    expect(defaultBriefId(undefined, [runDoc({ status: 'failed', briefId: null })])).toBeUndefined();
  });

  it('leads with the newest run only when it ended without a brief', () => {
    const failed = runDoc({ id: 'run-c', createdAt: '2026-11-28T10:00:00.000000Z', status: 'failed', errorCode: 'no_sources' });
    expect(failedLatestRun([older, failed])?.id).toBe('run-c');
    expect(failedLatestRun([failed, runDoc({ id: 'run-d', createdAt: '2026-11-29T10:00:00.000000Z' })])).toBeUndefined();
    expect(runErrorSentence(failed)).toMatch(/no usable sources/);
    expect(runErrorSentence(runDoc({ status: 'quarantined', quarantineReason: 'blocklist_hit' }))).toMatch(/blocklist/);
    expect(runErrorSentence(runDoc({ status: 'cancelled', errorCode: 'cancelled' }))).toBe('You cancelled this run.');
    expect(runErrorSentence(runDoc({ status: 'failed', errorCode: 'something_else' }))).toMatch(/stopped before it finished/);
  });

  it('looks back 30 days for news analysis and a year otherwise', () => {
    expect(defaultRecencyDays('news-analysis')).toBe(30);
    expect(defaultRecencyDays('deep-explainer')).toBe(365);
    expect(defaultRecencyDays(null)).toBe(365);
  });
});

describe('briefs', () => {
  it('applies the two-source rule to numbers', () => {
    expect(statSupport({ key: true, citationIds: [CITATION_A.id, CITATION_B.id] })).toEqual({ confirmed: true, sources: 2 });
    expect(statSupport({ key: false, citationIds: [CITATION_B.id] })).toEqual({ confirmed: false, sources: 1 });
    // A key flag over one distinct source is not two sources.
    expect(statSupport({ key: true, citationIds: [CITATION_A.id, CITATION_A.id] })).toEqual({ confirmed: false, sources: 1 });
  });

  it('counts what rests on each source', () => {
    const use = citationUse(briefDoc());
    expect(use.get(CITATION_A.id)).toBe(4);
    expect(use.get(CITATION_B.id)).toBe(4);
  });

  it('numbers footnote markers by the source list and hugs them to the sentence', () => {
    const parts = answerParts(`Yes, on iOS. [^${CITATION_B.id}][^src-unknown] More. [^${CITATION_A.id}]`, [CITATION_A, CITATION_B]);
    expect(parts).toEqual([
      { type: 'text', text: 'Yes, on iOS.' },
      { type: 'cite', citation: CITATION_B, number: 2 },
      { type: 'text', text: ' More.' },
      { type: 'cite', citation: CITATION_A, number: 1 },
    ]);
    expect(answerParts('', [])).toEqual([]);
  });
});

describe('the run form', () => {
  it('accepts https links only', () => {
    expect(ownerUrlProblem('https://example.com/a')).toBeUndefined();
    expect(ownerUrlProblem('   ')).toBeUndefined();
    expect(ownerUrlProblem('http://example.com')).toBe('Use an https:// link.');
    expect(ownerUrlProblem('example.com')).toBe('Enter the whole link, with https:// at the start.');
    expect(ownerUrlProblem('https://localhost')).toBe('That link has no proper domain.');
    expect(cleanOwnerUrls([' https://a.example/x ', '', 'https://a.example/x', 'https://b.example'])).toEqual([
      'https://a.example/x',
      'https://b.example',
    ]);
  });

  it('puts the server’s messages on the fields, by dotted path', () => {
    expect(fieldPath('ownerUrls[2]')).toBe('ownerUrls.2');
    const validation = apiError(422, 'validation_failed', {
      errors: [{ loc: ['body', 'topic'], msg: 'String should have at least 3 characters' }],
    });
    expect(problemsByField(validation)).toEqual({ topic: 'String should have at least 3 characters' });
    const confidential = apiError(422, 'confidential_text', {
      findings: [{ field: 'ownerUrls[1]', code: 'blocklist', severity: 'block', message: 'Blocked.', ref: 'term-3' }],
    });
    expect(problemsByField(confidential)).toEqual({ 'ownerUrls.1': 'This matches a term on your blocklist. Rephrase it.' });
  });
});
