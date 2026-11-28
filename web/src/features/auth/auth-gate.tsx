import { useSyncExternalStore, type ReactNode } from 'react';

import { useAppData } from '@/lib/app-data';
import type { AuthState } from '@/lib/auth/auth-controller';

import { NotAuthorised, SignInBlocked, SigningIn, type ScreenLayout } from './auth-screens';

export function useAuthState(): AuthState {
  const { auth } = useAppData();
  return useSyncExternalStore(auth.subscribe, auth.getState, auth.getState);
}

/**
 * Renders the app while the session is fine, and the matching screen when it is not: signing in,
 * sign-in stopped (loop guard) or not authorised (403).
 */
export function AuthGate({ children, layout = 'inline' }: { children: ReactNode; layout?: ScreenLayout }) {
  const { auth } = useAppData();
  const state = useAuthState();
  switch (state.status) {
    case 'ok':
      return children;
    case 'signing-in':
      return <SigningIn layout={layout} />;
    case 'sign-in-blocked':
      return <SignInBlocked reason={state.reason} onSignIn={auth.signIn} layout={layout} />;
    case 'forbidden':
      return <NotAuthorised requestId={state.requestId} layout={layout} />;
  }
}
