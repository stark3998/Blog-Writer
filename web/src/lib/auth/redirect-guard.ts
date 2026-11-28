/**
 * Counts automatic sign-in redirects in sessionStorage, so a misconfigured login can never turn
 * into an endless redirect loop (app -> 401 -> sign-in -> app -> 401 -> ...).
 *
 * sessionStorage survives the round trip through the sign-in page (same tab, same origin) and is
 * forgotten when the tab closes. The count only expires with time: a success in between does not
 * reset it, because a loop can include successful requests (one endpoint failing while
 * `/api/me` works, for example).
 */

export type RedirectDecision =
  /** Recorded; go ahead and redirect. */
  | 'allowed'
  /** Too many redirects recently: stop and ask the owner. */
  | 'loop'
  /** Storage is unavailable, so a loop could not be detected: do not redirect automatically. */
  | 'unavailable';

export interface RedirectGuard {
  /** Records an automatic redirect if one is allowed now. */
  readonly attempt: () => RedirectDecision;
  /**
   * Records a redirect the owner asked for (a "Sign in" button). Never refused, and it counts:
   * if signing in still fails afterwards, the next automatic redirect is refused at once.
   */
  readonly recordManual: () => void;
}

export interface RedirectGuardOptions {
  /** Usually `window.sessionStorage`; `undefined` when the browser refuses access to it. */
  readonly storage: Storage | undefined;
  readonly key?: string;
  /** Automatic redirects allowed inside the window. */
  readonly limit?: number;
  readonly windowMs?: number;
  readonly now?: () => number;
}

export const REDIRECT_GUARD_KEY = 'bw:auth-redirects';
export const REDIRECT_LIMIT = 3;
export const REDIRECT_WINDOW_MS = 5 * 60_000;

/** Keeps the list short even if the clock jumps around. */
const MAX_ENTRIES = 20;

export function createRedirectGuard({
  storage,
  key = REDIRECT_GUARD_KEY,
  limit = REDIRECT_LIMIT,
  windowMs = REDIRECT_WINDOW_MS,
  now = Date.now,
}: RedirectGuardOptions): RedirectGuard {
  /** Timestamps inside the window, oldest first; `undefined` when storage cannot be read. */
  const read = (at: number): number[] | undefined => {
    if (!storage) return undefined;
    let raw: string | null;
    try {
      raw = storage.getItem(key);
    } catch {
      return undefined;
    }
    let parsed: unknown;
    try {
      parsed = raw === null ? [] : JSON.parse(raw);
    } catch {
      parsed = [];
    }
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (value): value is number =>
        typeof value === 'number' && Number.isFinite(value) && value > at - windowMs && value <= at,
    );
  };

  /** Returns false when storage refused the write. */
  const write = (entries: readonly number[]): boolean => {
    if (!storage) return false;
    try {
      storage.setItem(key, JSON.stringify(entries.slice(-MAX_ENTRIES)));
      return true;
    } catch {
      return false;
    }
  };

  return {
    attempt: () => {
      const at = now();
      const entries = read(at);
      if (entries === undefined) return 'unavailable';
      if (entries.length >= limit) return 'loop';
      return write([...entries, at]) ? 'allowed' : 'unavailable';
    },
    recordManual: () => {
      const at = now();
      write([...(read(at) ?? []), at]);
    },
  };
}

/** `window.sessionStorage`, or `undefined` where the browser denies access (some privacy modes). */
export function browserSessionStorage(): Storage | undefined {
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
}
