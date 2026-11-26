import { LogIn, LogOut, RotateCw } from 'lucide-react';
import type { ReactNode } from 'react';

import { Page, PageHeader } from '@/components/shell/page';
import { Button } from '@/components/ui/button';
import { logoutUrl } from '@/lib/api/session';
import { cn } from '@/lib/cn';

export type ScreenLayout = 'inline' | 'fullscreen';

function Screen({ layout, children }: { layout: ScreenLayout; children: ReactNode }) {
  return <div className={cn('bg-sheet', layout === 'fullscreen' && 'min-h-dvh')}>{children}</div>;
}

function reload() {
  window.location.reload();
}

const NOT_AUTHORISED =
  'You’re signed in with an account that can’t use Blog Writer. Sign out, then sign in with the owner’s account.';
const SIGN_IN_LOOP =
  'Signing in kept sending you back here, so it stopped trying by itself. ' +
  'Sign in once more; if this page comes back, the sign-in setup needs a look.';
const SIGN_IN_NO_STORAGE =
  'Your session has ended. This browser blocks the storage that keeps automatic sign-in safe, so sign in yourself.';

/** Shown for a 403: signed in, but not as the owner. It offers a way out and never redirects by itself. */
export function NotAuthorised({ requestId, layout = 'inline' }: { requestId?: string; layout?: ScreenLayout }) {
  return (
    <Screen layout={layout}>
      <Page>
        <PageHeader title="Not authorised" standfirst={NOT_AUTHORISED} />
        <div className="flex flex-wrap gap-3">
          <Button asChild>
            <a href={logoutUrl('/')}>
              <LogOut aria-hidden="true" />
              Sign out
            </a>
          </Button>
          <Button variant="secondary" onClick={reload}>
            <RotateCw aria-hidden="true" />
            Try again
          </Button>
        </div>
        {requestId ? (
          <p className="mt-8 text-sm text-ink-subtle">
            Request ID <code className="font-mono">{requestId}</code>
          </p>
        ) : null}
      </Page>
    </Screen>
  );
}

/** Shown when automatic sign-in was stopped, so a broken sign-in can never loop. */
export function SignInBlocked({
  reason,
  onSignIn,
  layout = 'inline',
}: {
  reason: 'loop' | 'storage';
  onSignIn: () => void;
  layout?: ScreenLayout;
}) {
  return (
    <Screen layout={layout}>
      <Page>
        <PageHeader title="Sign-in didn’t work" standfirst={reason === 'loop' ? SIGN_IN_LOOP : SIGN_IN_NO_STORAGE} />
        <Button onClick={onSignIn}>
          <LogIn aria-hidden="true" />
          Sign in
        </Button>
      </Page>
    </Screen>
  );
}

/** Shown for the moment between a 401 and the sign-in page loading. */
export function SigningIn({ layout = 'inline' }: { layout?: ScreenLayout }) {
  return (
    <Screen layout={layout}>
      <Page>
        <p role="status" className="text-lg text-ink-muted">
          Your session has ended. Taking you to sign in…
        </p>
      </Page>
    </Screen>
  );
}
