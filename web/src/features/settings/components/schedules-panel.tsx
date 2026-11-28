import { CircleAlert, CirclePause, CircleDot, Pause, Play, SkipForward } from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { describeRule, formatDateTime, formatRelative, jobTypeLabel } from '../format';
import { settingsKeys, useSchedules, type ScheduleView } from '../queries';
import { BlockLoading } from './settings-block';
import { StateBadge, type StateLook } from './status-badge';
import { useNow } from '@/lib/use-now';

const STATUS_LOOKS: Readonly<Record<ScheduleView['status'], StateLook>> = {
  active: { label: 'Active', tone: 'success', icon: CircleDot },
  needs_confirmation: { label: 'Waiting for you', tone: 'warn', icon: CircleAlert },
  paused: { label: 'Paused', tone: 'neutral', icon: CirclePause },
};

function ScheduleActions({ schedule }: { schedule: ScheduleView }) {
  const options = { invalidates: [settingsKeys.schedules, queryKeys.jobs.all] } as const;
  const pause = useApiMutation('post', '/api/schedules/{schedule_id}/pause', {
    ...options,
    successToast: `Paused: ${schedule.name}.`,
    errorToast: `Couldn’t pause “${schedule.name}”.`,
  });
  const resume = useApiMutation('post', '/api/schedules/{schedule_id}/resume', {
    ...options,
    successToast: `Resumed: ${schedule.name}. It runs again from its next time.`,
    errorToast: `Couldn’t resume “${schedule.name}”.`,
  });
  const confirm = useApiMutation('post', '/api/schedules/{schedule_id}/confirm', {
    ...options,
    successToast: (_data, variables) =>
      variables.body.run ? `Started the missed run of “${schedule.name}”.` : `Skipped the missed run of “${schedule.name}”.`,
    errorToast: `Couldn’t settle the missed run of “${schedule.name}”.`,
  });
  const params = { params: { path: { schedule_id: schedule.id } } };
  const busy = pause.isPending || resume.isPending || confirm.isPending;

  switch (schedule.status) {
    case 'needs_confirmation':
      return (
        <>
          <Button
            size="sm"
            disabled={busy}
            aria-label={`Run the missed “${schedule.name}” now`}
            onClick={() => {
              confirm.mutate({ ...params, body: { run: true } });
            }}
          >
            <Play aria-hidden="true" />
            Run now
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            aria-label={`Skip the missed “${schedule.name}”`}
            onClick={() => {
              confirm.mutate({ ...params, body: { run: false } });
            }}
          >
            <SkipForward aria-hidden="true" />
            Skip it
          </Button>
        </>
      );
    case 'paused':
      return (
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          aria-label={`Resume “${schedule.name}”`}
          onClick={() => {
            resume.mutate(params);
          }}
        >
          <Play aria-hidden="true" />
          Resume
        </Button>
      );
    case 'active':
      return (
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          aria-label={`Pause “${schedule.name}”`}
          onClick={() => {
            pause.mutate(params);
          }}
        >
          <Pause aria-hidden="true" />
          Pause
        </Button>
      );
  }
}

function ScheduleRow({ schedule, now }: { schedule: ScheduleView; now: number }) {
  return (
    <li className="grid gap-x-6 gap-y-2 border-b border-line py-4 md:grid-cols-[minmax(0,1fr)_minmax(0,14rem)_auto] md:items-start">
      <div className="min-w-0">
        <p className="font-semibold text-ink">{schedule.name}</p>
        <p className="text-sm text-ink-muted">
          {describeRule(schedule.rule)} <span className="text-ink-subtle">({schedule.timezone})</span>
        </p>
        <p className="text-xs text-ink-subtle">{jobTypeLabel(schedule.jobType)}</p>
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-sm">
        {schedule.status === 'needs_confirmation' && schedule.pendingRunAt ? (
          <>
            <dt className="text-ink-subtle">Missed</dt>
            <dd className="text-ink">
              <time dateTime={schedule.pendingRunAt}>{formatDateTime(schedule.pendingRunAt, schedule.timezone)}</time>
            </dd>
          </>
        ) : (
          <>
            <dt className="text-ink-subtle">Next</dt>
            <dd className="text-ink">
              {schedule.status === 'paused' ? (
                'Not while paused'
              ) : (
                <time dateTime={schedule.nextRunAt} title={formatDateTime(schedule.nextRunAt, schedule.timezone)}>
                  {formatRelative(schedule.nextRunAt, now)}
                </time>
              )}
            </dd>
          </>
        )}
        <dt className="text-ink-subtle">Last</dt>
        <dd className="text-ink">
          {schedule.lastRunAt ? (
            <time dateTime={schedule.lastRunAt} title={formatDateTime(schedule.lastRunAt, schedule.timezone)}>
              {formatRelative(schedule.lastRunAt, now)}
            </time>
          ) : (
            'Not yet'
          )}
        </dd>
        {schedule.skippedRuns > 0 ? (
          <>
            <dt className="text-ink-subtle">Skipped</dt>
            <dd className="text-ink">{schedule.skippedRuns}</dd>
          </>
        ) : null}
      </dl>
      <div className="flex flex-wrap items-center gap-2 md:justify-end">
        <StateBadge look={STATUS_LOOKS[schedule.status]} />
        <ScheduleActions schedule={schedule} />
      </div>
    </li>
  );
}

/**
 * The schedule items (`GET /api/schedules`): what runs on its own and when, with pause and resume,
 * and the confirmation an item asks for after downtime (contract section 8: past its maximum
 * lateness a schedule waits for the owner instead of firing late).
 */
export function SchedulesPanel() {
  const schedules = useSchedules();
  const now = useNow();

  if (schedules.isPending) return <BlockLoading label="Loading the schedules" />;
  if (schedules.isError) {
    return (
      <ProblemAlert
        error={schedules.error}
        title="Couldn’t load the schedules."
        onRetry={() => void schedules.refetch()}
        retryLabel="Try loading the schedules again"
      />
    );
  }
  const items = schedules.data;
  const waiting = items.filter((item) => item.status === 'needs_confirmation');
  return (
    <div className="space-y-4">
      {waiting.length > 0 ? (
        <Alert variant="warn">
          <AlertTitle>
            {waiting.length === 1 ? '1 schedule is waiting for you.' : `${String(waiting.length)} schedules are waiting for you.`}
          </AlertTitle>
          <AlertDescription>
            <p>
              The app was down when they were due. Run the missed one now or skip it; nothing runs late without you.
            </p>
          </AlertDescription>
        </Alert>
      ) : null}
      {items.length === 0 ? (
        <p className="text-ink-muted">No schedules yet. They appear once the server has seeded them from the cadence.</p>
      ) : (
        <ul aria-label="Schedules" className="border-t border-line">
          {items.map((schedule) => (
            <ScheduleRow key={schedule.id} schedule={schedule} now={now} />
          ))}
        </ul>
      )}
    </div>
  );
}
