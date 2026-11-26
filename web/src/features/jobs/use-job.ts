import { useCallback, useEffect, useSyncExternalStore } from 'react';

import { useAppData } from '@/lib/app-data';
import type { EventStreamStatus } from '@/lib/events/event-stream';
import type { JobAwaiting, JobFailure, JobStatus, JobStepState } from '@/lib/events/events';
import type { JobView, StepText } from '@/lib/events/job-store';

const NO_STEPS: readonly JobStepState[] = [];

/**
 * Reads one field of a job from the store. Re-renders only when that field changes, so a timeline
 * does not re-render for every token of streamed text. `select` must return a value the store
 * already holds (or a primitive), never a new object.
 */
function useJobField<T>(jobId: string, select: (job: JobView | undefined) => T): T {
  const { jobs } = useAppData();
  const subscribe = useCallback((listener: () => void) => jobs.subscribe(jobId, listener), [jobs, jobId]);
  const read = () => select(jobs.get(jobId));
  return useSyncExternalStore(subscribe, read, read);
}

export function useJobSteps(jobId: string): readonly JobStepState[] {
  return useJobField(jobId, (job) => job?.steps ?? NO_STEPS);
}

export function useJobStatus(jobId: string): JobStatus | undefined {
  return useJobField(jobId, (job) => job?.status);
}

export function useJobAwaiting(jobId: string): JobAwaiting | null {
  return useJobField(jobId, (job) => job?.awaiting ?? null);
}

export function useJobError(jobId: string): JobFailure | null {
  return useJobField(jobId, (job) => job?.error ?? null);
}

/** The job's attempt and its limit, as numbers (both 0 when unknown). */
export function useJobAttempt(jobId: string): readonly [attempt: number, maxAttempts: number] {
  const attempt = useJobField(jobId, (job) => job?.attempt ?? 0);
  const maxAttempts = useJobField(jobId, (job) => job?.maxAttempts ?? 0);
  return [attempt, maxAttempts];
}

/** One step's streamed text (token deltas) so far. */
export function useJobStepText(jobId: string, step: string): StepText | undefined {
  return useJobField(jobId, (job) => job?.text[step]);
}

/** Keeps the app-wide event stream open while `live` is true and the component is mounted. */
export function useLiveEvents(live: boolean): void {
  const { events } = useAppData();
  useEffect(() => (live ? events.retain() : undefined), [events, live]);
}

export function useEventStreamStatus(): EventStreamStatus {
  const { events } = useAppData();
  return useSyncExternalStore(events.subscribeStatus, events.getStatus, events.getStatus);
}
