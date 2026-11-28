import { http, HttpResponse } from 'msw';
import { describe, expect, expectTypeOf, it } from 'vitest';

import { createTestAppData } from '@/test/app-data';
import type { TestPage, TestPaths, TestThing, TestVersioned } from '@/test/api-paths';
import { apiUrl, problem, server } from '@/test/msw';

import { createApiClient } from './client';
import { ApiError } from './errors';
import { apiRequest, apiRequestWithResponse, withHeaders, type ApiData, type ApiInit, type ApiPath } from './request';

const THING: TestThing = { id: 'thing-1', title: 'First', status: 'draft', updatedAt: '2026-11-26T15:00:00.000000Z' };

function client() {
  const { auth } = createTestAppData();
  return createApiClient<TestPaths>({ auth });
}

describe('route types', () => {
  it('are computed from the schema, so new routes type-check once schema.ts lists them', () => {
    expectTypeOf<ApiPath<'get', TestPaths>>().toEqualTypeOf<'/api/things' | '/api/things/{id}'>();
    expectTypeOf<ApiPath<'post', TestPaths>>().toEqualTypeOf<'/api/things' | '/api/things/pause'>();
    expectTypeOf<ApiPath<'put', TestPaths>>().toEqualTypeOf<'/api/things/{id}'>();
    expectTypeOf<ApiData<'get', '/api/things/{id}', TestPaths>>().toEqualTypeOf<TestVersioned<TestThing>>();
    expectTypeOf<ApiData<'get', '/api/things', TestPaths>>().toEqualTypeOf<TestPage<TestThing>>();
    expectTypeOf<NonNullable<ApiInit<'put', '/api/things/{id}', TestPaths>['body']>>().toExtend<{ title: string }>();
    expectTypeOf<{ title: string }>().toExtend<NonNullable<ApiInit<'put', '/api/things/{id}', TestPaths>['body']>>();
    // The generated schema (blogwriter.main) lists every route: Phase 1's and the packages'.
    expectTypeOf<
      '/api/health' | '/api/me' | '/api/events' | '/api/articles' | '/api/system/status'
    >().toExtend<ApiPath<'get'>>();
  });

  it('refuses unknown routes, wrong methods and missing parameters at compile time', () => {
    const api = client();
    // Never called: these lines only have to fail to compile.
    const typeOnly = () => {
      // @ts-expect-error: not a route of the schema
      void apiRequest(api, 'get', '/api/nothing');
      // @ts-expect-error: the route has no PATCH
      void apiRequest(api, 'patch', '/api/things/{id}', { params: { path: { id: 'x' } } });
      // @ts-expect-error: the path parameter is required
      void apiRequest(api, 'get', '/api/things/{id}');
      // @ts-expect-error: the body is required
      void apiRequest(api, 'post', '/api/things', {});
    };
    expect(typeOnly).toBeTypeOf('function');
  });
});

describe('apiRequest', () => {
  it('resolves with the typed body of a 2xx and sends params, body and headers', async () => {
    const seen: Request[] = [];
    server.use(
      http.put(apiUrl('/api/things/:id'), async ({ request, params }) => {
        seen.push(request.clone());
        const body = (await request.json()) as { title: string };
        return HttpResponse.json(
          { item: { ...THING, id: String(params.id), title: body.title }, etag: '"2"' },
          { headers: { ETag: '"2"' } },
        );
      }),
    );
    const api = client();
    const saved = await apiRequest(api, 'put', '/api/things/{id}', {
      params: { path: { id: 'thing-1' } },
      body: { title: 'Renamed' },
      headers: { 'If-Match': '"1"' },
    });
    expectTypeOf(saved).toEqualTypeOf<TestVersioned<TestThing>>();
    expect(saved).toEqual({ item: { ...THING, title: 'Renamed' }, etag: '"2"' });
    expect(seen[0]?.headers.get('If-Match')).toBe('"1"');
    expect(seen[0]?.url).toBe(apiUrl('/api/things/thing-1'));
  });

  it('serializes query parameters, and needs no init for routes without required parameters', async () => {
    const urls: string[] = [];
    server.use(
      http.get(apiUrl('/api/things'), ({ request }) => {
        urls.push(request.url);
        return HttpResponse.json({ items: [], nextCursor: null });
      }),
      http.post(apiUrl('/api/things/pause'), () => HttpResponse.json({ jobId: 'job-1', status: 'queued' }, { status: 202 })),
    );
    const api = client();
    await apiRequest(api, 'get', '/api/things', { params: { query: { limit: 20, status: 'draft' } } });
    await apiRequest(api, 'get', '/api/things');
    await expect(apiRequest(api, 'post', '/api/things/pause')).resolves.toEqual({ jobId: 'job-1', status: 'queued' });
    expect(urls).toEqual([apiUrl('/api/things?limit=20&status=draft'), apiUrl('/api/things')]);
  });

  it('rejects with an ApiError that keeps the problem, its extensions and the request id', async () => {
    server.use(
      http.put(apiUrl('/api/things/:id'), () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Precondition Failed',
            status: 412,
            code: 'etag_mismatch',
            detail: 'It changed since you loaded it. Reload, compare and save again.',
            request_id: 'req-412',
            current: { etag: '"9"', updatedAt: '2026-11-26T15:04:05.123456Z', updatedBy: 'pipeline', version: 4 },
          },
          { status: 412, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': 'req-412' } },
        ),
      ),
    );
    const error = await apiRequest(client(), 'put', '/api/things/{id}', {
      params: { path: { id: 'thing-1' } },
      body: { title: 'x' },
    }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: 'client', status: 412, code: 'etag_mismatch', requestId: 'req-412' });
    expect((error as ApiError).problem?.extensions).toEqual({
      current: { etag: '"9"', updatedAt: '2026-11-26T15:04:05.123456Z', updatedBy: 'pipeline', version: 4 },
    });
  });

  it('gives the response too, for its headers', async () => {
    server.use(
      http.get(apiUrl('/api/things/:id'), () =>
        HttpResponse.json({ item: THING, etag: '"1"' }, { headers: { ETag: '"1"', 'X-Request-Id': 'req-1' } }),
      ),
    );
    const { data, response } = await apiRequestWithResponse(client(), 'get', '/api/things/{id}', {
      params: { path: { id: 'thing-1' } },
    });
    expect(data.etag).toBe('"1"');
    expect(response.headers.get('ETag')).toBe('"1"');
  });

  it('treats a 204 as success without data', async () => {
    server.use(http.delete(apiUrl('/api/things/:id'), () => new HttpResponse(null, { status: 204 })));
    await expect(
      apiRequest(client(), 'delete', '/api/things/{id}', { params: { path: { id: 'thing-1' } } }),
    ).resolves.toBeUndefined();
  });

  it('reports a server error as transient', async () => {
    server.use(http.get(apiUrl('/api/things'), () => problem(503, 'store_unavailable', 'req-503')));
    await expect(apiRequest(client(), 'get', '/api/things')).rejects.toMatchObject({
      kind: 'server',
      transient: true,
      requestId: 'req-503',
    });
  });
});

describe('withHeaders', () => {
  it('adds headers to whatever form the request already has', () => {
    expect(Object.fromEntries(withHeaders({ headers: { Accept: 'text/plain' } }, { 'If-Match': '"1"' }).headers)).toEqual({
      accept: 'text/plain',
      'if-match': '"1"',
    });
    expect(Object.fromEntries(withHeaders(undefined, { 'If-None-Match': '*' }).headers)).toEqual({ 'if-none-match': '*' });
  });
});
