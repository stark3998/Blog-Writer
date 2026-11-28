// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { ApiError, kindForStatus, parseProblem } from './errors';
import {
  confidentialFindings,
  fieldErrors,
  isProblem,
  isSessionError,
  isSilentError,
  missingFields,
  PROBLEM_MESSAGES,
  problemCode,
  problemMessage,
  readOnlyState,
  refusedTransition,
} from './problems';

/** An ApiError as the client builds it from a problem response body. */
function problemError(status: number, body: Record<string, unknown> = {}): ApiError {
  const problem = parseProblem({ type: 'about:blank', title: 'Error', status, request_id: 'req-1', ...body });
  return new ApiError({ kind: kindForStatus(status, problem?.code), status, problem });
}

describe('parseProblem', () => {
  it('keeps the extension members apart from the standard ones', () => {
    expect(parseProblem({ type: 'about:blank', status: 409, code: 'meta_incomplete', missing: ['title'] })).toEqual({
      type: 'about:blank',
      title: undefined,
      status: 409,
      detail: undefined,
      code: 'meta_incomplete',
      requestId: undefined,
      extensions: { missing: ['title'] },
    });
    expect(parseProblem({ status: 500, code: 'internal_error' })).not.toHaveProperty('extensions');
  });
});

describe('problemMessage', () => {
  it('prefers the server’s owner-facing detail', () => {
    const error = problemError(409, { code: 'slot_conflict', detail: 'Tuesday 10:00 already has a post.' });
    expect(problemMessage(error)).toBe('Tuesday 10:00 already has a post.');
  });

  it('falls back to a sentence for the code, then for the status', () => {
    expect(problemMessage(problemError(409, { code: 'blocklist_not_configured' }))).toBe(
      PROBLEM_MESSAGES.blocklist_not_configured,
    );
    expect(problemMessage(problemError(503, { code: 'render_unavailable' }))).toBe(
      'That service isn’t available right now. Try again in a moment.',
    );
    expect(problemMessage(problemError(409, { code: 'something_new' }))).toBe(
      'It can’t be done in its current state. Reload and try again.',
    );
    expect(problemMessage(problemError(412))).toBe(PROBLEM_MESSAGES.etag_mismatch);
    expect(problemMessage(problemError(429))).toBe('Too many requests at once. Wait a moment and try again.');
    expect(problemMessage(problemError(502))).toBe('The server had a problem. Try again in a moment.');
    expect(problemMessage(problemError(404))).toBe(PROBLEM_MESSAGES.not_found);
  });

  it('keeps the session and connection sentences, and is generic for bugs', () => {
    expect(problemMessage(new ApiError({ kind: 'network', status: 0 }))).toBe('Can’t reach the server. Check your connection.');
    expect(problemMessage(problemError(403, { code: 'not_owner', detail: 'x' }))).toBe(
      'This account isn’t allowed to use Blog Writer.',
    );
    expect(problemMessage(new TypeError('x is undefined'))).toBe('Something went wrong.');
  });
});

describe('problem classification', () => {
  it('matches codes and tells session and silent errors apart', () => {
    const error = problemError(412, { code: 'etag_mismatch' });
    expect(problemCode(error)).toBe('etag_mismatch');
    expect(isProblem(error, 'etag_mismatch')).toBe(true);
    expect(isProblem(error, ['confidential_text', 'etag_mismatch'])).toBe(true);
    expect(isProblem(error, [])).toBe(false);
    expect(isProblem(new Error('etag_mismatch'), 'etag_mismatch')).toBe(false);
    expect(isSessionError(problemError(401, { code: 'unauthenticated' }))).toBe(true);
    expect(isSessionError(problemError(403, { code: 'not_owner' }))).toBe(true);
    expect(isSessionError(problemError(403, { code: 'csrf_failed' }))).toBe(false);
    expect(isSilentError(Object.assign(new Error('aborted'), { name: 'AbortError' }))).toBe(true);
  });
});

describe('extension readers', () => {
  it('maps validation errors to body fields', () => {
    const error = problemError(422, {
      code: 'validation_failed',
      errors: [
        { loc: ['body', 'title'], msg: 'Field required', type: 'missing' },
        { loc: ['body', 'meta', 'description'], msg: 'Too long', type: 'string_too_long' },
        { loc: ['body', 'slots', 0, 'time'], msg: 'Invalid time', type: 'value_error' },
        { loc: ['body', 'title'], msg: 'A second message', type: 'x' },
        { loc: ['query', 'limit'], msg: 'Too large', type: 'less_than_equal' },
        'junk',
      ],
    });
    expect(fieldErrors(error)).toEqual({
      title: 'Field required',
      'meta.description': 'Too long',
      'slots.0.time': 'Invalid time',
      limit: 'Too large',
    });
    expect(fieldErrors(problemError(409, { code: 'conflict' }))).toEqual({});
  });

  it('reads blocklist findings by field and span, never needing the term', () => {
    const error = problemError(422, {
      code: 'confidential_text',
      findings: [
        { field: 'instruction', code: 'blocklist', severity: 'block', message: 'Matches a blocked name.', start: 4, end: 11, ref: 'term-3' },
        { field: 'answers.1', code: 'blocklist', severity: 'block', message: 'Matches a blocked name.' },
        { nope: true },
      ],
    });
    expect(confidentialFindings(error)).toEqual([
      { field: 'instruction', code: 'blocklist', severity: 'block', message: 'Matches a blocked name.', start: 4, end: 11, ref: 'term-3' },
      { field: 'answers.1', code: 'blocklist', severity: 'block', message: 'Matches a blocked name.' },
    ]);
  });

  it('reads missing fields, refused transitions and read-only states', () => {
    expect(missingFields(problemError(409, { code: 'meta_incomplete', missing: ['title', 'slug', 3] }))).toEqual([
      'title',
      'slug',
    ]);
    expect(
      refusedTransition(
        problemError(409, { code: 'invalid_transition', machine: 'article', from: 'live', to: 'drafting', allowed: ['unpublishing'] }),
      ),
    ).toEqual({ machine: 'article', from: 'live', to: 'drafting', allowed: ['unpublishing'] });
    // `status` is the problem's own HTTP status, so the document's status comes as `state`.
    expect(readOnlyState(problemError(409, { code: 'read_only_state', state: 'drafting', writer: 'pipeline' }))).toEqual({
      state: 'drafting',
      writer: 'pipeline',
    });
    expect(refusedTransition(problemError(409, { code: 'conflict' }))).toBeUndefined();
  });
});
