import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiInfiniteQuery } from '@/lib/query/api-query';

import { formatDateTime, formatRelative } from '../format';
import { settingsKeys, type JobView } from '../queries';
import { BlockLoading } from './settings-block';
import { JobStatusBadge } from './status-badge';
import { useNow } from '@/lib/use-now';

/** The step a job is on (or failed at, or finished last), in the given words. */
export function currentStep(job: JobView, labels: Readonly<Record<string, string>>): string | undefined {
  const active = job.steps.find((step) => step.status === 'running' || step.status === 'waiting_input' || step.status === 'failed');
  const latest = active ?? [...job.steps].reverse().find((step) => step.status === 'succeeded');
  return latest ? (labels[latest.name] ?? latest.name) : undefined;
}

/**
 * The newest job of one type, in a line: its status, when, the step it is on, and what stopped it.
 * `GET /api/jobs?type=&limit=1`; job status events keep it current while the stream is open.
 */
export function LastJob({
  type,
  label,
  steps,
  empty,
}: {
  type: string;
  /** "Last publish", "Last crawl". */
  label: string;
  steps: Readonly<Record<string, string>>;
  /** Shown when no job of the type has run yet. */
  empty: string;
}) {
  const now = useNow();
  const jobs = useApiInfiniteQuery({
    queryKey: settingsKeys.jobs({ type, size: 1 }),
    path: '/api/jobs',
    init: { params: { query: { type } } },
    pageSize: 1,
  });
  if (jobs.isPending) return <BlockLoading label={`Loading the ${label.toLocaleLowerCase()}`} lines={1} />;
  if (jobs.isError) {
    return (
      <ProblemAlert
        error={jobs.error}
        title={`Couldn’t load the ${label.toLocaleLowerCase()}.`}
        onRetry={() => void jobs.refetch()}
        retryLabel={`Try loading the ${label.toLocaleLowerCase()} again`}
      />
    );
  }
  const job = jobs.data[0]?.item;
  if (!job) return <p className="text-sm text-ink-muted">{empty}</p>;
  const step = currentStep(job, steps);
  return (
    <div className="grid gap-1.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-ink-subtle">{label}</span>
        <JobStatusBadge status={job.status} />
        <time dateTime={job.updatedAt} title={formatDateTime(job.updatedAt)} className="text-ink-muted">
          {formatRelative(job.updatedAt, now)}
        </time>
      </div>
      {step && job.status !== 'succeeded' ? (
        <p className="text-ink-muted">{`Step: ${step}.`}</p>
      ) : null}
      {job.error?.detail ? <p className="text-ink">{job.error.detail}</p> : null}
      {job.status === 'waiting_input' && job.awaiting ? <p className="text-ink">{job.awaiting.prompt}</p> : null}
    </div>
  );
}
