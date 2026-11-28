import { loginUrl } from '@/lib/api/session';

import type { RedirectGuard } from './redirect-guard';

/**
 * Where the browser stands with sign-in. Anything but `ok` replaces the app with a screen (see
 * `features/auth`); none of them redirects by itself, so none of them can loop.
 */
export type AuthState =
  | { readonly status: 'ok' }
  /** A top-level navigation to the sign-in page is under way. */
  | { readonly status: 'signing-in' }
  /** Automatic sign-in was stopped: it kept failing (`loop`) or could not be counted (`storage`). */
  | { readonly status: 'sign-in-blocked'; readonly reason: 'loop' | 'storage' }
  /** Signed in, but with an account that may not use the app. */
  | { readonly status: 'forbidden'; readonly code?: string; readonly requestId?: string };

export interface ForbiddenDetails {
  readonly code?: string;
  readonly requestId?: string;
}

export interface AuthController {
  readonly getState: () => AuthState;
  readonly subscribe: (listener: () => void) => () => void;
  /**
   * A request found no session. Navigates to the sign-in page, then back to the current path,
   * unless the redirect guard says that would loop. Later calls do nothing.
   */
  readonly handleUnauthenticated: () => void;
  /** A request was refused for this account. Shows the "not authorised" screen; never redirects. */
  readonly handleForbidden: (details?: ForbiddenDetails) => void;
  /** The owner pressed "Sign in": navigates even if automatic sign-in was stopped. */
  readonly signIn: () => void;
}

export interface AuthControllerOptions {
  readonly guard: RedirectGuard;
  /** A top-level navigation: `window.location.assign` in the browser. */
  readonly navigate: (url: string) => void;
  /** The in-app path to come back to after signing in. */
  readonly currentPath: () => string;
}

const OK: AuthState = { status: 'ok' };

export function createAuthController({ guard, navigate, currentPath }: AuthControllerOptions): AuthController {
  let state: AuthState = OK;
  const listeners = new Set<() => void>();

  const set = (next: AuthState) => {
    state = next;
    for (const listener of listeners) listener();
  };

  const goToSignIn = () => {
    set({ status: 'signing-in' });
    navigate(loginUrl(currentPath()));
  };

  return {
    getState: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    handleUnauthenticated: () => {
      // One decision per page: a redirect is already under way, was refused, or the account is wrong.
      if (state.status !== 'ok') return;
      const decision = guard.attempt();
      if (decision === 'allowed') {
        goToSignIn();
        return;
      }
      set({ status: 'sign-in-blocked', reason: decision === 'loop' ? 'loop' : 'storage' });
    },
    handleForbidden: ({ code, requestId } = {}) => {
      if (state.status === 'forbidden' || state.status === 'signing-in') return;
      set({ status: 'forbidden', code, requestId });
    },
    signIn: () => {
      guard.recordManual();
      goToSignIn();
    },
  };
}
