import { useEffect } from 'react';

import { ProblemAlert } from '@/features/data/problem-alert';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useAppData } from '@/lib/app-data';
import { parseSnapshot } from '@/lib/events/events';
import { useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

/**
 * `GET /api/jobs/{id}` (the SSE snapshot shape), put into the app's job store as a snapshot, so a
 * `JobTimeline` shows a job the event stream never sent (one that finished before the page
 * opened). The store merges by `seq`, so a read older than the live events changes nothing. Status
 * events refresh `['jobs']`, which re-reads this too.
 */
export function useJobSnapshot(jobId: string) {
  const { jobs } = useAppData();
  const query = useApiQuery({
    queryKey: queryKeys.jobs.detail(jobId),
    path: '/api/jobs/{job_id}',
    init: { params: { path: { job_id: jobId } } },
  });
  const job = query.data;
  useEffect(() => {
    if (!job) return;
    const snapshot = parseSnapshot(job);
    if (snapshot) jobs.apply({ type: 'snapshot', job: snapshot });
  }, [jobs, job]);
  return query;
}

export interface LiveJobProps {
  jobId: string;
  /** The heading above the steps ("Crawling the feeds"). */
  title?: string;
  /** Owner-facing labels by step name. */
  labels?: Readonly<Record<string, string>>;
  className?: string;
}

/** A job's steps, live while it runs: the REST snapshot first, then the event stream. */
export function LiveJob({ jobId, title, labels, className }: LiveJobProps) {
  const query = useJobSnapshot(jobId);
  return (
    <div className={className}>
      <JobTimeline jobId={jobId} title={title} labels={labels} />
      {query.isError ? (
        <ProblemAlert
          className="mt-3 max-w-measure"
          error={query.error}
          title="Couldn’t load this job’s details."
          onRetry={() => void query.refetch()}
        />
      ) : null}
    </div>
  );
}
