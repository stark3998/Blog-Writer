import { Circle, CircleAlert, CircleCheck, CircleDashed, CircleX, type LucideIcon } from 'lucide-react';
import { useId } from 'react';

import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/cn';
import { isLiveJobStatus, type JobFailure, type JobStatus, type StepStatus } from '@/lib/events/events';

import {
  useEventStreamStatus,
  useJobAttempt,
  useJobError,
  useJobProgress,
  useJobStatus,
  useJobSteps,
  useLiveEvents,
} from './use-job';

const STEP_STATUS: Record<StepStatus, { label: string; icon: LucideIcon; className: string }> = {
  pending: { label: 'not started', icon: Circle, className: 'text-ink-subtle' },
  running: { label: 'in progress', icon: CircleDashed, className: 'text-ink' },
  waiting_input: { label: 'waiting for you', icon: CircleAlert, className: 'text-warn' },
  succeeded: { label: 'done', icon: CircleCheck, className: 'text-success' },
  failed: { label: 'failed', icon: CircleX, className: 'text-danger' },
};

/** "research.search" -> "Research search": readable until a job type supplies its own labels. */
export function stepLabel(name: string): string {
  const words = name.replace(/[._-]+/g, ' ').trim();
  return words === '' ? name : words.charAt(0).toUpperCase() + words.slice(1);
}

/** One sentence about the job as a whole, when its steps alone do not say it. */
export function jobSummary(
  status: JobStatus | undefined,
  { error, attempt, maxAttempts }: { error: JobFailure | null; attempt: number; maxAttempts: number },
): string | undefined {
  switch (status) {
    case undefined:
      return undefined;
    case 'queued':
      return 'Queued. It starts when a slot is free.';
    case 'running':
      return attempt > 1 && maxAttempts > 0 ? `Attempt ${attempt} of ${maxAttempts}.` : undefined;
    case 'waiting_input':
      return 'Waiting for you.';
    case 'succeeded':
      return 'Finished.';
    case 'failed':
      return error?.detail ? `Stopped: ${error.detail}` : 'Stopped: the job failed.';
    case 'cancelled':
      return 'Cancelled.';
    case 'quarantined':
      return 'Stopped after repeated failures. It needs a look before it runs again.';
  }
}

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' });

function StepTime({ at }: { at: string | null }) {
  if (at === null) return null;
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return null;
  return (
    <time dateTime={at} className="shrink-0 text-sm tabular-nums text-ink-subtle">
      {timeFormat.format(date)}
    </time>
  );
}

/**
 * A running step's progress bar, when the step reports `progress` events. The bar is named and
 * carries "3 of 12 sources" as its value text; the visible count is hidden from screen readers,
 * so the row (read out whole on change) does not repeat at every tick.
 */
function StepProgress({ jobId, step, label }: { jobId: string; step: string; label: string }) {
  const progress = useJobProgress(jobId, step);
  if (!progress) return null;
  const counted = `${String(progress.done)} of ${String(progress.total)}${progress.label ? ` ${progress.label}` : ''}`;
  return (
    <div className="mt-2 flex items-center gap-3">
      <Progress
        size="sm"
        value={progress.total > 0 ? progress.done : null}
        max={progress.total > 0 ? progress.total : undefined}
        aria-label={`${label} progress`}
        getValueLabel={() => counted}
        className="max-w-48"
      />
      <span aria-hidden="true" className="shrink-0 text-xs tabular-nums text-ink-subtle">
        {counted}
      </span>
    </div>
  );
}

export interface JobTimelineProps {
  jobId: string;
  /** The heading above the steps. */
  title?: string;
  /** Labels by step name; other steps get a readable form of their name. */
  labels?: Readonly<Record<string, string>>;
  className?: string;
}

/**
 * A job's steps as they happen, from the event stream: one row per step with its state from the
 * step events. Keeps the stream open while the job may still change, and announces changes
 * politely to screen readers (each row is read out whole when it is added or changes).
 */
export function JobTimeline({ jobId, title = 'Progress', labels, className }: JobTimelineProps) {
  const steps = useJobSteps(jobId);
  const status = useJobStatus(jobId);
  const error = useJobError(jobId);
  const [attempt, maxAttempts] = useJobAttempt(jobId);
  const live = isLiveJobStatus(status);
  useLiveEvents(live);
  const streamStatus = useEventStreamStatus();
  const headingId = useId();
  const summary = jobSummary(status, { error, attempt, maxAttempts });

  return (
    <section aria-labelledby={headingId} className={cn('max-w-measure', className)}>
      <h3 id={headingId} className="font-semibold text-ink">
        {title}
      </h3>
      {/* The live region stays mounted, so the first step is announced too. */}
      <div aria-live="polite" className="mt-3">
        {steps.length === 0 ? (
          <p className="text-ink-muted">{live ? 'Waiting for the first step…' : 'No steps were recorded.'}</p>
        ) : (
          <ol aria-labelledby={headingId} className="border-t border-line">
            {steps.map((step) => {
              const { label, icon: Icon, className: tone } = STEP_STATUS[step.status];
              const name = labels?.[step.name] ?? stepLabel(step.name);
              return (
                <li
                  key={step.name}
                  aria-atomic="true"
                  data-status={step.status}
                  className="flex items-start gap-3 border-b border-line py-3"
                >
                  <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
                  <div className="min-w-0 flex-1">
                    <p className={step.status === 'pending' ? 'text-ink-muted' : 'text-ink'}>
                      {name}
                      <span className="sr-only">{`: ${label}`}</span>
                    </p>
                    {step.status === 'running' ? <StepProgress jobId={jobId} step={step.name} label={name} /> : null}
                  </div>
                  <StepTime at={step.finishedAt ?? step.startedAt} />
                </li>
              );
            })}
          </ol>
        )}
        {summary ? (
          <p aria-atomic="true" className="mt-3 text-ink-muted">
            {summary}
          </p>
        ) : null}
      </div>
      {live && streamStatus === 'reconnecting' ? (
        <p className="mt-2 text-sm text-ink-subtle">Live updates paused. Reconnecting…</p>
      ) : null}
    </section>
  );
}
