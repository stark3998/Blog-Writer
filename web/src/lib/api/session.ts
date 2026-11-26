/**
 * Session facts shared by the API client, the auth controller and the event stream.
 *
 * The app sits behind Azure Container Apps Easy Auth. Requests use `redirect: 'manual'`, so an
 * expired session never turns into a cross-origin fetch of the login page: it shows up as a 401
 * (from the app or Easy Auth), as a 3xx, or, in browsers, as an opaque redirect (`status` 0,
 * `type` "opaqueredirect"). All three mean "sign in again".
 */

/** Easy Auth's sign-in page for Microsoft Entra ID. */
export const LOGIN_PATH = '/.auth/login/aad';
/** Easy Auth's sign-out page. */
export const LOGOUT_PATH = '/.auth/logout';

const REDIRECT_STATUSES: ReadonlySet<number> = new Set([301, 302, 303, 307, 308]);

/** True when a response means there is no session, so the browser must sign in again. */
export function isLoggedOutResponse(response: Pick<Response, 'status' | 'type'>): boolean {
  return response.type === 'opaqueredirect' || response.status === 401 || REDIRECT_STATUSES.has(response.status);
}

/**
 * A path the login page may send the browser back to: same-origin, and not an auth or API
 * endpoint (coming back to those would only fail or loop). Anything else becomes `/`.
 */
export function safeReturnPath(path: string): string {
  // `//host` and `/\host` are protocol-relative URLs in browsers, not paths.
  if (!path.startsWith('/') || path.startsWith('//') || path.startsWith('/\\')) return '/';
  const pathname = path.split(/[?#]/, 1)[0] ?? '/';
  if (/^\/(?:\.auth|api)(?:\/|$)/i.test(pathname)) return '/';
  return path;
}

/** The sign-in URL that brings the browser back to `returnTo` afterwards. */
export function loginUrl(returnTo: string): string {
  return `${LOGIN_PATH}?post_login_redirect_uri=${encodeURIComponent(safeReturnPath(returnTo))}`;
}

/** The sign-out URL; Easy Auth then sends the browser to `returnTo` (which signs in again). */
export function logoutUrl(returnTo = '/'): string {
  return `${LOGOUT_PATH}?post_logout_redirect_uri=${encodeURIComponent(safeReturnPath(returnTo))}`;
}

/** The current in-app location as a path with query and fragment. */
export function pathOf(location: Pick<Location, 'pathname' | 'search' | 'hash'>): string {
  return `${location.pathname}${location.search}${location.hash}`;
}
