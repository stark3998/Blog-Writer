import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';

import { createTestAppData } from '@/test/app-data';
import { MockEventSource } from '@/test/event-source';
import { deltaPayload, jobEventPayload, snapshotPayload } from '@/test/job-events';
import { apiUrl, problem, server } from '@/test/msw';

import { createApiClient } from './api/client';
import { probeSession } from './app-data';
import { createRedirectGuard } from './auth/redirect-guard';
import { CLOSED, CONNECTING } from './events/event-stream';
import { queryKeys } from './query/keys';

describe('probeSession', () => {
  it.each([
    ['a 200', () => HttpResponse.json({ oid: 'x', name: null }), 'authenticated'],
    ['a 401', () => problem(401, 'unauthenticated'), 'logged-out'],
    [
      'a redirect to sign in',
      () => new HttpResponse(null, { status: 302, headers: { Location: '/.auth/login/aad' } }),
      'logged-out',
    ],
    ['a 403', () => problem(403, 'not_owner'), 'forbidden'],
    ['a 503', () => problem(503), 'unavailable'],
    ['no response', () => HttpResponse.error(), 'unavailable'],
  ] as const)('reads %s as %s', async (_name, respond, outcome) => {
    server.use(http.get(apiUrl('/api/me'), respond));
    const { api } = createTestAppData();
    await expect(probeSession(api)).resolves.toBe(outcome);
  });

  it('asks /api/me without the cache and without following redirects', async () => {
    const seen: Request[] = [];
    const { auth } = createTestAppData();
    const api = createApiClient({
      auth,
      fetch: (request) => {
        seen.push(request);
        return Promise.resolve(Response.json({ oid: 'x', name: null }));
      },
    });
    await probeSession(api);
    expect(seen[0]?.url).toBe(apiUrl('/api/me'));
    expect(seen[0]?.redirect).toBe('manual');
    expect(seen[0]?.cache).toBe('no-store');
  });

  it('gives up on a probe that hangs', async () => {
    server.use(http.get(apiUrl('/api/me'), () => new Promise<Response>(() => undefined)));
    const { api } = createTestAppData();
    await expect(probeSession(api, 20)).resolves.toBe('unavailable');
  });
});

describe('createAppData', () => {
  it('sends the browser to sign in when the stream drops and the session has ended', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401)));
    const appData = createTestAppData({ currentPath: () => '/today' });
    appData.events.retain();
    MockEventSource.latest().open();
    MockEventSource.latest().fail(CLOSED);
    await vi.waitFor(() => {
      expect(appData.events.getStatus()).toBe('signed-out');
    });
    expect(appData.navigate).toHaveBeenCalledExactlyOnceWith('/.auth/login/aad?post_login_redirect_uri=%2Ftoday');
    expect(appData.auth.getState()).toEqual({ status: 'signing-in' });
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it('keeps to the sign-in loop guard when the stream finds the session gone', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(401)));
    // Three automatic trips through the sign-in page in this tab already.
    const earlier = createRedirectGuard({ storage: window.sessionStorage });
    for (let i = 0; i < 3; i += 1) earlier.attempt();

    const appData = createTestAppData();
    appData.events.retain();
    MockEventSource.latest().open();
    MockEventSource.latest().fail(CONNECTING); // what a dropped stream looks like to the browser
    await vi.waitFor(() => {
      expect(appData.events.getStatus()).toBe('signed-out');
    });
    expect(appData.navigate).not.toHaveBeenCalled();
    expect(appData.auth.getState()).toEqual({ status: 'sign-in-blocked', reason: 'loop' });
    // And it stops: no reconnects, no more probes, while the owner reads the "Sign in" screen.
    expect(MockEventSource.latest().closed).toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it('shows "not authorised" when the stream drops for a refused account', async () => {
    server.use(http.get(apiUrl('/api/me'), () => problem(403, 'not_owner', 'req-9')));
    const appData = createTestAppData();
    appData.events.retain();
    MockEventSource.latest().fail(CLOSED);
    await vi.waitFor(() => {
      expect(appData.events.getStatus()).toBe('forbidden');
    });
    expect(appData.auth.getState()).toEqual({ status: 'forbidden', code: 'not_owner', requestId: 'req-9' });
    expect(appData.navigate).not.toHaveBeenCalled();
  });

  it('reconnects when the session is still there', async () => {
    const appData = createTestAppData({ streamBackoff: { baseMs: 5, maxMs: 10, random: () => 0.5 } });
    appData.events.retain();
    MockEventSource.latest().fail(CLOSED);
    await vi.waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(2);
    });
    expect(appData.navigate).not.toHaveBeenCalled();
    appData.events.stop();
  });

  it('routes stream events into the job store and invalidates the job queries', async () => {
    const appData = createTestAppData();
    const invalidate = vi.spyOn(appData.queryClient, 'invalidateQueries');
    appData.events.retain();
    const source = MockEventSource.latest();
    source.open();
    source.emit('snapshot', snapshotPayload(), 'job-1:3');
    source.emit('job', jobEventPayload(4, 'step.succeeded', { step: 'search' }), 'job-1:4');
    source.emit('delta', deltaPayload('Hello', { step: 'brief' }));
    source.emit('job', jobEventPayload(5, 'status', { status: 'succeeded', data: { status: 'succeeded' } }), 'job-1:5');
    expect(appData.jobs.get('job-1')).toMatchObject({
      status: 'succeeded',
      seq: 5,
      text: { brief: { text: 'Hello' } },
      steps: [{ name: 'plan' }, { name: 'search', status: 'succeeded' }, { name: 'brief' }],
    });
    await vi.waitFor(() => {
      expect(invalidate).toHaveBeenCalledExactlyOnceWith({ queryKey: queryKeys.jobs.all });
    });
    appData.events.stop();
  });

  it('refreshes the changed document’s queries and then tells the feed', async () => {
    const appData = createTestAppData();
    const invalidate = vi.spyOn(appData.queryClient, 'invalidateQueries');
    const heard: string[] = [];
    appData.feed.subscribe((event) => {
      heard.push(`${event.type}:${String(appData.jobs.get('job-1')?.seq ?? 0)}`);
    });
    appData.events.retain();
    const source = MockEventSource.latest();
    source.open();
    source.emit('job', jobEventPayload(1, 'entity.changed', { step: 'save', data: { entity: 'idea', id: 'idea-1' } }), 'job-1:1');
    expect(heard).toEqual(['job:1']);
    await vi.waitFor(() => {
      expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
        ['today'],
        ['ideas', 'list'],
        ['jobs', 'detail', 'job-1'],
        ['ideas', 'detail', 'idea-1'],
      ]);
    });
    appData.events.stop();
  });
});
