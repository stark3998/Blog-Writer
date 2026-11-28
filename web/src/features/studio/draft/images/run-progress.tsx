import { Circle, CircleCheck, CircleDashed, CircleX, type LucideIcon } from 'lucide-react';
import { useId } from 'react';

import { Progress } from '@/components/ui/progress';
import { useEventStreamStatus, useJobProgress, useJobStatus, useJobSteps } from '@/features/jobs/use-job';
import { cn } from '@/lib/cn';
import type { JobFailure, JobStatus, StepStatus } from '@/lib/events/events';

import { formatDateTime } from '../model/format';
import { droppedReasons, type RunOutcome } from '../model/illustrations';

/** The steps of `illustrations.generate` (`illustrations.steps.STEP_NAMES`), in the owner's words. */
export const RUN_STEPS: readonly { readonly name: string; readonly label: string }[] = [
  { name: 'plan', label: 'Plan the images' },
  { name: 'generate', label: 'Make them' },
  { name: 'check', label: 'Check each one against the article' },
  { name: 'store', label: 'Keep the ones that pass' },
  { name: 'attach', label: 'Add them here' },
];

const STEP_LOOK: Readonly<Record<StepStatus, { icon: LucideIcon; className: string; label: string }>> = {
  pending: { icon: Circle, className: 'text-ink-subtle', label: 'not started' },
  running: { icon: CircleDashed, className: 'text-ink', label: 'in progress' },
  waiting_input: { icon: CircleDashed, className: 'text-ink', label: 'waiting' },
  succeeded: { icon: CircleCheck, className: 'text-success', label: 'done' },
  failed: { icon: CircleX, className: 'text-danger', label: 'failed' },
};

/** One sentence about where the run is, for the live region (it changes once per step, not per tick). */
function runSentence(status: JobStatus | undefined, step: string | undefined): string {
  if (status === undefined || status === 'queued') return 'Queued. It starts in a moment.';
  switch (step) {
    case undefined:
      return 'Working on it.';
    case 'plan':
      return 'Planning the images from the title, summary, headings and the pillar’s art brief.';
    case 'generate':
      return 'Making the images. Each takes up to a minute.';
    case 'check':
      return 'Checking that each image shows the idea, with no text, logos or real people.';
    default:
      return 'Keeping the images that passed.';
  }
}

/** The count shown while a step that reports one runs: images made, then images checked. */
const COUNTED: Readonly<Record<string, { name: string; words: string }>> = {
  generate: { name: 'Images made', words: 'made' },
  check: { name: 'Images checked', words: 'checked' },
};

/** A run that is going: its steps, the images made and checked so far, and one live sentence. */
export function RunProgress({ jobId }: { jobId: string }) {
  const headingId = useId();
  const steps = useJobSteps(jobId);
  const status = useJobStatus(jobId);
  const made = useJobProgress(jobId, 'generate');
  const checked = useJobProgress(jobId, 'check');
  const stream = useEventStreamStatus();
  const current = steps.find((step) => step.status === 'running')?.name;
  const count = current === 'generate' ? made : current === 'check' ? checked : undefined;
  const counted = current === undefined ? undefined : COUNTED[current];

  return (
    <section aria-labelledby={headingId} className="grid gap-3 rounded-lg border border-line bg-sheet p-4">
      <h4 id={headingId} className="font-medium text-ink">
        Making illustrations
      </h4>
      <ol className="grid gap-1.5 text-sm">
        {RUN_STEPS.map((step) => {
          const state = steps.find((candidate) => candidate.name === step.name)?.status ?? 'pending';
          const { icon: Icon, className, label } = STEP_LOOK[state];
          return (
            <li key={step.name} data-status={state} className="flex items-start gap-2">
              <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', className)} />
              <span className={state === 'pending' ? 'text-ink-muted' : 'text-ink'}>
                {step.label}
                <span className="sr-only">{`: ${label}`}</span>
              </span>
            </li>
          );
        })}
      </ol>
      {count !== undefined && counted !== undefined && count.total > 0 ? (
        <div className="flex items-center gap-3">
          <Progress
            size="sm"
            value={count.done}
            max={count.total}
            aria-label={counted.name}
            getValueLabel={() => `${String(count.done)} of ${String(count.total)}`}
          />
          <span aria-hidden="true" className="shrink-0 text-xs tabular-nums text-ink-subtle">
            {`${String(count.done)} of ${String(count.total)} ${counted.words}`}
          </span>
        </div>
      ) : (
        <Progress size="sm" aria-label="Making illustrations" />
      )}
      <p aria-live="polite" aria-atomic="true" className="text-sm text-ink-muted">
        {runSentence(status, current)}
      </p>
      {stream === 'reconnecting' ? <p className="text-xs text-ink-subtle">Live updates paused. Reconnecting…</p> : null}
    </section>
  );
}

/**
 * How a run that ended without new candidates ended, in the owner's words. With the run's
 * outcome, a run whose images all failed their checks also says why.
 */
export function runEndSentence(
  status: JobStatus | undefined,
  error: JobFailure | null,
  outcome?: RunOutcome,
): string | undefined {
  switch (status) {
    case 'failed': {
      const when = formatDateTime(error?.at);
      const detail = error?.detail ?? failureText(error?.code ?? '');
      const why = error?.code === 'no_illustrations' && outcome ? droppedReasons(outcome.reasons) : [];
      const reason = why.length > 0 ? ` Why: ${why.join(', ')}.` : '';
      return when ? `The run of ${when} stopped: ${detail}${reason}` : `The last run stopped: ${detail}${reason}`;
    }
    case 'cancelled':
      return 'The last run was cancelled before it finished.';
    case 'quarantined':
      return 'The last run stopped after repeated failures. Settings > System shows it.';
    case 'queued':
    case 'running':
    case 'waiting_input':
    case 'succeeded':
    case undefined:
      return undefined;
  }
}

/** For a failure without the server's own sentence (`JobFailed` details are owner-facing). */
function failureText(code: string): string {
  switch (code) {
    case 'no_illustrations':
      return 'No illustration passed its checks. Try another direction.';
    case 'images_disabled':
      return 'Illustrations are switched off in Settings > Models.';
    case 'images_unavailable':
      return 'No image model is set up.';
    case 'blocklist_hit':
      return 'Some text for the illustrations matches the blocklist.';
    case 'superseded':
      return 'A newer request replaced it.';
    default:
      return 'Something went wrong. Try again in a moment.';
  }
}
