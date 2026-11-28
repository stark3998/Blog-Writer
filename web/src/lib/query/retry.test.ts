// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { ApiError, kindForStatus } from '@/lib/api/errors';

import { isRetryableError, MAX_QUERY_RETRIES, shouldRetryQuery } from './retry';

function httpError(status: number, code?: string): ApiError {
  return new ApiError({ kind: kindForStatus(status, code), status, problem: { code } });
}

describe('query retry rules', () => {
  it.each([401, 403, 404, 400, 409, 412, 422])('never retries a %i', (status) => {
    expect(isRetryableError(httpError(status))).toBe(false);
    expect(shouldRetryQuery(0, httpError(status))).toBe(false);
  });

  it('never retries a sign-in redirect or a CSRF failure', () => {
    expect(shouldRetryQuery(0, new ApiError({ kind: 'unauthenticated', status: 0 }))).toBe(false);
    expect(shouldRetryQuery(0, httpError(403, 'csrf_failed'))).toBe(false);
  });

  it.each([500, 502, 503, 504, 408, 429])('retries a %i', (status) => {
    expect(shouldRetryQuery(0, httpError(status))).toBe(true);
  });

  it('retries a request that got no response', () => {
    expect(shouldRetryQuery(0, new ApiError({ kind: 'network', status: 0 }))).toBe(true);
  });

  it('gives up after three retries', () => {
    expect(MAX_QUERY_RETRIES).toBe(3);
    const error = httpError(503);
    expect([0, 1, 2, 3, 4].map((failures) => shouldRetryQuery(failures, error))).toEqual([true, true, true, false, false]);
  });

  it('does not retry errors that are not API errors (bugs)', () => {
    expect(shouldRetryQuery(0, new TypeError('x is undefined'))).toBe(false);
    expect(shouldRetryQuery(0, new SyntaxError('Unexpected token <'))).toBe(false);
  });
});
