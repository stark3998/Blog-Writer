import { CircleAlert, CircleCheck, CircleMinus, LogIn, RefreshCw, Unlink } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';

import { BlockLoading } from '../components/settings-block';
import { StateBadge, type StateLook } from '../components/status-badge';
import { useNow } from '@/lib/use-now';
import { formatDate, formatDateTime, formatRelative } from '../format';
import { settingsKeys, type Schemas } from '../queries';
import { clearConnectMarker, isLinkedInAuthorizeUrl, leaveForLinkedIn, readConnectMarker } from './navigation';
import { TOKEN_LIFETIME_DAYS, TokenRuler } from './token-ruler';

type ConnectionView = Schemas['ConnectionView'];

const STATUS_LOOKS: Readonly<Record<ConnectionView['status'], StateLook>> = {
  connected: { label: 'Connected', tone: 'success', icon: CircleCheck },
  needs_reconnect: { label: 'Reconnect needed', tone: 'danger', icon: CircleAlert },
  disconnected: { label: 'Not connected', tone: 'neutral', icon: CircleMinus },
};

/** What a connection's `lastError` code means for the owner (`linkedin.transport` codes). */
export function connectionErrorSentence(code: string): string {
  switch (code) {
    case 'oauth_denied':
      return 'You cancelled on LinkedIn’s page, so nothing changed. Connect again when you’re ready.';
    case 'oauth_state_invalid':
      return 'The sign-in took more than 10 minutes, or was opened twice. Start it again from here.';
    case 'oauth_code_missing':
      return 'LinkedIn didn’t send the sign-in back. Start it again from here.';
    case 'oauth_exchange_failed':
      return 'LinkedIn didn’t accept the sign-in. Try again in a few minutes.';
    case 'oauth_scope_missing':
      return 'LinkedIn didn’t grant permission to post. Connect again and allow posting.';
    case 'oauth_userinfo_failed':
      return 'LinkedIn didn’t share your member id. Try again in a few minutes.';
    case 'linkedin_not_configured':
      return 'The LinkedIn app isn’t set up on this server.';
    case 'vault_unavailable':
      return 'Token encryption isn’t set up on this server, so the connection can’t be stored.';
    case 'token_expired':
      return 'The token reached the end of its 60 days. Reconnect to keep one-tap publishing.';
    case 'token_unreadable':
      return 'The stored token can’t be read any more (the encryption key changed). Reconnect.';
    case 'unauthorized':
      return 'LinkedIn refused the token when a post went out. Reconnect.';
    case 'introspect_failed':
      return 'The daily token check couldn’t reach LinkedIn. It tries again tomorrow.';
    case 'needs_reconnect':
      return 'Reconnect LinkedIn to keep one-tap publishing.';
    default:
      return `The last attempt failed (${code}).`;
  }
}

function daysLeft(connection: ConnectionView, now: number): number | undefined {
  if (connection.expiresAt) {
    const end = Date.parse(connection.expiresAt);
    if (!Number.isNaN(end)) return Math.max(Math.ceil((end - now) / 86_400_000), 0);
  }
  if (typeof connection.tokenDay === 'number') return Math.max(TOKEN_LIFETIME_DAYS - connection.tokenDay, 0);
  return undefined;
}

/** The greeting after returning from LinkedIn's sign-in: success, or why it didn't connect. */
function ReturnNotice({ connection, since }: { connection: ConnectionView; since: string }) {
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;
  // Connected at or after the tab left for LinkedIn (a few minutes' leeway for clock skew).
  const connectedAt = connection.connectedAt ? Date.parse(connection.connectedAt) : Number.NaN;
  const connectedNow = connection.status === 'connected' && connectedAt >= Date.parse(since) - 5 * 60_000;
  if (connectedNow) {
    return (
      <Alert variant="success" role="status">
        <AlertTitle>LinkedIn is connected.</AlertTitle>
        <AlertDescription>
          <p>One-tap publishing works for the next {String(TOKEN_LIFETIME_DAYS)} days. The app reminds you before it ends.</p>
        </AlertDescription>
      </Alert>
    );
  }
  if (!connection.lastError) return null;
  return (
    <Alert variant="error">
      <AlertTitle>LinkedIn didn’t connect.</AlertTitle>
      <AlertDescription>
        <p>{connectionErrorSentence(connection.lastError)}</p>
      </AlertDescription>
      <AlertActions>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setHidden(true);
          }}
        >
          Dismiss
        </Button>
      </AlertActions>
    </Alert>
  );
}

/**
 * The LinkedIn connection (phone-first): connect and reconnect through LinkedIn's sign-in page,
 * the token's age on its 60-day ruler with the day-50 banner, the daily check and disconnect.
 */
export function LinkedInPanel() {
  const now = useNow();
  const [returnedSince] = useState(() => readConnectMarker(now));
  useEffect(() => {
    clearConnectMarker();
  }, []);
  const connection = useApiQuery(
    { queryKey: settingsKeys.linkedin, path: '/api/linkedin/connection' },
    { refetchOnWindowFocus: true },
  );
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const start = useApiMutation('post', '/api/linkedin/oauth/start', {
    errorToast: false,
  });
  const check = useApiMutation('post', '/api/linkedin/introspect', {
    updates: () => settingsKeys.linkedin,
    invalidates: [settingsKeys.systemStatus],
    successToast: 'Checked the LinkedIn token with LinkedIn.',
    errorToast: 'Couldn’t check the token.',
  });
  const disconnect = useApiMutation('post', '/api/linkedin/disconnect', {
    updates: () => settingsKeys.linkedin,
    invalidates: [settingsKeys.systemStatus],
    successToast: 'LinkedIn disconnected. Posts go out through the ready-to-post kit.',
    errorToast: 'Couldn’t disconnect LinkedIn.',
  });

  const connect = () => {
    start.mutate(undefined, {
      onSuccess: ({ authorizeUrl }) => {
        if (isLinkedInAuthorizeUrl(authorizeUrl)) leaveForLinkedIn(authorizeUrl);
        else toast.error('Couldn’t start connecting LinkedIn.', { description: 'The server answered with an unexpected address.' });
      },
    });
  };

  if (connection.isPending) return <BlockLoading label="Loading the LinkedIn connection" />;
  if (connection.isError) {
    return (
      <ProblemAlert
        error={connection.error}
        title="Couldn’t load the LinkedIn connection."
        onRetry={() => void connection.refetch()}
        retryLabel="Try loading the LinkedIn connection again"
      />
    );
  }
  const view = connection.data;
  const left = daysLeft(view, now);
  const startError = start.isError ? (
    <Alert variant="error" className="mt-4">
      <AlertTitle>Couldn’t start connecting LinkedIn.</AlertTitle>
      <AlertDescription>
        <p>
          {isProblem(start.error, 'linkedin_not_configured')
            ? 'The LinkedIn app isn’t set up on this server.'
            : isProblem(start.error, 'vault_unavailable')
              ? 'Token encryption isn’t set up on this server, so a connection couldn’t be stored.'
              : problemMessage(start.error)}
        </p>
      </AlertDescription>
    </Alert>
  ) : null;

  if (!view.configured) {
    return (
      <div className="max-w-measure space-y-3">
        <StateBadge look={{ label: 'Not set up', tone: 'neutral', icon: CircleMinus }} />
        <p className="text-ink-muted">
          The LinkedIn app registration isn’t on this server yet, so one-tap publishing is off. Posts go out through the
          ready-to-post kit, which needs no connection.
        </p>
      </div>
    );
  }

  const connectButton = (label: string, primary: boolean) => (
    <Button
      size="lg"
      variant={primary ? 'primary' : 'secondary'}
      className="w-full sm:w-auto"
      disabled={start.isPending}
      onClick={connect}
    >
      <LogIn aria-hidden="true" />
      {start.isPending ? 'Opening LinkedIn…' : label}
    </Button>
  );

  return (
    <div className="max-w-measure space-y-6">
      {returnedSince ? <ReturnNotice connection={view} since={returnedSince} /> : null}

      <div className="flex flex-wrap items-center gap-3">
        <StateBadge look={STATUS_LOOKS[view.status]} />
        {view.status !== 'disconnected' && left !== undefined ? (
          <p className="text-sm text-ink-muted">
            {left === 0 ? 'The token has run out.' : `${String(left)} ${left === 1 ? 'day' : 'days'} left`}
            {view.expiresAt ? ` (until ${formatDate(view.expiresAt)})` : null}
          </p>
        ) : null}
      </div>

      {view.status === 'connected' && view.bannerDue ? (
        <Alert variant="warn">
          <AlertTitle>
            {left === undefined
              ? 'Your LinkedIn connection ends soon.'
              : `Your LinkedIn connection ends in ${String(left)} ${left === 1 ? 'day' : 'days'}.`}
          </AlertTitle>
          <AlertDescription>
            <p>Reconnect now: it takes a minute and starts a fresh 60 days. Posts keep their slots either way.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {view.status === 'needs_reconnect' ? (
        <Alert variant="error">
          <AlertTitle>One-tap publishing is off until you reconnect.</AlertTitle>
          <AlertDescription>
            <p>{view.lastError ? connectionErrorSentence(view.lastError) : 'The token can’t be used any more.'}</p>
            <p>Posts at a slot can still go out through the ready-to-post kit.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {view.status !== 'disconnected' && typeof view.tokenDay === 'number' ? (
        <TokenRuler day={view.tokenDay} needsReconnect={view.status === 'needs_reconnect'} />
      ) : null}

      {view.status === 'disconnected' ? (
        <p className="text-ink-muted">
          Connect once for one-tap publishing at a slot. Without it, posts go out through the ready-to-post kit: you paste
          them into LinkedIn yourself.
        </p>
      ) : (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {view.connectedAt ? (
            <>
              <dt className="text-ink-subtle">Connected on</dt>
              <dd className="text-ink">{formatDateTime(view.connectedAt)}</dd>
            </>
          ) : null}
          <dt className="text-ink-subtle">Last checked</dt>
          <dd className="text-ink">
            {view.lastIntrospectedAt ? (
              <time dateTime={view.lastIntrospectedAt} title={formatDateTime(view.lastIntrospectedAt)}>
                {formatRelative(view.lastIntrospectedAt, now)}
              </time>
            ) : (
              'Not yet'
            )}
          </dd>
          <dt className="text-ink-subtle">Permissions</dt>
          <dd className="text-ink">Sign in and post as you ({view.scopes.join(', ')})</dd>
          <dt className="text-ink-subtle">API version</dt>
          <dd className="text-ink">{view.apiVersion}</dd>
        </dl>
      )}

      <div className="flex flex-wrap gap-2">
        {view.status === 'connected' ? (
          <>
            {connectButton('Reconnect', view.bannerDue)}
            <Button
              size="lg"
              variant="secondary"
              className="w-full sm:w-auto"
              disabled={check.isPending}
              onClick={() => {
                check.mutate(undefined);
              }}
            >
              <RefreshCw aria-hidden="true" />
              {check.isPending ? 'Checking…' : 'Check the token now'}
            </Button>
          </>
        ) : (
          connectButton(view.status === 'needs_reconnect' ? 'Reconnect LinkedIn' : 'Connect LinkedIn', true)
        )}
        {view.status !== 'disconnected' ? (
          <Button
            size="lg"
            variant="ghost"
            className="w-full sm:w-auto"
            disabled={disconnect.isPending}
            onClick={() => {
              setConfirmDisconnect(true);
            }}
          >
            <Unlink aria-hidden="true" />
            Disconnect
          </Button>
        ) : null}
      </div>
      {startError}

      <AlertDialog open={confirmDisconnect} onOpenChange={setConfirmDisconnect}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect LinkedIn?</AlertDialogTitle>
            <AlertDialogDescription>
              The stored token is deleted. Approved posts stay approved and go out through the ready-to-post kit until you
              connect again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Stay connected</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              onClick={() => {
                disconnect.mutate(undefined);
              }}
            >
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
