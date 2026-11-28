import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import { describe, expect, expectTypeOf, it, vi } from 'vitest';

import { DataProvider } from '@/features/data/data-provider';
import { createApiClient } from '@/lib/api/client';
import { ApiError } from '@/lib/api/errors';
import { createTestAppData, seedSession } from '@/test/app-data';
import type { TestPaths, TestThing, TestVersioned } from '@/test/api-paths';
import { apiUrl, problem, server, TEST_ME } from '@/test/msw';

import {
  apiInfiniteQueryOptions,
  apiMutationOptions,
  apiQueryOptions,
  useApiInfiniteQuery,
  useApiMutation,
  useApiQuery,
} from './api-query';
import { entityKeys, queryKeys } from './keys';

const things = entityKeys('things');

function thing(id: string, title = `Thing ${id}`): TestThing {
  return { id, title, status: 'draft', updatedAt: '2026-11-26T15:00:00.000000Z' };
}

function setup() {
  const appData = createTestAppData();
  const api = createApiClient<TestPaths>({ auth: appData.auth });
  const wrapper = ({ children }: { children: ReactNode }) => <DataProvider appData={appData}>{children}</DataProvider>;
  return { appData, api, wrapper };
}

describe('apiQueryOptions', () => {
  it('fetches one document under its contract key, with typed data', async () => {
    server.use(
      http.get(apiUrl('/api/things/:id'), ({ params }) =>
        HttpResponse.json({ item: thing(String(params.id)), etag: '"1"' }, { headers: { ETag: '"1"' } }),
      ),
    );
    const { api, wrapper, appData } = setup();
    const { result } = renderHook(
      () =>
        useQuery(
          apiQueryOptions(api, {
            queryKey: things.detail('thing-1'),
            path: '/api/things/{id}',
            init: { params: { path: { id: 'thing-1' } } },
          }),
        ),
      { wrapper },
    );
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expectTypeOf(result.current.data).toEqualTypeOf<TestVersioned<TestThing> | undefined>();
    expect(result.current.data).toEqual({ item: thing('thing-1'), etag: '"1"' });
    expect(appData.queryClient.getQueryData(['things', 'detail', 'thing-1'])).toEqual(result.current.data);
  });

  it('fails with the ApiError and does not retry a 404', async () => {
    const get = vi.fn(() => problem(404, 'not_found', 'req-404'));
    server.use(http.get(apiUrl('/api/things/:id'), get));
    const { api, wrapper } = setup();
    const { result } = renderHook(
      () =>
        useQuery(
          apiQueryOptions(api, {
            queryKey: things.detail('gone'),
            path: '/api/things/{id}',
            init: { params: { path: { id: 'gone' } } },
          }),
        ),
      { wrapper },
    );
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.error).toMatchObject({ kind: 'not-found', code: 'not_found', requestId: 'req-404' });
    expect(get).toHaveBeenCalledTimes(1);
  });
});

describe('argument types', () => {
  it('require init exactly when the route needs parameters', () => {
    const { api } = setup();
    // Never called: these lines only have to compile, or fail to.
    const typeOnly = () => {
      apiQueryOptions(api, { queryKey: things.list(), path: '/api/things' });
      // @ts-expect-error: the path parameter is required
      apiQueryOptions(api, { queryKey: things.detail('x'), path: '/api/things/{id}' });
      // @ts-expect-error: not a GET route
      apiQueryOptions(api, { queryKey: things.all, path: '/api/things/pause' });
      apiMutationOptions(api, 'put', '/api/things/{id}', {
        updates: (data) => (data.item.status === 'draft' ? things.detail(data.item.id) : undefined),
      });
    };
    expect(typeOnly).toBeTypeOf('function');
  });
});

describe('useApiQuery', () => {
  it('uses the app’s client and accepts query options such as select', async () => {
    const appData = createTestAppData();
    const wrapper = ({ children }: { children: ReactNode }) => <DataProvider appData={appData}>{children}</DataProvider>;
    const { result } = renderHook(
      () => useApiQuery({ queryKey: queryKeys.me, path: '/api/me' }, { select: (me) => me.name, staleTime: 0 }),
      { wrapper },
    );
    await waitFor(() => {
      expect(result.current.data).toBe(TEST_ME.name);
    });
    expectTypeOf(result.current.data).toEqualTypeOf<string | null | undefined>();
  });
});

describe('apiInfiniteQueryOptions', () => {
  it('pages with limit and cursor until nextCursor runs out, and flattens the entries', async () => {
    const urls: string[] = [];
    server.use(
      http.get(apiUrl('/api/things'), ({ request }) => {
        urls.push(request.url);
        const cursor = new URL(request.url).searchParams.get('cursor');
        return cursor === null
          ? HttpResponse.json({ items: [{ item: thing('a'), etag: '"a"' }, { item: thing('b'), etag: '"b"' }], nextCursor: 'b64' })
          : HttpResponse.json({ items: [{ item: thing('c'), etag: '"c"' }], nextCursor: null });
      }),
    );
    const { api, wrapper } = setup();
    const { result } = renderHook(
      () =>
        useInfiniteQuery({
          ...apiInfiniteQueryOptions(api, {
            queryKey: things.list({ status: 'draft' }),
            path: '/api/things',
            init: { params: { query: { status: 'draft' } } },
            pageSize: 2,
          }),
        }),
      { wrapper },
    );
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(result.current.hasNextPage).toBe(true);
    await act(async () => {
      await result.current.fetchNextPage();
    });
    // TanStack notifies observers on its next tick.
    await waitFor(() => {
      expect(result.current.hasNextPage).toBe(false);
    });
    expect(result.current.data?.pages.flatMap((page) => page.items.map((entry) => entry.item.id))).toEqual(['a', 'b', 'c']);
    expect(urls).toEqual([apiUrl('/api/things?status=draft&limit=2'), apiUrl('/api/things?status=draft&limit=2&cursor=b64')]);
  });
});

describe('the hooks bound to the app’s client', () => {
  // Today's schema has no list or write routes, so these borrow test routes at runtime only.
  const listPath = '/api/things' as unknown as '/api/me';

  it('useApiInfiniteQuery gives every loaded entry in order', async () => {
    server.use(
      http.get(apiUrl('/api/things'), ({ request }) =>
        new URL(request.url).searchParams.get('cursor') === null
          ? HttpResponse.json({ items: [{ item: thing('a'), etag: '"a"' }], nextCursor: 'next' })
          : HttpResponse.json({ items: [{ item: thing('b'), etag: '"b"' }], nextCursor: null }),
      ),
    );
    const { wrapper } = setup();
    const { result } = renderHook(() => useApiInfiniteQuery({ queryKey: things.list(), path: listPath, pageSize: 1 }), {
      wrapper,
    });
    await waitFor(() => {
      expect(result.current.data).toEqual([{ item: thing('a'), etag: '"a"' }]);
    });
    await act(async () => {
      await result.current.fetchNextPage();
    });
    await waitFor(() => {
      expect(result.current.data).toEqual([
        { item: thing('a'), etag: '"a"' },
        { item: thing('b'), etag: '"b"' },
      ]);
    });
    expect(result.current.hasNextPage).toBe(false);
  });

  it('useApiMutation writes through the app’s client', async () => {
    server.use(http.post(apiUrl('/api/things/pause'), () => HttpResponse.json({ jobId: 'job-1', status: 'queued' }, { status: 202 })));
    const { wrapper } = setup();
    const { result } = renderHook(() => useApiMutation('post' as never, '/api/things/pause' as never), { wrapper });
    let accepted: unknown;
    await act(async () => {
      accepted = await result.current.mutateAsync(undefined);
    });
    expect(accepted).toEqual({ jobId: 'job-1', status: 'queued' });
  });
});

describe('apiMutationOptions', () => {
  function useSave(api: ReturnType<typeof setup>['api'], extras: Parameters<typeof apiMutationOptions<TestPaths, 'put', '/api/things/{id}'>>[3] = {}) {
    return useMutation(apiMutationOptions(api, 'put', '/api/things/{id}', extras));
  }

  it('sends If-Match from ifMatch, updates the detail cache and invalidates the lists', async () => {
    const seen: Request[] = [];
    server.use(
      http.put(apiUrl('/api/things/:id'), async ({ request, params }) => {
        seen.push(request.clone());
        const body = (await request.json()) as { title: string };
        return HttpResponse.json({ item: thing(String(params.id), body.title), etag: '"2"' });
      }),
    );
    const { api, wrapper, appData } = setup();
    const invalidate = vi.spyOn(appData.queryClient, 'invalidateQueries');
    const success = vi.spyOn(toast, 'success').mockReturnValue(1);
    const { result } = renderHook(
      () =>
        useSave(api, {
          updates: (_data, variables) => things.detail(variables.params.path.id),
          invalidates: [things.lists(), queryKeys.today],
          successToast: 'Saved.',
        }),
      { wrapper },
    );
    await act(async () => {
      await result.current.mutateAsync({ params: { path: { id: 'thing-1' } }, body: { title: 'Renamed' }, ifMatch: '"1"' });
    });
    expect(seen[0]?.headers.get('If-Match')).toBe('"1"');
    expect(seen[0]?.headers.get('If-None-Match')).toBeNull();
    expect(appData.queryClient.getQueryData(things.detail('thing-1'))).toEqual({ item: thing('thing-1', 'Renamed'), etag: '"2"' });
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([['things', 'list'], ['today']]);
    expect(success).toHaveBeenCalledExactlyOnceWith('Saved.', {});
  });

  it('sends If-None-Match: * for the first save of a singleton (etag null)', async () => {
    const seen: Request[] = [];
    server.use(
      http.put(apiUrl('/api/things/:id'), ({ request }) => {
        seen.push(request.clone());
        return HttpResponse.json({ item: thing('pillars'), etag: '"1"' });
      }),
    );
    const { api, wrapper } = setup();
    const { result } = renderHook(() => useSave(api), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ params: { path: { id: 'pillars' } }, body: { title: 'x' }, ifMatch: null });
    });
    expect(seen[0]?.headers.get('If-None-Match')).toBe('*');
    expect(seen[0]?.headers.get('If-Match')).toBeNull();
  });

  it('toasts an error with its title and request id, except codes the screen handles itself', async () => {
    server.use(http.put(apiUrl('/api/things/:id'), () => problem(412, 'etag_mismatch', 'req-412')));
    const { api, wrapper } = setup();
    const error = vi.spyOn(toast, 'error').mockReturnValue(1);
    const onError = vi.fn();

    const toasted = renderHook(() => useSave(api, { errorToast: 'Couldn’t save the thing.', onError }), { wrapper });
    await act(async () => {
      await toasted.result.current
        .mutateAsync({ params: { path: { id: 'thing-1' } }, body: { title: 'x' }, ifMatch: '"0"' })
        .catch(() => undefined);
    });
    expect(error).toHaveBeenCalledExactlyOnceWith('Couldn’t save the thing.', {
      description: 'It changed since you loaded it. Reload, compare and save again. Request ID req-412.',
      id: undefined,
      action: undefined,
    });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]?.[0]).toMatchObject({ status: 412, code: 'etag_mismatch' });

    error.mockClear();
    const handled = renderHook(() => useSave(api, { handles: ['etag_mismatch'] }), { wrapper });
    await act(async () => {
      await handled.result.current
        .mutateAsync({ params: { path: { id: 'thing-1' } }, body: { title: 'x' }, ifMatch: '"0"' })
        .catch(() => undefined);
    });
    expect(error).not.toHaveBeenCalled();
    expect(handled.result.current.error).toMatchObject({ status: 412 });
  });

  it('can be called without variables when the route needs none', async () => {
    server.use(http.post(apiUrl('/api/things/pause'), () => HttpResponse.json({ jobId: 'job-9', status: 'queued' }, { status: 202 })));
    const { api, wrapper } = setup();
    const { result } = renderHook(() => useMutation(apiMutationOptions(api, 'post', '/api/things/pause')), { wrapper });
    let accepted: unknown;
    await act(async () => {
      accepted = await result.current.mutateAsync();
    });
    expect(accepted).toEqual({ jobId: 'job-9', status: 'queued' });
  });

  it('never retries a write, and stays quiet for session errors', async () => {
    const put = vi.fn(() => problem(401, 'unauthenticated'));
    server.use(http.put(apiUrl('/api/things/:id'), put));
    const { api, wrapper, appData } = setup();
    seedSession(appData);
    const error = vi.spyOn(toast, 'error').mockReturnValue(1);
    const { result } = renderHook(() => useSave(api), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ params: { path: { id: 'thing-1' } }, body: { title: 'x' } }).catch(() => undefined);
    });
    expect(put).toHaveBeenCalledTimes(1);
    expect(error).not.toHaveBeenCalled();
  });
});
