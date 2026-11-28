import { Bell, BellOff, BellRing, CircleAlert, CircleCheck, CircleMinus, Send, Smartphone } from 'lucide-react';
import type { ReactNode } from 'react';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiMutation } from '@/lib/query/api-query';

import { StateBadge, type StateLook } from '../components/status-badge';
import type { ServiceView } from '../queries';
import { usePush, type DeviceState } from './use-push';

const LOOKS = {
  on: { label: 'On for this device', tone: 'success', icon: CircleCheck },
  off: { label: 'Off on this device', tone: 'neutral', icon: CircleMinus },
  blocked: { label: 'Blocked', tone: 'warn', icon: CircleAlert },
  unavailable: { label: 'Not available here', tone: 'neutral', icon: CircleMinus },
} as const satisfies Record<string, StateLook>;

function unsupportedText(state: Extract<DeviceState, { kind: 'unsupported' }>): string {
  switch (state.support) {
    case 'install-first':
      return 'On iPhone and iPad, notifications work once the app is on your Home Screen: tap Share, then Add to Home Screen, and open the app from there.';
    case 'no-service-worker':
      return 'This browser can’t receive notifications for the app. Private windows and older browsers can’t.';
    case 'no-push':
      return 'This browser doesn’t support web notifications.';
  }
}

/** "Sent to 1 device." / "Sent to 2 devices, 1 failed." */
function testOutcome(push: { delivered: number; failed: number; expired: number } | null | undefined): string {
  if (!push) return 'Test alert sent.';
  const parts = [`Sent to ${String(push.delivered)} ${push.delivered === 1 ? 'device' : 'devices'}`];
  if (push.failed > 0) parts.push(`${String(push.failed)} failed`);
  if (push.expired > 0) parts.push(`${String(push.expired)} no longer subscribed`);
  return `${parts.join(', ')}.`;
}

export interface PushSetupProps {
  /** The server's view of push (`services.push`), to notice a subscription the server lost. */
  service?: ServiceView;
  /** Smaller headings and no explanation (System's device panel). */
  compact?: boolean;
}

/**
 * Notifications on this device (phone-first): the permission prompt on a tap, subscribing with the
 * server's VAPID key, a test notification, and turning them off. Notifications carry a title and a
 * link only; reminders, staging notices and veto notices arrive this way.
 */
export function PushSetup({ service, compact = false }: PushSetupProps) {
  const push = usePush();
  const test = useApiMutation('post', '/api/system/test-alert', {
    successToast: (data) => `Test notification: ${testOutcome(data.push)}`,
    errorToast: 'Couldn’t send a test notification.',
  });
  const state = push.device.data;

  if (push.device.isPending || !state) {
    return (
      <div className="max-w-measure">
        <p role="status" className="sr-only">
          Checking notifications on this device
        </p>
        <Skeleton className="h-6 w-48" />
        <Skeleton className="mt-3 h-11 w-full max-w-64" />
      </div>
    );
  }

  const buttonClass = 'w-full sm:w-auto';
  let body: ReactNode;
  switch (state.kind) {
    case 'unsupported':
      body = (
        <>
          <StateBadge look={LOOKS.unavailable} />
          <p className="flex items-start gap-2 text-ink-muted">
            <Smartphone aria-hidden="true" className="mt-1 size-4 shrink-0 text-ink-subtle" />
            {unsupportedText(state)}
          </p>
        </>
      );
      break;
    case 'no-worker':
      body = (
        <>
          <StateBadge look={LOOKS.unavailable} />
          <p className="text-ink-muted">
            The app’s service worker isn’t running on this device yet. Reload the app once and come back. Development builds
            have none.
          </p>
        </>
      );
      break;
    case 'denied':
      body = (
        <>
          <StateBadge look={LOOKS.blocked} />
          <p className="text-ink-muted">
            Notifications are blocked for this site. Allow them in the browser’s site settings (on a phone: the app’s
            notification settings), then come back here.
          </p>
        </>
      );
      break;
    case 'off':
      body = (
        <>
          <StateBadge look={LOOKS.off} />
          {push.serverMissing ? (
            <p className="text-ink-muted">
              Push isn’t set up on the server yet (its VAPID key is missing), so nothing could be sent. Reminders still
              show in Today.
            </p>
          ) : push.keyError ? (
            <ProblemAlert error={push.keyError} title="Couldn’t read the server’s push key." />
          ) : (
            <>
              {compact ? null : (
                <p className="text-ink-muted">
                  The browser asks for permission once. Turn it on for the phone you carry: post slots, reminders and
                  veto notices arrive as notifications.
                </p>
              )}
              <Button
                size="lg"
                className={buttonClass}
                disabled={push.busy || !push.serverReady}
                onClick={push.turnOn}
              >
                <Bell aria-hidden="true" />
                {push.busy ? 'Turning on…' : 'Turn on notifications'}
              </Button>
            </>
          )}
        </>
      );
      break;
    case 'on': {
      const lost = service?.state === 'degraded' && service.reason === 'no_subscriptions';
      body = (
        <>
          <StateBadge look={LOOKS.on} />
          {lost ? (
            <Alert variant="warn">
              <AlertTitle>The server has no device registered.</AlertTitle>
              <AlertDescription>
                <p>This device is subscribed, but the server lost the record, so nothing reaches it.</p>
              </AlertDescription>
              <AlertActions>
                <Button size="sm" disabled={push.busy} onClick={push.registerAgain}>
                  <BellRing aria-hidden="true" />
                  Register this device again
                </Button>
              </AlertActions>
            </Alert>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              size="lg"
              variant="secondary"
              className={buttonClass}
              disabled={test.isPending}
              onClick={() => {
                test.mutate({ body: { push: true } });
              }}
            >
              <Send aria-hidden="true" />
              {test.isPending ? 'Sending…' : 'Send a test notification'}
            </Button>
            <Button size="lg" variant="ghost" className={buttonClass} disabled={push.busy} onClick={push.turnOff}>
              <BellOff aria-hidden="true" />
              {push.busy ? 'Turning off…' : 'Turn off'}
            </Button>
          </div>
          {test.isSuccess ? (
            <p role="status" className="text-sm text-ink-muted">
              {testOutcome(test.data.push)} It shows the title only and opens Settings when tapped.
            </p>
          ) : null}
        </>
      );
      break;
    }
  }

  return <div className="grid max-w-measure justify-items-start gap-4">{body}</div>;
}
