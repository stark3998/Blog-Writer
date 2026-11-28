import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';

import { createTestAppData } from '@/test/app-data';
import { apiUrl, problem, server, TEST_ME } from '@/test/msw';

import { createApiClient, REQUEST_DEFAULTS, unwrap } from './client';
import { ApiError } from './errors';

/** A write endpoint for the CSRF tests; the Phase 1 schema has none yet. */
interface WritePaths {
  '/api/things': {
    post: {
      requestBody: { content: { 'application/json': { name: string } } };
      responses: { 200: { content: { 'application/json': { id: string } } } };
    };
  };
}

/** Node's fetch returns the 3xx itself for `redirect: 'manual'`; browsers return this instead. */
function opaqueRedirect(): Response {
  const response = new Response(null);
  Object.defineProperties(response, {
    type: { value: 'opaqueredirect' },
    status: { value: 0 },
    ok: { value: false },
  });
  return response;
}

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error('expected the call to fail');
}

describe('createApiClient', () => {
  it('sends same-origin requests with cookies and never follows redirects', async () => {
    const seen: Request[] = [];
    const { auth } = createTestAppData();
    const api = createApiClient({
      auth,
      fetch: (request) => {
        seen.push(request);
        return Promise.resolve(Response.json(TEST_ME));
      },
    });
    await expect(unwrap(api.GET('/api/me'))).resolves.toEqual(TEST_ME);
    const [request] = seen;
    expect(request?.url).toBe(apiUrl('/api/me'));
    expect(request?.credentials).toBe('same-origin');
    expect(request?.redirect).toBe('manual');
    expect(request?.headers.get('Accept')).toBe('application/json');
    expect(REQUEST_DEFAULTS).toEqual({ credentials: 'same-origin', redirect: 'manual' });
  });

  it('returns typed data for the Phase 1 endpoints', async () => {
    const { api } = createTestAppData();
    await expect(unwrap(api.GET('/api/me'))).resolves.toEqual(TEST_ME);
    await expect(unwrap(api.GET('/api/health'))).resolves.toMatchObject({ status: 'ok' });
  });
});

describe('a missing session', () => {
  it('turns a 401 into a top-level navigation to sign in, back to the current path', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401, 'unauthenticated')));
    const appData = createTestAppData({ currentPath: () => '/studio/piece-1/draft?x=1' });
    const error = await rejection(unwrap(appData.api.GET('/api/me')));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: 'unauthenticated', status: 401, code: 'unauthenticated' });
    expect(appData.navigate).toHaveBeenCalledExactlyOnceWith(
      '/.auth/login/aad?post_login_redirect_uri=%2Fstudio%2Fpiece-1%2Fdraft%3Fx%3D1',
    );
    expect(appData.auth.getState()).toEqual({ status: 'signing-in' });
  });

  it('treats Easy Auth redirecting to its login page as signed out (Node sees the 302)', async () => {
    server.use(
      http.get(
        apiUrl('/api/me'),
        () => new HttpResponse(null, { status: 302, headers: { Location: 'https://login.microsoftonline.com/' } }),
      ),
    );
    const appData = createTestAppData({ currentPath: () => '/today' });
    await expect(unwrap(appData.api.GET('/api/me'))).rejects.toMatchObject({ kind: 'unauthenticated' });
    expect(appData.navigate).toHaveBeenCalledExactlyOnceWith('/.auth/login/aad?post_login_redirect_uri=%2Ftoday');
  });

  it('treats an opaque redirect (what browsers return) as signed out', async () => {
    const appData = createTestAppData({ currentPath: () => '/today' });
    const api = createApiClient({ auth: appData.auth, fetch: () => Promise.resolve(opaqueRedirect()) });
    await expect(unwrap(api.GET('/api/me'))).rejects.toMatchObject({ kind: 'unauthenticated', status: 0 });
    expect(appData.navigate).toHaveBeenCalledExactlyOnceWith('/.auth/login/aad?post_login_redirect_uri=%2Ftoday');
  });

  it('redirects once even when several calls fail together', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401)), http.get(apiUrl('/api/health'), () => problem(401)));
    const appData = createTestAppData();
    await Promise.allSettled([unwrap(appData.api.GET('/api/me')), unwrap(appData.api.GET('/api/health'))]);
    expect(appData.navigate).toHaveBeenCalledTimes(1);
  });

  it('stops after three sign-in round trips and shows the blocked state instead of looping', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401)));
    // Each round trip through the sign-in page is a fresh page load with a fresh app.
    for (let load = 0; load < 3; load += 1) {
      const appData = createTestAppData();
      await expect(unwrap(appData.api.GET('/api/me'))).rejects.toBeInstanceOf(ApiError);
      expect(appData.navigate).toHaveBeenCalledTimes(1);
    }
    const looping = createTestAppData();
    await expect(unwrap(looping.api.GET('/api/me'))).rejects.toBeInstanceOf(ApiError);
    expect(looping.navigate).not.toHaveBeenCalled();
    expect(looping.auth.getState()).toEqual({ status: 'sign-in-blocked', reason: 'loop' });
  });
});

describe('a refused account', () => {
  it('shows "not authorised" for a 403 not_owner, with the request id, and never redirects', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(403, 'not_owner', 'req-403')));
    const appData = createTestAppData();
    const error = await rejection(unwrap(appData.api.GET('/api/me')));
    expect(error).toMatchObject({ kind: 'forbidden', status: 403, code: 'not_owner', requestId: 'req-403' });
    expect(appData.auth.getState()).toEqual({ status: 'forbidden', code: 'not_owner', requestId: 'req-403' });
    expect(appData.navigate).not.toHaveBeenCalled();
  });

  it('treats Easy Auth’s own 403 page (not JSON) the same way', async () => {
    server.use(
      http.get(
        apiUrl('/api/me'),
        () => new HttpResponse('<h1>403 Forbidden</h1>', { status: 403, headers: { 'Content-Type': 'text/html' } }),
      ),
    );
    const appData = createTestAppData();
    await expect(unwrap(appData.api.GET('/api/me'))).rejects.toMatchObject({ kind: 'forbidden', code: undefined });
    expect(appData.auth.getState()).toMatchObject({ status: 'forbidden' });
    expect(appData.navigate).not.toHaveBeenCalled();
  });
});

describe('CSRF failures on writes', () => {
  it('refreshes the app cookie with GET /api/me, then sends the write once more', async () => {
    const calls: string[] = [];
    const bodies: unknown[] = [];
    let attempts = 0;
    server.use(
      http.get(apiUrl('/api/me'), () => {
        calls.push('GET /api/me');
        return HttpResponse.json(TEST_ME);
      }),
      http.post(apiUrl('/api/things'), async ({ request }) => {
        calls.push('POST /api/things');
        bodies.push(await request.json());
        attempts += 1;
        return attempts === 1 ? problem(403, 'csrf_failed') : HttpResponse.json({ id: 'thing-1' });
      }),
    );
    const appData = createTestAppData();
    const api = createApiClient<WritePaths>({ auth: appData.auth });
    await expect(unwrap(api.POST('/api/things', { body: { name: 'draft' } }))).resolves.toEqual({ id: 'thing-1' });
    expect(calls).toEqual(['POST /api/things', 'GET /api/me', 'POST /api/things']);
    expect(bodies).toEqual([{ name: 'draft' }, { name: 'draft' }]);
    expect(appData.auth.getState()).toEqual({ status: 'ok' });
  });

  it('retries only once, and a CSRF failure is never shown as "not authorised"', async () => {
    const post = vi.fn(() => problem(403, 'csrf_failed'));
    server.use(http.post(apiUrl('/api/things'), post));
    const appData = createTestAppData();
    const api = createApiClient<WritePaths>({ auth: appData.auth });
    await expect(unwrap(api.POST('/api/things', { body: { name: 'draft' } }))).rejects.toMatchObject({
      kind: 'csrf',
      status: 403,
      code: 'csrf_failed',
    });
    expect(post).toHaveBeenCalledTimes(2);
    expect(appData.auth.getState()).toEqual({ status: 'ok' });
  });

  it('does not retry reads, which can never fail CSRF', async () => {
    const me = vi.fn(() => problem(403, 'csrf_failed'));
    server.use(http.get(apiUrl('/api/me'), me));
    const appData = createTestAppData();
    await expect(unwrap(appData.api.GET('/api/me'))).rejects.toMatchObject({ kind: 'csrf' });
    expect(me).toHaveBeenCalledTimes(1);
  });
});

describe('other failures', () => {
  it('reports a server error with its code and the X-Request-Id header', async () => {
    server.use(http.get(apiUrl('/api/health'), () => problem(503, 'store_unavailable', 'req-503')));
    const { api, auth } = createTestAppData();
    const error = await rejection(unwrap(api.GET('/api/health')));
    expect(error).toMatchObject({ kind: 'server', status: 503, code: 'store_unavailable', requestId: 'req-503' });
    expect((error as ApiError).transient).toBe(true);
    expect((error as ApiError).message).toBe('API request failed: HTTP 503 store_unavailable');
    expect(auth.getState()).toEqual({ status: 'ok' });
  });

  it('turns "no response" into a network ApiError', async () => {
    server.use(http.get(apiUrl('/api/me'), () => HttpResponse.error()));
    const { api, auth } = createTestAppData();
    const error = await rejection(unwrap(api.GET('/api/me')));
    expect(error).toMatchObject({ kind: 'network', status: 0, transient: true });
    expect(auth.getState()).toEqual({ status: 'ok' });
  });

  it('lets a cancelled request reject with the AbortError itself', async () => {
    const { api } = createTestAppData();
    const controller = new AbortController();
    controller.abort();
    const error = await rejection(api.GET('/api/me', { signal: controller.signal }));
    expect(error).not.toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ name: 'AbortError' });
  });
});
