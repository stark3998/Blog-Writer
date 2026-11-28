import { JOB_EVENT, type JobEventRecord } from './events';
import type { JobView } from './job-store';

/** A long step's progress (`job` events of type `progress`, contract section 9). */
export interface JobProgress {
  /** The step that reported it, or `null` for the job as a whole. */
  readonly step: string | null;
  readonly done: number;
  readonly total: number;
  /** Owner-facing words for the unit ("sources read"), when the step sent them. */
  readonly label: string | undefined;
  /** `done / total` between 0 and 1, or `undefined` when the total is unknown (0). */
  readonly fraction: number | undefined;
  readonly at: string;
}

/**
 * The job's newest `progress` event (of `step`, when given). It is the store's own record, so
 * `useSyncExternalStore` can compare it by reference.
 */
export function latestProgressEvent(job: JobView | undefined, step?: string): JobEventRecord | undefined {
  if (!job) return undefined;
  for (let index = job.events.length - 1; index >= 0; index -= 1) {
    const event = job.events[index];
    if (event?.type === JOB_EVENT.progress && (step === undefined || event.step === step)) return event;
  }
  return undefined;
}

function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
}

/** Reads a `progress` event's data; `undefined` when it does not carry a usable `done` and `total`. */
export function parseProgress(event: JobEventRecord): JobProgress | undefined {
  if (event.type !== JOB_EVENT.progress) return undefined;
  const done = count(event.data.done);
  const total = count(event.data.total);
  if (done === undefined || total === undefined) return undefined;
  const label = typeof event.data.label === 'string' && event.data.label !== '' ? event.data.label : undefined;
  return {
    step: event.step,
    done,
    total,
    label,
    fraction: total > 0 ? Math.min(1, done / total) : undefined,
    at: event.at,
  };
}
