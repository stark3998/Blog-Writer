// @vitest-environment node
import { toast } from 'sonner';
import { describe, expect, it, vi } from 'vitest';

import { ApiError, kindForStatus, parseProblem } from './errors';
import { requestIdLine, shouldToastError, toastError, toastSuccess } from './toast';

function problemError(status: number, body: Record<string, unknown> = {}, requestId?: string): ApiError {
  const problem = parseProblem({ type: 'about:blank', status, ...body });
  return new ApiError({ kind: kindForStatus(status, problem?.code), status, problem, requestId });
}

describe('toastError', () => {
  it('shows the owner-facing sentence and the request id', () => {
    const error = vi.spyOn(toast, 'error').mockReturnValue(1);
    toastError(problemError(409, { code: 'slot_conflict' }, 'req-42'));
    expect(error).toHaveBeenCalledExactlyOnceWith('Another post already has that slot. Pick another one.', {
      description: 'Request ID req-42.',
      id: undefined,
      action: undefined,
    });
  });

  it('puts the action’s own title first and the reason after it', () => {
    const error = vi.spyOn(toast, 'error').mockReturnValue(1);
    const retry = { label: 'Try again', onClick: vi.fn() };
    toastError(problemError(503, { code: 'store_unavailable' }), { title: 'Couldn’t save the pillars.', action: retry });
    expect(error).toHaveBeenCalledExactlyOnceWith('Couldn’t save the pillars.', {
      description: 'The database is busy. Try again in a moment.',
      id: undefined,
      action: retry,
    });
  });

  it('shows one "no connection" toast for a burst, and none for session errors or cancelled requests', () => {
    const error = vi.spyOn(toast, 'error').mockReturnValue(1);
    toastError(new ApiError({ kind: 'network', status: 0 }));
    expect(error.mock.calls[0]?.[1]).toMatchObject({ id: 'api-network-error' });
    error.mockClear();
    toastError(problemError(401, { code: 'unauthenticated' }));
    toastError(problemError(403, { code: 'not_owner' }));
    toastError(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    expect(error).not.toHaveBeenCalled();
    expect(shouldToastError(problemError(403, { code: 'csrf_failed' }))).toBe(true);
  });

  it('formats the request id line only when there is one', () => {
    expect(requestIdLine(problemError(500, {}, 'req-9'))).toBe('Request ID req-9.');
    expect(requestIdLine(problemError(500))).toBeUndefined();
    expect(requestIdLine(new Error('x'))).toBeUndefined();
  });
});

describe('toastSuccess', () => {
  it('says what happened in the action’s words', () => {
    const success = vi.spyOn(toast, 'success').mockReturnValue(1);
    toastSuccess('Pillars saved.');
    expect(success).toHaveBeenCalledExactlyOnceWith('Pillars saved.', {});
  });
});
