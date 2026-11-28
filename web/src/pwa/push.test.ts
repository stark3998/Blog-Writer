// @vitest-environment node
import { createHash } from 'node:crypto';

import { describe, expect, it, vi } from 'vitest';

import {
  PushSetupError,
  applicationServerKey,
  base64UrlToBytes,
  isAppleMobile,
  pushSupport,
  subscribeDevice,
  subscriptionBody,
  subscriptionIdFor,
  type PushEnvironment,
} from './push';

const FULL: PushEnvironment = {
  hasServiceWorker: true,
  hasPushManager: true,
  hasNotification: true,
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) Chrome/140',
  maxTouchPoints: 0,
  standalone: false,
};

function base64Url(bytes: number[]): string {
  return Buffer.from(bytes).toString('base64url');
}

const KEY_BYTES = [4, ...Array.from({ length: 64 }, (_, index) => index)];
const KEY = base64Url(KEY_BYTES);

describe('what the browser supports', () => {
  it('needs service workers, the Push API and notifications', () => {
    expect(pushSupport(FULL)).toBe('supported');
    expect(pushSupport({ ...FULL, hasServiceWorker: false, hasPushManager: false })).toBe('no-service-worker');
    expect(pushSupport({ ...FULL, hasPushManager: false })).toBe('no-push');
  });

  it('asks iPhone and iPad owners to add the app to the Home Screen first', () => {
    const iphone = { ...FULL, hasPushManager: false, hasNotification: false, userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)' };
    expect(pushSupport(iphone)).toBe('install-first');
    expect(pushSupport({ ...iphone, standalone: true })).toBe('no-push');
    expect(isAppleMobile({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0)', maxTouchPoints: 5 })).toBe(true);
    expect(isAppleMobile({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0)', maxTouchPoints: 0 })).toBe(false);
  });
});

describe('the server key', () => {
  it('decodes base64url, padded or not', () => {
    expect(Array.from(base64UrlToBytes('AQID') ?? [])).toEqual([1, 2, 3]);
    expect(Array.from(base64UrlToBytes('AQI') ?? [])).toEqual([1, 2]);
    expect(Array.from(base64UrlToBytes('_-8=') ?? [])).toEqual([255, 239]);
    expect(base64UrlToBytes('not base64!')).toBeUndefined();
  });

  it('accepts only an uncompressed P-256 point', () => {
    expect(Array.from(applicationServerKey(KEY))).toEqual(KEY_BYTES);
    expect(() => applicationServerKey(base64Url([4, 1, 2]))).toThrow(PushSetupError);
    expect(() => applicationServerKey(base64Url([2, ...KEY_BYTES.slice(1)]))).toThrow('The server’s push key isn’t valid.');
  });
});

describe('the subscription', () => {
  it('derives the server’s id from the endpoint, as the server does', async () => {
    const endpoint = 'https://fcm.googleapis.com/fcm/send/device-1';
    const expected = createHash('sha256').update(endpoint).digest('hex').slice(0, 32);
    await expect(subscriptionIdFor(endpoint)).resolves.toBe(expected);
  });

  it('becomes the request body, refusing an incomplete one', () => {
    const json = { endpoint: 'https://fcm.googleapis.com/x', expirationTime: null, keys: { p256dh: 'P', auth: 'A' } };
    expect(subscriptionBody({ toJSON: () => json })).toEqual({
      endpoint: 'https://fcm.googleapis.com/x',
      expirationTime: null,
      keys: { p256Dh: 'P', auth: 'A' },
    });
    expect(() => subscriptionBody({ toJSON: () => ({ endpoint: 'https://fcm.googleapis.com/x' }) })).toThrow(
      'The browser returned an incomplete subscription.',
    );
  });

  it('reuses a subscription made with the same key and replaces one made with another', async () => {
    const same = {
      options: { applicationServerKey: new Uint8Array(KEY_BYTES).buffer },
      unsubscribe: vi.fn(() => Promise.resolve(true)),
    } as unknown as PushSubscription;
    const fresh = { options: {} } as unknown as PushSubscription;
    const subscribe = vi.fn(() => Promise.resolve(fresh));
    await expect(
      subscribeDevice({ pushManager: { getSubscription: () => Promise.resolve(same), subscribe } as unknown as PushManager }, KEY),
    ).resolves.toBe(same);
    expect(subscribe).not.toHaveBeenCalled();

    const unsubscribe = vi.fn(() => Promise.resolve(true));
    const other = {
      options: { applicationServerKey: new Uint8Array([4, 9, 9]).buffer },
      unsubscribe,
    } as unknown as PushSubscription;
    await expect(
      subscribeDevice({ pushManager: { getSubscription: () => Promise.resolve(other), subscribe } as unknown as PushManager }, KEY),
    ).resolves.toBe(fresh);
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(subscribe).toHaveBeenCalledWith({ userVisibleOnly: true, applicationServerKey: applicationServerKey(KEY) });
  });

  it('turns a refused subscription into an owner-facing reason', async () => {
    const pushManager = {
      getSubscription: () => Promise.resolve(null),
      subscribe: () => Promise.reject(new DOMException('denied', 'NotAllowedError')),
    } as unknown as PushManager;
    await expect(subscribeDevice({ pushManager }, KEY)).rejects.toMatchObject({ code: 'not_allowed' });
  });
});
