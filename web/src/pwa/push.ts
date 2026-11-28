import type { components } from '@/lib/api/schema';

/**
 * Web Push on this device (Settings > Integrations, contract section 10 and I-2): what the
 * browser supports, the permission flow, subscribing with the server's VAPID key, and the body
 * `POST /api/push/subscriptions` takes (the browser's `PushSubscription.toJSON()`).
 *
 * The subscription endpoint is a capability (anyone holding it can push to this device). It goes
 * to the server once, in the subscribe request, and is never logged, stored or put in a URL here.
 * The server knows a subscription by an id derived from its endpoint (the first 32 hex digits of
 * its SHA-256, `notify.subscriptions.subscription_id_for`), so this device finds its own id again
 * by hashing its endpoint: nothing about push is kept in local storage.
 *
 * Notifications carry a title and a same-origin link only (`public/sw.js` shows them).
 */

export type PushSubscriptionBody = components['schemas']['PushSubscriptionIn'];

/** Whether this browser can receive push, and if not, why. */
export type PushSupport =
  /** Service workers, the Push API and notifications are all there. */
  | 'supported'
  /** iPhone or iPad in a Safari tab: push works only once the app is on the Home Screen. */
  | 'install-first'
  /** No service workers (an old browser, a private window, or a page that is not https). */
  | 'no-service-worker'
  /** Service workers, but no Push API or notifications. */
  | 'no-push';

/** What `pushSupport` reads from the browser; tests pass their own. */
export interface PushEnvironment {
  readonly hasServiceWorker: boolean;
  readonly hasPushManager: boolean;
  readonly hasNotification: boolean;
  readonly userAgent: string;
  readonly maxTouchPoints: number;
  /** Running as an installed app (`display-mode: standalone`, or iOS `navigator.standalone`). */
  readonly standalone: boolean;
}

export function browserPushEnvironment(): PushEnvironment {
  const nav = navigator as Navigator & { readonly standalone?: boolean };
  return {
    hasServiceWorker: 'serviceWorker' in navigator,
    hasPushManager: 'PushManager' in window,
    hasNotification: 'Notification' in window,
    userAgent: navigator.userAgent,
    maxTouchPoints: navigator.maxTouchPoints,
    standalone: window.matchMedia('(display-mode: standalone)').matches || nav.standalone === true,
  };
}

/** iPhone, iPod or iPad (iPadOS reports a Mac user agent, but has touch points). */
export function isAppleMobile(env: Pick<PushEnvironment, 'userAgent' | 'maxTouchPoints'>): boolean {
  return /\biP(?:hone|ad|od)\b/.test(env.userAgent) || (/\bMacintosh\b/.test(env.userAgent) && env.maxTouchPoints > 1);
}

export function pushSupport(env: PushEnvironment = browserPushEnvironment()): PushSupport {
  const complete = env.hasServiceWorker && env.hasPushManager && env.hasNotification;
  if (complete) return 'supported';
  // Safari on iOS and iPadOS 16.4+ offers push to Home Screen apps only.
  if (isAppleMobile(env) && !env.standalone) return 'install-first';
  if (!env.hasServiceWorker) return 'no-service-worker';
  return 'no-push';
}

/** The browser's notification permission (`default` until the owner answers the prompt). */
export function notificationPermission(): NotificationPermission {
  return 'Notification' in window ? Notification.permission : 'denied';
}

/**
 * Asks for permission to show notifications. Call it from a tap: browsers ignore (or block)
 * a prompt that no user gesture started.
 */
export async function requestNotificationPermission(): Promise<NotificationPermission> {
  if (!('Notification' in window)) return 'denied';
  if (Notification.permission !== 'default') return Notification.permission;
  return Notification.requestPermission();
}

/** Bytes of a base64url string (no padding needed), or `undefined` when it is not base64url. */
export function base64UrlToBytes(value: string): Uint8Array<ArrayBuffer> | undefined {
  if (!/^[A-Za-z0-9_-]*={0,2}$/.test(value)) return undefined;
  const text = value.replace(/=+$/, '').replace(/-/g, '+').replace(/_/g, '/');
  const padded = text + '='.repeat((4 - (text.length % 4)) % 4);
  let binary: string;
  try {
    binary = atob(padded);
  } catch {
    return undefined;
  }
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

/**
 * The VAPID public key (`GET /api/push/key`) as `PushManager.subscribe` takes it: the 65-byte
 * uncompressed P-256 point. Throws for anything else, so a broken key never reaches the browser.
 */
export function applicationServerKey(base64url: string): Uint8Array<ArrayBuffer> {
  const bytes = base64UrlToBytes(base64url);
  if (bytes?.length !== 65 || bytes[0] !== 0x04) {
    throw new PushSetupError('invalid_key', 'The server’s push key isn’t valid.');
  }
  return bytes;
}

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** The server's id for a subscription: the first 32 hex digits of SHA-256(endpoint). */
export async function subscriptionIdFor(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint));
  return hex(digest).slice(0, 32);
}

/** `POST /api/push/subscriptions`'s body from the browser's subscription (`toJSON()`). */
export function subscriptionBody(subscription: Pick<PushSubscription, 'toJSON'>): PushSubscriptionBody {
  const json = subscription.toJSON();
  const endpoint = json.endpoint;
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!endpoint || !p256dh || !auth) {
    throw new PushSetupError('incomplete_subscription', 'The browser returned an incomplete subscription.');
  }
  // The server reads `p256dh` by name or by its camelCase alias; the generated type names the alias.
  return { endpoint, expirationTime: json.expirationTime ?? null, keys: { p256Dh: p256dh, auth } };
}

/** A push setup step that failed, with an owner-facing sentence (never the endpoint or a key). */
export class PushSetupError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = 'PushSetupError';
    this.code = code;
  }
}

/**
 * This origin's service worker registration, or `undefined` when there is none yet (the
 * development server registers none; a fresh install registers after the first load). Never
 * `navigator.serviceWorker.ready`: that waits forever when nothing is registered.
 */
export async function findServiceWorker(
  container: ServiceWorkerContainer | undefined = 'serviceWorker' in navigator ? navigator.serviceWorker : undefined,
): Promise<ServiceWorkerRegistration | undefined> {
  if (!container) return undefined;
  try {
    return await container.getRegistration('/');
  } catch {
    return undefined;
  }
}

function sameKey(current: ArrayBuffer | null | undefined, wanted: Uint8Array): boolean {
  if (!current) return false;
  const bytes = new Uint8Array(current);
  return bytes.length === wanted.length && bytes.every((byte, index) => byte === wanted[index]);
}

/**
 * Subscribes this device with the server's key. An existing subscription made with the same key is
 * reused; one made with another key (the server's VAPID key changed) is replaced, or pushes signed
 * with the new key would never arrive.
 */
export async function subscribeDevice(
  registration: Pick<ServiceWorkerRegistration, 'pushManager'>,
  serverKey: string,
): Promise<PushSubscription> {
  const key = applicationServerKey(serverKey);
  const existing = await registration.pushManager.getSubscription();
  if (existing) {
    if (sameKey(existing.options.applicationServerKey, key)) return existing;
    await existing.unsubscribe();
  }
  try {
    return await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  } catch (error) {
    const name = error instanceof DOMException ? error.name : '';
    if (name === 'NotAllowedError') {
      throw new PushSetupError('not_allowed', 'The browser didn’t allow notifications for this app.');
    }
    throw new PushSetupError('subscribe_failed', 'The browser couldn’t turn on push. Try again in a moment.');
  }
}
