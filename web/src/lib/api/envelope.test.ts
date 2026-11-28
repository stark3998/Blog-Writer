// @vitest-environment node
import { describe, expect, it } from 'vitest';

import {
  conflictOf,
  etagFromResponse,
  isEtagMismatch,
  isPreconditionRequired,
  itemsOf,
  preconditionHeaders,
  type Page,
} from './envelope';
import { ApiError, kindForStatus, parseProblem } from './errors';

function problemError(status: number, body: Record<string, unknown> = {}): ApiError {
  const problem = parseProblem({ type: 'about:blank', status, ...body });
  return new ApiError({ kind: kindForStatus(status, problem?.code), status, problem });
}

describe('preconditionHeaders (contract 1.4)', () => {
  it('sends If-Match with an etag, If-None-Match: * for a never-saved singleton, nothing otherwise', () => {
    expect(preconditionHeaders('"0a00c1d2"')).toEqual({ 'If-Match': '"0a00c1d2"' });
    expect(preconditionHeaders(null)).toEqual({ 'If-None-Match': '*' });
    expect(preconditionHeaders(undefined)).toEqual({});
  });

  it('reads the ETag header as it is', () => {
    expect(etagFromResponse(new Response(null, { headers: { ETag: '"7"' } }))).toBe('"7"');
    expect(etagFromResponse(new Response(null))).toBeUndefined();
  });
});

describe('412 conflicts', () => {
  it('reads who saved the current version, when, and its etag', () => {
    const error = problemError(412, {
      code: 'etag_mismatch',
      current: {
        etag: '"9"',
        updatedAt: '2026-11-26T15:04:05.123456Z',
        updatedBy: 'pipeline',
        version: 4,
        status: 'in_review',
        extra: 'ignored',
      },
    });
    expect(isEtagMismatch(error)).toBe(true);
    expect(conflictOf(error)).toEqual({
      etag: '"9"',
      updatedAt: '2026-11-26T15:04:05.123456Z',
      updatedBy: 'pipeline',
      version: 4,
      status: 'in_review',
    });
  });

  it('is empty when the route did not know the document, and undefined for other errors', () => {
    expect(conflictOf(problemError(412, { code: 'etag_mismatch' }))).toEqual({});
    expect(conflictOf(problemError(412, { code: 'etag_mismatch', current: { version: 1.5, etag: '' } }))).toEqual({});
    expect(conflictOf(problemError(409, { code: 'conflict' }))).toBeUndefined();
    expect(conflictOf(new Error('412'))).toBeUndefined();
  });

  it('tells a missing precondition (a bug) from a stale one', () => {
    expect(isPreconditionRequired(problemError(428, { code: 'if_match_required' }))).toBe(true);
    expect(isPreconditionRequired(problemError(412, { code: 'etag_mismatch' }))).toBe(false);
    expect(isEtagMismatch(problemError(428, { code: 'if_match_required' }))).toBe(false);
  });
});

describe('itemsOf', () => {
  it('drops the envelopes of a page', () => {
    const page: Page<{ id: string }> = { items: [{ item: { id: 'a' }, etag: '"1"' }, { item: { id: 'b' }, etag: '"2"' }], nextCursor: null };
    expect(itemsOf(page)).toEqual([{ id: 'a' }, { id: 'b' }]);
  });
});
