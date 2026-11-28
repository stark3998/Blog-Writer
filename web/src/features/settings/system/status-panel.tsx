import { CircleCheck, CircleHelp, CirclePause, OctagonAlert, Power } from 'lucide-react';
import { useState } from 'react';

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
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { BlockLoading, SubHeading } from '../components/settings-block';
import { ServiceBadge, StateBadge, serviceSentence, type StateLook } from '../components/status-badge';
import { formatDateTime } from '../format';
import { settingsKeys, useSystemStatus, type Schemas } from '../queries';

type ServicesView = Schemas['ServicesView'];
type SchedulesView = Schemas['SchedulesView'];

/**
 * What the schedule items' freshness means (`SystemStatus.schedules`). `schedulerRunning`
 * (`runner.schedulerRunning`) decides what a failed update means meanwhile: a running scheduler
 * may still fire the earlier times, while a held one (the boot's first update has not landed)
 * runs nothing until it lands.
 */
export function schedulesLook(view: SchedulesView, schedulerRunning: boolean): { look: StateLook; sentence: string } {
  switch (view.state) {
    case 'current':
      return {
        look: { label: 'Up to date', tone: 'success', icon: CircleCheck },
        sentence: view.seededAt ? `Match your cadence since ${formatDateTime(view.seededAt)}.` : 'Match your cadence.',
      };
    case 'out_of_date':
      return {
        look: { label: 'Out of date', tone: 'warn', icon: OctagonAlert },
        sentence:
          view.reason === 'not_seeded'
            ? 'Being brought up to date after the restart. The scheduler starts once they match your cadence.'
            : view.reason === 'partial'
              ? 'One area’s schedule couldn’t be read, so its earlier times are kept. Check the alerts.'
              : view.retrying
                ? schedulerRunning
                  ? 'The last update failed and is being retried. Until it lands, earlier times may still run.'
                  : 'The last update failed and is being retried. Scheduled jobs wait until it lands.'
                : 'They may not match your cadence. Save the cadence again in Slots to update them.',
      };
    case 'unknown':
      return {
        look: { label: 'Unknown', tone: 'neutral', icon: CircleHelp },
        sentence: 'Their state isn’t known on this server.',
      };
  }
}

const SERVICE_NAMES: readonly [keyof ServicesView, string][] = [
  ['ai', 'AI models'],
  ['embeddings', 'Embeddings'],
  ['storage', 'File storage'],
  ['render', 'Rendering'],
  ['fetcher', 'Page fetcher'],
  ['webiq', 'Web IQ'],
  ['vault', 'Token encryption'],
  ['push', 'Push notifications'],
  ['linkedin', 'LinkedIn'],
  ['portfolio', 'Portfolio publishing'],
];

function shortSha(sha: string): string {
  return /^[0-9a-f]{40}$/i.test(sha) ? sha.slice(0, 7) : sha;
}

/**
 * The running app: safe mode (and leaving it), the job runner and scheduler, every service the
 * app depends on, and expiries to watch.
 */
export function StatusPanel() {
  const status = useSystemStatus();
  const [confirming, setConfirming] = useState(false);
  const exit = useApiMutation('post', '/api/system/safe-mode/exit', {
    updates: () => settingsKeys.systemStatus,
    invalidates: [queryKeys.jobs.all],
    // The scheduler stays held while the schedules' first update after the restart has not landed.
    successToast: (after) =>
      after.runner.schedulerRunning
        ? 'Safe mode is off. The runner and the scheduler are running again.'
        : 'Safe mode is off. The runner is running again; scheduled jobs wait until the schedules are up to date.',
    errorToast: 'Couldn’t leave safe mode.',
  });

  if (status.isPending) return <BlockLoading label="Loading the system status" lines={5} />;
  if (status.isError) {
    return (
      <ProblemAlert
        error={status.error}
        title="Couldn’t load the system status."
        onRetry={() => void status.refetch()}
        retryLabel="Try loading the system status again"
      />
    );
  }
  const view = status.data;
  const schedules = schedulesLook(view.schedules, view.runner.schedulerRunning);
  const runnerLook = view.runner.running
    ? { label: 'Running', tone: 'success' as const, icon: CircleCheck }
    : { label: 'Stopped', tone: 'warn' as const, icon: CirclePause };

  return (
    <div className="grid gap-8">
      {view.safeMode ? (
        <Alert variant="warn" className="max-w-measure">
          <AlertTitle>Safe mode: nothing runs in the background.</AlertTitle>
          <AlertDescription>
            <p>
              {`The app restarted ${String(view.boot?.boots ?? 0)} times within an hour, so the job runner and the scheduler stayed off.`}{' '}
              Check the failed jobs below first, then leave safe mode.
            </p>
          </AlertDescription>
          <AlertActions>
            <Button
              size="sm"
              disabled={exit.isPending}
              onClick={() => {
                setConfirming(true);
              }}
            >
              <Power aria-hidden="true" />
              {exit.isPending ? 'Starting…' : 'Leave safe mode'}
            </Button>
          </AlertActions>
        </Alert>
      ) : null}

      <dl className="grid max-w-measure grid-cols-[auto_minmax(0,1fr)] items-center gap-x-6 gap-y-3">
        <dt className="text-ink-subtle">Job runner</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <StateBadge look={runnerLook} />
          <span className="text-sm text-ink-muted">
            {`${String(view.runner.claimed)} ${view.runner.claimed === 1 ? 'job' : 'jobs'} in hand`}
          </span>
        </dd>
        <dt className="text-ink-subtle">Scheduler</dt>
        <dd>
          <StateBadge
            look={
              view.runner.schedulerRunning
                ? { label: 'Running', tone: 'success', icon: CircleCheck }
                : { label: 'Stopped', tone: 'warn', icon: OctagonAlert }
            }
          />
        </dd>
        <dt className="text-ink-subtle">Schedules</dt>
        <dd className="flex flex-wrap items-center gap-2">
          <StateBadge look={schedules.look} />
          <span className="text-sm text-ink-muted">{schedules.sentence}</span>
        </dd>
        <dt className="text-ink-subtle">Server build</dt>
        <dd className="text-ink">
          <code className="font-mono text-sm" title={view.buildSha}>
            {shortSha(view.buildSha)}
          </code>
        </dd>
        {typeof view.expiry.linkedinTokenDay === 'number' ? (
          <>
            <dt className="text-ink-subtle">LinkedIn token</dt>
            <dd className="text-ink">{`Day ${String(view.expiry.linkedinTokenDay)} of 60`}</dd>
          </>
        ) : null}
        {view.expiry.patExpiresAt ? (
          <>
            <dt className="text-ink-subtle">Portfolio token</dt>
            <dd className="text-ink">{`Expires ${formatDateTime(view.expiry.patExpiresAt)}`}</dd>
          </>
        ) : null}
      </dl>

      <div>
        <SubHeading>Services</SubHeading>
        <ul aria-label="Services" className="mt-3 max-w-3xl border-t border-line">
          {SERVICE_NAMES.map(([key, name]) => {
            const service = view.services[key];
            return (
              <li
                key={key}
                className="grid gap-x-6 gap-y-1 border-b border-line py-3 sm:grid-cols-[12rem_minmax(0,1fr)_auto] sm:items-center"
              >
                <span className="font-medium text-ink">{name}</span>
                <span className="text-sm text-ink-muted">{serviceSentence(service)}</span>
                <span className="sm:justify-self-end">
                  <ServiceBadge view={service} />
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Leave safe mode?</AlertDialogTitle>
            <AlertDialogDescription>
              The runner starts again and queued jobs run. The scheduler starts too once the schedules match your cadence,
              and schedules past their lateness wait for your confirmation. If the app keeps restarting, it returns to safe
              mode.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Stay in safe mode</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                exit.mutate();
              }}
            >
              Leave safe mode
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
