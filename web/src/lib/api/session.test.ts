// @vitest-environment node
import { describe, expect, it } from 'vitest';

import { isLoggedOutResponse, loginUrl, logoutUrl, pathOf, safeReturnPath, SHARE_TARGET_PARAMS } from './session';

/** Browsers report a manual redirect as an opaque response; Node cannot build one, so fake its two fields. */
const opaqueRedirect = { status: 0, type: 'opaqueredirect' } as const;

describe('isLoggedOutResponse', () => {
  it.each([
    ['a 401', { status: 401, type: 'basic' as const }],
    ['an opaque redirect (browsers, redirect: manual)', opaqueRedirect],
    ['a 302 (Node, redirect: manual)', { status: 302, type: 'basic' as const }],
    ['a 307', { status: 307, type: 'basic' as const }],
  ])('is true for %s', (_name, response) => {
    expect(isLoggedOutResponse(response)).toBe(true);
  });

  it.each([200, 204, 304, 403, 404, 500, 503])('is false for %i', (status) => {
    expect(isLoggedOutResponse({ status, type: 'basic' })).toBe(false);
  });
});

describe('sign-in and sign-out URLs', () => {
  it('sends the browser to Easy Auth with the current path to come back to', () => {
    expect(loginUrl('/studio/piece-1/draft?tab=2#outline')).toBe(
      '/.auth/login/aad?post_login_redirect_uri=%2Fstudio%2Fpiece-1%2Fdraft%3Ftab%3D2%23outline',
    );
    expect(logoutUrl()).toBe('/.auth/logout?post_logout_redirect_uri=%2F');
  });

  it.each([
    ['https://evil.example/', '/'],
    ['//evil.example/today', '/'],
    ['/\\evil.example', '/'],
    ['today', '/'],
    ['/.auth/login/aad/callback?code=x', '/'],
    ['/.AUTH/me', '/'],
    ['/api/me', '/'],
    ['/api', '/'],
    ['/apiary', '/apiary'],
    ['/today?from=/api/me', '/today?from=/api/me'],
  ])('only returns to same-origin app paths: %s becomes %s', (path, expected) => {
    expect(safeReturnPath(path)).toBe(expected);
    expect(loginUrl(path)).toBe(`/.auth/login/aad?post_login_redirect_uri=${encodeURIComponent(expected)}`);
  });

  it('drops the share target’s text from the way back, keeping the rest of the address', () => {
    // Contract section 10: other members' shared text never travels through the sign-in page.
    expect(loginUrl('/today?share=Someone%E2%80%99s%20words&share_url=https%3A%2F%2Fexample.org%2Fp%2F1')).toBe(
      '/.auth/login/aad?post_login_redirect_uri=%2Ftoday',
    );
    expect(safeReturnPath('/today?engage=post-1&share=words#top')).toBe('/today?engage=post-1#top');
    expect(safeReturnPath('/today?share_url=x')).toBe('/today');
    expect(safeReturnPath('/calendar/ideas?take=slot-1')).toBe('/calendar/ideas?take=slot-1');
    expect(SHARE_TARGET_PARAMS).toEqual({ text: 'share', url: 'share_url' });
  });

  it('reads the path, query and fragment from a location', () => {
    expect(pathOf({ pathname: '/calendar/ideas', search: '?q=1', hash: '#top' })).toBe('/calendar/ideas?q=1#top');
  });
});
