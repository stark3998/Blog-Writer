import { backoffDelay, type BackoffOptions } from '@/lib/query/backoff';

import { parseStreamEvent, STREAM_EVENT_TYPES, type StreamEvent, type StreamEventType } from './events';

/** The app-wide Server-Sent Events endpoint. Same-origin, so the session cookies go with it. */
export const EVENTS_PATH = '/api/events';

/** What the stream uses of `EventSource`: the browser's, or a fake in tests. */
export interface EventSourceLike {
  readonly readyState: number;
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
}

// EventSource.readyState values.
export const CONNECTING = 0;
export const OPEN = 1;
export const CLOSED = 2;

/** What `GET /api/me` (with `redirect: 'manual'`) says about the session after a stream error. */
export type ProbeOutcome = 'authenticated' | 'logged-out' | 'forbidden' | 'unavailable';

export type EventStreamStatus =
  /** Nothing needs live events, so there is no connection. */
  | 'idle'
  | 'connecting'
  | 'open'
  /** The connection dropped: checking the session, or waiting to try again. */
  | 'reconnecting'
  /** The session ended; sign-in takes over. Final for this page. */
  | 'signed-out'
  /** The account may not use the app. Final for this page. */
  | 'forbidden'
  /** This browser has no `EventSource`. Final. */
  | 'unsupported';

const FINAL: ReadonlySet<EventStreamStatus> = new Set(['signed-out', 'forbidden', 'unsupported']);

export interface EventStream {
  /**
   * Opens the stream (once, however many callers) and keeps it open until every caller has
   * released it, plus `lingerMs`, so moving between pages does not reconnect.
   */
  readonly retain: () => () => void;
  readonly getStatus: () => EventStreamStatus;
  readonly subscribeStatus: (listener: () => void) => () => void;
  /** Closes the connection and cancels pending work now, whatever the retain count. */
  readonly stop: () => void;
}

export interface EventStreamOptions {
  readonly url?: string;
  readonly createEventSource: (url: string) => EventSourceLike;
  /** Checks the session after an error. Must not throw (a rejection counts as `unavailable`). */
  readonly probe: () => Promise<ProbeOutcome>;
  readonly onEvent: (event: StreamEvent) => void;
  readonly onLoggedOut: () => void;
  readonly onForbidden: () => void;
  readonly backoff?: Partial<BackoffOptions>;
  /** How long the stream stays open after the last release. */
  readonly lingerMs?: number;
  /** A connection that stayed open this long was healthy: its end starts the backoff afresh. */
  readonly stableMs?: number;
  readonly now?: () => number;
  /** Timers and the `online` event. */
  readonly win?: Pick<Window, 'setTimeout' | 'clearTimeout' | 'addEventListener' | 'removeEventListener'>;
}

export const DEFAULT_STREAM_BACKOFF: BackoffOptions = { baseMs: 1000, maxMs: 30_000 };
export const DEFAULT_LINGER_MS = 15_000;
export const DEFAULT_STABLE_MS = 10_000;

/**
 * The one app-wide event stream.
 *
 * Reconnection: after a healthy connection ends (the server ends every stream before 240 s,
 * with a `close` event), the browser's own reconnect is left to run once, because it sends
 * `Last-Event-ID` and the server resumes from there. Every error also probes `GET /api/me`
 * (`redirect: 'manual'`):
 *
 * - logged out: close for good and hand over to sign-in (`onLoggedOut`);
 * - refused account: close for good (`onForbidden`);
 * - otherwise, when the browser gave up (`CLOSED`, for example after a 5xx) or its own retry
 *   failed too, reconnect with capped exponential backoff (1 s, 2 s, 4 s ... at most 30 s, with
 *   jitter). A new `EventSource` cannot send `Last-Event-ID`; the server then sends full
 *   snapshots, and the job store skips what it already has. Coming back online skips the wait.
 */
export function createEventStream({
  url = EVENTS_PATH,
  createEventSource,
  probe,
  onEvent,
  onLoggedOut,
  onForbidden,
  backoff: backoffOverrides,
  lingerMs = DEFAULT_LINGER_MS,
  stableMs = DEFAULT_STABLE_MS,
  now = Date.now,
  win = window,
}: EventStreamOptions): EventStream {
  const backoff: BackoffOptions = { ...DEFAULT_STREAM_BACKOFF, ...backoffOverrides };

  let status: EventStreamStatus = 'idle';
  let source: EventSourceLike | undefined;
  let retainCount = 0;
  /** Backoff reconnects since the last healthy connection. */
  let attempt = 0;
  /** Errors since the last healthy connection. */
  let failures = 0;
  let openedAt: number | undefined;
  let retryTimer: number | undefined;
  let lingerTimer: number | undefined;
  /** Bumped by `stop()`, so callbacks from an earlier run (a slow probe) do nothing. */
  let run = 0;
  let probingRun: number | undefined;
  let listeningOnline = false;
  const statusListeners = new Set<() => void>();

  const setStatus = (next: EventStreamStatus) => {
    if (status === next) return;
    status = next;
    for (const listener of statusListeners) listener();
  };

  const clearRetry = () => {
    if (retryTimer === undefined) return;
    win.clearTimeout(retryTimer);
    retryTimer = undefined;
  };

  const closeSource = () => {
    const current = source;
    source = undefined;
    current?.close();
  };

  const onOnline = () => {
    // Back online while waiting to reconnect: no point waiting out the backoff.
    if (retryTimer !== undefined) connect();
  };

  const listenOnline = (listen: boolean) => {
    if (listen === listeningOnline) return;
    listeningOnline = listen;
    if (listen) win.addEventListener('online', onOnline);
    else win.removeEventListener('online', onOnline);
  };

  const scheduleReconnect = () => {
    clearRetry();
    const delay = backoffDelay(attempt, backoff);
    attempt += 1;
    setStatus('reconnecting');
    retryTimer = win.setTimeout(() => {
      retryTimer = undefined;
      connect();
    }, delay);
  };

  const finish = (final: 'signed-out' | 'forbidden') => {
    clearRetry();
    closeSource();
    listenOnline(false);
    setStatus(final);
  };

  const handleError = async (failed: EventSourceLike) => {
    if (openedAt !== undefined && now() - openedAt >= stableMs) {
      attempt = 0;
      failures = 0;
    }
    openedAt = undefined;
    failures += 1;
    // The browser retries by itself once (CONNECTING). Take over when it has given up (CLOSED)
    // or when its retry failed as well.
    if (failed.readyState === CLOSED || failures > 1) closeSource();
    setStatus('reconnecting');

    if (probingRun === run) return;
    const thisRun = run;
    probingRun = thisRun;
    let outcome: ProbeOutcome;
    try {
      outcome = await probe();
    } catch {
      outcome = 'unavailable';
    }
    if (thisRun !== run) return;
    probingRun = undefined;

    if (outcome === 'logged-out') {
      finish('signed-out');
      onLoggedOut();
      return;
    }
    if (outcome === 'forbidden') {
      finish('forbidden');
      onForbidden();
      return;
    }
    // Still the browser's turn: its retry is under way or has already reopened the stream.
    if (source !== undefined && source.readyState !== CLOSED) return;
    closeSource();
    if (retryTimer === undefined) scheduleReconnect();
  };

  const handleMessage = (type: StreamEventType, event: Event) => {
    if (!(event instanceof MessageEvent)) return;
    const parsed = typeof event.data === 'string' ? parseStreamEvent(type, event.data) : undefined;
    if (parsed === undefined) {
      console.warn('event_stream_dropped', { type });
      return;
    }
    onEvent(parsed);
  };

  function connect(): void {
    clearRetry();
    closeSource();
    let next: EventSourceLike;
    try {
      next = createEventSource(url);
    } catch {
      listenOnline(false);
      setStatus('unsupported');
      return;
    }
    source = next;
    listenOnline(true);
    setStatus('connecting');
    next.addEventListener('open', () => {
      if (source !== next) return;
      openedAt = now();
      setStatus('open');
    });
    next.addEventListener('error', () => {
      if (source !== next) return;
      void handleError(next);
    });
    for (const type of STREAM_EVENT_TYPES) {
      next.addEventListener(type, (event) => {
        if (source === next) handleMessage(type, event);
      });
    }
  }

  const stop = () => {
    run += 1;
    probingRun = undefined;
    clearRetry();
    if (lingerTimer !== undefined) {
      win.clearTimeout(lingerTimer);
      lingerTimer = undefined;
    }
    closeSource();
    listenOnline(false);
    attempt = 0;
    failures = 0;
    openedAt = undefined;
    if (!FINAL.has(status)) setStatus('idle');
  };

  return {
    retain: () => {
      retainCount += 1;
      if (lingerTimer !== undefined) {
        win.clearTimeout(lingerTimer);
        lingerTimer = undefined;
      }
      if (status === 'idle') connect();
      let released = false;
      return () => {
        if (released) return;
        released = true;
        retainCount -= 1;
        if (retainCount > 0) return;
        lingerTimer = win.setTimeout(() => {
          lingerTimer = undefined;
          if (retainCount === 0) stop();
        }, lingerMs);
      };
    },
    getStatus: () => status,
    subscribeStatus: (listener) => {
      statusListeners.add(listener);
      return () => {
        statusListeners.delete(listener);
      };
    },
    stop,
  };
}
