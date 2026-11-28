// @vitest-environment node
import { runInNewContext } from 'node:vm';

import { describe, expect, it, vi } from 'vitest';

import { readProjectFile } from '@/test/files';

/**
 * `public/sw.js`'s push and notificationclick handlers, run in a sandbox that stands in for the
 * service worker global: what a push shows, and where a tap goes.
 */

const ORIGIN = 'https://writer.example.com';
const source = readProjectFile('public/sw.js');

interface Shown {
  title: string;
  options: { tag?: string; body?: string; data: { url: string }; icon?: string };
}

interface FakeClient {
  url: string;
  focus: () => Promise<FakeClient>;
  navigate?: (url: string) => Promise<FakeClient>;
}

function loadWorker(windows: FakeClient[] = []) {
  const listeners = new Map<string, (event: unknown) => void>();
  const shown: Shown[] = [];
  const openWindow = vi.fn((url: string) => Promise.resolve({ url }));
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, listener: (event: unknown) => void) => {
      listeners.set(type, listener);
    },
    skipWaiting: vi.fn(),
    registration: {
      showNotification: (title: string, options: Shown['options']) => {
        shown.push({ title, options });
        return Promise.resolve();
      },
    },
    clients: {
      claim: vi.fn(() => Promise.resolve()),
      matchAll: vi.fn(() => Promise.resolve(windows)),
      openWindow,
    },
  };
  runInNewContext(source, { self, URL });

  async function dispatch(type: string, event: Record<string, unknown>): Promise<void> {
    const pending: Promise<unknown>[] = [];
    const listener = listeners.get(type);
    if (!listener) throw new Error(`no ${type} listener`);
    listener({ ...event, waitUntil: (promise: Promise<unknown>) => pending.push(promise) });
    await Promise.all(pending);
  }

  return {
    shown,
    openWindow,
    push: (data: unknown) =>
      dispatch('push', {
        data:
          data === undefined
            ? null
            : {
                json: () => {
                  if (typeof data === 'string') return JSON.parse(data) as unknown;
                  return data;
                },
              },
      }),
    click: (url: unknown) => {
      const close = vi.fn();
      return dispatch('notificationclick', { notification: { data: { url }, close } }).then(() => close);
    },
  };
}

describe('the service worker', () => {
  it('still installs and claims without caching or intercepting requests', () => {
    expect(source).toContain("addEventListener('install'");
    expect(source).toContain("addEventListener('activate'");
    expect(source).not.toMatch(/addEventListener\('fetch'/);
    expect(source).not.toMatch(/caches\./);
  });

  it('shows a push as its title only, with its tag and link', async () => {
    const worker = loadWorker();
    await worker.push({ title: 'Post at 10:00: tap to publish', url: '/studio/post-1/go-live?post=post-1', tag: 'stage:post-1' });
    expect(worker.shown).toHaveLength(1);
    const [notification] = worker.shown;
    expect(notification?.title).toBe('Post at 10:00: tap to publish');
    expect(notification?.options.tag).toBe('stage:post-1');
    expect(notification?.options.data).toEqual({ url: '/studio/post-1/go-live?post=post-1' });
    expect(notification?.options).not.toHaveProperty('body');
  });

  it('never links outside the app', async () => {
    const worker = loadWorker();
    for (const url of ['//evil.example.com/x', '/\\evil.example.com', 'https://evil.example.com/', 'javascript:alert(1)', 42]) {
      await worker.push({ title: 'Check this', url, tag: 'x' });
    }
    expect(worker.shown.map((notification) => notification.options.data.url)).toEqual([
      '/today',
      '/today',
      '/today',
      '/today',
      '/today',
    ]);
  });

  it('falls back to a plain notification for an unsafe or broken payload', async () => {
    const worker = loadWorker();
    await worker.push({ title: 'Bidi \u202ereversed', url: '/today', tag: 'bad tag!' });
    await worker.push({ title: 'x'.repeat(81), url: '/today' });
    await worker.push(undefined);
    await worker.push('not json {');
    expect(worker.shown.map((notification) => notification.title)).toEqual([
      'Blog Writer',
      'Blog Writer',
      'Blog Writer',
      'Blog Writer',
    ]);
    expect(worker.shown[0]?.options.tag).toBeUndefined();
  });

  it('takes an open window of the app to the link', async () => {
    const navigate = vi.fn((url: string) => Promise.resolve({ url } as FakeClient));
    const client: FakeClient = { url: `${ORIGIN}/today`, focus: () => Promise.resolve(client), navigate };
    const worker = loadWorker([{ url: 'https://other.example.com/', focus: vi.fn() }, client]);
    const close = await worker.click('/calendar/plan?week=2026-11-30');
    expect(close).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(`${ORIGIN}/calendar/plan?week=2026-11-30`);
    expect(worker.openWindow).not.toHaveBeenCalled();
  });

  it('opens a new window when the app is not open, and only on the app', async () => {
    const worker = loadWorker();
    await worker.click('https://evil.example.com/');
    expect(worker.openWindow).toHaveBeenCalledWith(`${ORIGIN}/today`);
  });
});
