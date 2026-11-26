import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';

import { meQuery } from '@/features/system/queries';
import { unwrap } from '@/lib/api/client';
import { ApiError } from '@/lib/api/errors';
import { createTestAppData } from '@/test/app-data';
import { apiUrl, problem, server, TEST_ME } from '@/test/msw';

import { createQueryClient, QUERY_STALE_TIME_MS } from './query-client';

/** Answers with each response in turn, repeating the last; counts the requests. */
function sequence(...responses: (() => Response)[]) {
  const handler = vi.fn(() => {
    const next = responses[Math.min(handler.mock.calls.length - 1, responses.length - 1)];
    if (!next) throw new Error('no responses');
    return next();
  });
  return handler;
}

describe('createQueryClient', () => {
  it('retries a 5xx with backoff and succeeds when the server recovers', async () => {
    const me = sequence(
      () => problem(503),
      () => problem(502),
      () => HttpResponse.json(TEST_ME),
    );
    server.use(http.get(apiUrl('/api/me'), me));
    const { api, queryClient } = createTestAppData();
    await expect(queryClient.query(meQuery(api))).resolves.toEqual(TEST_ME);
    expect(me).toHaveBeenCalledTimes(3);
  });

  it('retries "no response" too', async () => {
    const me = sequence(
      () => HttpResponse.error(),
      () => HttpResponse.json(TEST_ME),
    );
    server.use(http.get(apiUrl('/api/me'), me));
    const { api, queryClient } = createTestAppData();
    await expect(queryClient.query(meQuery(api))).resolves.toEqual(TEST_ME);
    expect(me).toHaveBeenCalledTimes(2);
  });

  it('stops after three retries (four attempts) of a 5xx', async () => {
    const me = sequence(() => problem(500));
    server.use(http.get(apiUrl('/api/me'), me));
    const { api, queryClient } = createTestAppData();
    await expect(queryClient.query(meQuery(api))).rejects.toMatchObject({ kind: 'server', status: 500 });
    expect(me).toHaveBeenCalledTimes(4);
  });

  it.each([
    [401, 'unauthenticated'],
    [403, 'forbidden'],
    [404, 'not-found'],
    [422, 'client'],
  ] as const)('does not retry a %i', async (status, kind) => {
    const me = sequence(() => problem(status));
    server.use(http.get(apiUrl('/api/me'), me));
    const { api, queryClient } = createTestAppData();
    await expect(queryClient.query(meQuery(api))).rejects.toMatchObject({ kind, status });
    expect(me).toHaveBeenCalledTimes(1);
  });

  it('waits 1 s, 2 s, then 4 s between retries by default', () => {
    const queryClient = createQueryClient({ random: () => 0.5 });
    const retryDelay = queryClient.getDefaultOptions().queries?.retryDelay;
    if (typeof retryDelay !== 'function') throw new Error('retryDelay should be a function');
    const error = new ApiError({ kind: 'server', status: 503 });
    expect([0, 1, 2, 10].map((failures) => retryDelay(failures, error))).toEqual([1000, 2000, 4000, 30_000]);
  });

  it('keeps data fresh for a while, does not refetch on focus by default, and never retries writes', async () => {
    const queryClient = createQueryClient();
    const defaults = queryClient.getDefaultOptions();
    expect(defaults.queries?.staleTime).toBe(QUERY_STALE_TIME_MS);
    expect(defaults.queries?.refetchOnWindowFocus).toBe(false);
    expect(defaults.mutations?.retry).toBe(false);

    const write = sequence(() => problem(503));
    server.use(http.get(apiUrl('/api/me'), write));
    const { api } = createTestAppData();
    const mutation = queryClient.getMutationCache().build(queryClient, {
      mutationFn: () => unwrap(api.GET('/api/me')),
    });
    await expect(mutation.execute(undefined)).rejects.toMatchObject({ kind: 'server' });
    expect(write).toHaveBeenCalledTimes(1);
  });
});
