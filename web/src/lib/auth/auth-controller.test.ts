import { describe, expect, it, vi } from 'vitest';

import { createAuthController } from './auth-controller';
import { createRedirectGuard } from './redirect-guard';

/** `storage: null` simulates a browser that denies sessionStorage. */
function setup({ storage, path = '/calendar/ideas?q=1' }: { storage?: Storage | null; path?: string } = {}) {
  const navigate = vi.fn<(url: string) => void>();
  const auth = createAuthController({
    guard: createRedirectGuard({ storage: storage === undefined ? window.sessionStorage : (storage ?? undefined) }),
    navigate,
    currentPath: () => path,
  });
  const listener = vi.fn();
  auth.subscribe(listener);
  return { auth, navigate, listener };
}

describe('createAuthController', () => {
  it('starts signed in', () => {
    expect(setup().auth.getState()).toEqual({ status: 'ok' });
  });

  it('sends a signed-out browser to sign in, then back to where it was', () => {
    const { auth, navigate, listener } = setup();
    auth.handleUnauthenticated();
    expect(navigate).toHaveBeenCalledExactlyOnceWith('/.auth/login/aad?post_login_redirect_uri=%2Fcalendar%2Fideas%3Fq%3D1');
    expect(auth.getState()).toEqual({ status: 'signing-in' });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('redirects once per page, however many requests fail', () => {
    const { auth, navigate } = setup();
    auth.handleUnauthenticated();
    auth.handleUnauthenticated();
    auth.handleUnauthenticated();
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it('stops redirecting when sign-in keeps coming back (loop guard)', () => {
    // Three page loads in a row that each found no session.
    for (let i = 0; i < 3; i += 1) setup().auth.handleUnauthenticated();
    const { auth, navigate } = setup();
    auth.handleUnauthenticated();
    expect(navigate).not.toHaveBeenCalled();
    expect(auth.getState()).toEqual({ status: 'sign-in-blocked', reason: 'loop' });
  });

  it('does not redirect automatically when the loop guard cannot count', () => {
    const { auth, navigate } = setup({ storage: null });
    auth.handleUnauthenticated();
    expect(navigate).not.toHaveBeenCalled();
    expect(auth.getState()).toEqual({ status: 'sign-in-blocked', reason: 'storage' });
  });

  it('lets the owner sign in by hand from the blocked screen', () => {
    const { auth, navigate } = setup({ storage: null });
    auth.handleUnauthenticated();
    auth.signIn();
    expect(navigate).toHaveBeenCalledExactlyOnceWith('/.auth/login/aad?post_login_redirect_uri=%2Fcalendar%2Fideas%3Fq%3D1');
    expect(auth.getState()).toEqual({ status: 'signing-in' });
  });

  it('shows "not authorised" for a 403 and never redirects afterwards', () => {
    const { auth, navigate } = setup();
    auth.handleForbidden({ code: 'not_owner', requestId: 'req-1' });
    expect(auth.getState()).toEqual({ status: 'forbidden', code: 'not_owner', requestId: 'req-1' });
    auth.handleUnauthenticated();
    auth.handleForbidden({ code: 'other' });
    expect(navigate).not.toHaveBeenCalled();
    expect(auth.getState()).toEqual({ status: 'forbidden', code: 'not_owner', requestId: 'req-1' });
  });

  it('ignores a late 403 once a sign-in redirect is under way', () => {
    const { auth } = setup();
    auth.handleUnauthenticated();
    auth.handleForbidden();
    expect(auth.getState()).toEqual({ status: 'signing-in' });
  });

  it('stops notifying a listener after it unsubscribes', () => {
    const { auth } = setup();
    const listener = vi.fn();
    const unsubscribe = auth.subscribe(listener);
    unsubscribe();
    auth.handleForbidden();
    expect(listener).not.toHaveBeenCalled();
  });
});
