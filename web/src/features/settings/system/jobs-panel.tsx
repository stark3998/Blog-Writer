import { ChevronDown, RotateCw, Square } from 'lucide-react';
import { useState } from 'react';

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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field } from '@/components/ui/field';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useLiveEvents } from '@/features/jobs/use-job';
import { isLiveJobStatus, type JobStatus } from '@/lib/events/events';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { LiveJob } from '../components/live-job';
import { BlockLoading } from '../components/settings-block';
import { JOB_STATUS_LOOKS, JobStatusBadge } from '../components/status-badge';
import { useNow } from '@/lib/use-now';
import { JOB_TYPES, formatDateTime, formatRelative, jobTypeLabel } from '../format';
import { settingsKeys, type JobView } from '../queries';

const ALL = 'all' as const;
const STATUS_ORDER: readonly JobStatus[] = ['running', 'waiting_input', 'queued', 'failed', 'quarantined', 'cancelled', 'succeeded'];
const CANCELLABLE: ReadonlySet<JobStatus> = new Set(['queued', 'running', 'waiting_input']);
const RETRYABLE: ReadonlySet<JobStatus> = new Set(['failed', 'quarantined', 'cancelled']);

function JobActions({ job }: { job: JobView }) {
  const [confirming, setConfirming] = useState(false);
  const name = jobTypeLabel(job.type);
  const cancel = useApiMutation('post', '/api/jobs/{job_id}/cancel', {
    invalidates: [queryKeys.jobs.all],
    successToast: `Cancelling: ${name}. It stops at its next safe point.`,
    errorToast: `Couldn’t cancel “${name}”.`,
  });
  const retry = useApiMutation('post', '/api/jobs/{job_id}/retry', {
    invalidates: [queryKeys.jobs.all],
    successToast: `Queued again: ${name}.`,
    errorToast: `Couldn’t retry “${name}”.`,
  });
  const params = { params: { path: { job_id: job.jobId } } };
  if (!CANCELLABLE.has(job.status) && !RETRYABLE.has(job.status)) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {RETRYABLE.has(job.status) ? (
        <Button
          size="sm"
          disabled={retry.isPending}
          onClick={() => {
            retry.mutate(params);
          }}
        >
          <RotateCw aria-hidden="true" />
          {retry.isPending ? 'Queueing…' : 'Retry'}
        </Button>
      ) : null}
      {CANCELLABLE.has(job.status) && !job.cancelRequested ? (
        <Button
          size="sm"
          variant="secondary"
          disabled={cancel.isPending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          <Square aria-hidden="true" />
          Cancel
        </Button>
      ) : null}
      {job.cancelRequested ? <p className="text-sm text-ink-muted">Cancelling at the next safe point…</p> : null}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Cancel “${name}”?`}</AlertDialogTitle>
            <AlertDialogDescription>
              It stops at its next safe point and its document waits for you. You can retry it from here later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Let it run</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              onClick={() => {
                cancel.mutate(params);
              }}
            >
              Cancel the job
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function JobRow({ job, now }: { job: JobView; now: number }) {
  const [open, setOpen] = useState(false);
  const name = jobTypeLabel(job.type);
  return (
    <li className="border-b border-line">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full min-h-11 flex-wrap items-center gap-x-4 gap-y-1 py-3 text-left hover:bg-wash"
          >
            <ChevronDown
              aria-hidden="true"
              className={`size-4 shrink-0 text-ink-subtle transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
            />
            <span className="min-w-0 flex-1 font-medium text-ink">
              {name}
              <span className="sr-only">{`, ${JOB_STATUS_LOOKS[job.status].label}`}</span>
            </span>
            <span aria-hidden="true">
              <JobStatusBadge status={job.status} />
            </span>
            {job.attempt > 1 ? (
              <span className="text-xs text-ink-subtle">{`Attempt ${String(job.attempt)} of ${String(job.maxAttempts)}`}</span>
            ) : null}
            <time dateTime={job.updatedAt} title={formatDateTime(job.updatedAt)} className="text-sm text-ink-muted">
              {formatRelative(job.updatedAt, now)}
            </time>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="grid gap-4 pb-5 pl-8">
            <LiveJob jobId={job.jobId} title="Steps" />
            {job.awaiting ? <p className="max-w-measure text-ink">{job.awaiting.prompt}</p> : null}
            {job.error ? (
              <p className="max-w-measure text-sm text-ink-muted">
                {`Error code ${job.error.code}${job.error.step ? ` in “${job.error.step}”` : ''}, ${formatDateTime(job.error.at)}.`}
              </p>
            ) : null}
            <p className="text-xs text-ink-subtle">
              Job id <code className="font-mono">{job.jobId}</code>
            </p>
            <JobActions job={job} />
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

/**
 * Background jobs, newest change first: their steps live, with cancel and retry. The event stream
 * stays open while any listed job may still change, so the list follows along.
 */
export function JobsPanel() {
  const now = useNow();
  const [status, setStatus] = useState<JobStatus | typeof ALL>(ALL);
  const [type, setType] = useState<string>(ALL);
  const query = {
    ...(status === ALL ? {} : { status }),
    ...(type === ALL ? {} : { type }),
  };
  const jobs = useApiInfiniteQuery({
    queryKey: settingsKeys.jobs({ status, type }),
    path: '/api/jobs',
    init: { params: { query } },
    pageSize: 20,
  });
  const list = jobs.data ?? [];
  useLiveEvents(list.some((entry) => isLiveJobStatus(entry.item.status)));

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap gap-4">
        <Field label="Status">
          {(control) => (
            <Select
              value={status}
              onValueChange={(value) => {
                setStatus(STATUS_ORDER.find((item) => item === value) ?? ALL);
              }}
            >
              <SelectTrigger {...control} className="min-w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>Any status</SelectItem>
                {STATUS_ORDER.map((value) => (
                  <SelectItem key={value} value={value}>
                    {JOB_STATUS_LOOKS[value].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Kind of job">
          {(control) => (
            <Select value={type} onValueChange={setType}>
              <SelectTrigger {...control} className="min-w-60">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>Every kind</SelectItem>
                {JOB_TYPES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {jobTypeLabel(value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>

      {jobs.isPending ? (
        <BlockLoading label="Loading the jobs" />
      ) : jobs.isError ? (
        <ProblemAlert
          error={jobs.error}
          title="Couldn’t load the jobs."
          onRetry={() => void jobs.refetch()}
          retryLabel="Try loading the jobs again"
        />
      ) : list.length === 0 ? (
        <p className="text-ink-muted">
          {status === ALL && type === ALL ? 'No jobs have run yet.' : 'No jobs match these filters.'}
        </p>
      ) : (
        <>
          <ul aria-label="Jobs" className="max-w-4xl border-t border-line">
            {list.map((entry) => (
              <JobRow key={entry.item.jobId} job={entry.item} now={now} />
            ))}
          </ul>
          {jobs.hasNextPage ? (
            <Button
              variant="secondary"
              className="justify-self-start"
              disabled={jobs.isFetchingNextPage}
              onClick={() => void jobs.fetchNextPage()}
            >
              {jobs.isFetchingNextPage ? 'Loading…' : 'Show more jobs'}
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}
