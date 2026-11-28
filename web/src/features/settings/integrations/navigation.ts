/**
 * Leaving the app for LinkedIn's sign-in page, and noticing the way back.
 *
 * OAuth is a top-level redirect (contract W-A9): the server's callback always answers 303 to
 * `/settings/integrations` and reflects nothing, so the page reads the outcome from the
 * connection (`status`, `lastError`). A timestamp in sessionStorage marks that this tab left to
 * connect, so the page can say "LinkedIn is connected" (or why not) once, on return. It holds a
 * time only: never the authorize URL, its state or a token.
 */

const MARKER = 'bw:linkedin-connect-started';
/** A return later than this is not the answer to that sign-in any more. */
const MARKER_MAX_AGE_MS = 15 * 60_000;
/**
 * LinkedIn's authorize endpoint, as parts: the bundle holds no LinkedIn URL (`scripts/verify-dist.mjs`
 * fails on any external host); the address itself always comes from the server.
 */
const LINKEDIN_AUTHORIZE = { host: 'www.linkedin.com', pathname: '/oauth/v2/authorization' } as const;

/** Only LinkedIn's own authorize endpoint: the server's answer never sends the tab anywhere else. */
export function isLinkedInAuthorizeUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === 'https:' &&
      url.host === LINKEDIN_AUTHORIZE.host &&
      url.pathname === LINKEDIN_AUTHORIZE.pathname &&
      url.username === '' &&
      url.password === ''
    );
  } catch {
    return false;
  }
}

/** Top-level navigation to LinkedIn (a module function, so tests can replace it). */
export function leaveForLinkedIn(url: string): void {
  try {
    window.sessionStorage.setItem(MARKER, new Date().toISOString());
  } catch {
    // Storage unavailable: the page simply won't greet the return.
  }
  window.location.assign(url);
}

/** When this tab left to connect, if it did so recently (read it once per page; then clear it). */
export function readConnectMarker(now: number): string | undefined {
  let value: string | null;
  try {
    value = window.sessionStorage.getItem(MARKER);
  } catch {
    return undefined;
  }
  if (!value) return undefined;
  const at = Date.parse(value);
  if (Number.isNaN(at) || now - at > MARKER_MAX_AGE_MS || at - now > 60_000) return undefined;
  return value;
}

/** Forgets the mark, so a later visit does not greet the same return again. */
export function clearConnectMarker(): void {
  try {
    window.sessionStorage.removeItem(MARKER);
  } catch {
    // Nothing to clear.
  }
}
