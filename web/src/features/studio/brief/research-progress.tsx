import { useQueryClient } from '@tanstack/react-query';
import { RotateCw } from 'lucide-react';
import { useEffect, useId, useState } from 'react';

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useJobStatus } from '@/features/jobs/use-job';
import { isEtagMismatch } from '@/lib/api/envelope';
import { isProblem } from '@/lib/api/problems';
import { queryKeys } from '@/lib/query/keys';

import { formatDateTime } from '../piece/format';

import { RESEARCH_STEP_LABELS, runErrorSentence, type ResearchRun } from './brief-model';
import { useCancelRun } from './brief-queries';

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'quarantined']);

/**
 * A research run at work: the topic, the live job timeline (steps and "3 of 12 sources") and
 * Cancel. When the job ends the run and the brief are reloaded (the job's events also say so).
 */
export function ResearchInProgress({
  run,
  etag,
  forPipeline = false,
}: {
  run: ResearchRun;
  etag: string | null;
  /** The article's pipeline waits on this run: cancelling it stops the pipeline as well. */
  forPipeline?: boolean;
}) {
  const headingId = useId();
  const queryClient = useQueryClient();
  const jobId = run.jobId ?? undefined;
  const jobStatus = useJobStatus(jobId ?? '');
  const cancel = useCancelRun();
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | undefined>(undefined);

  // The run document changes when its job ends; reload it (and its brief) then.
  useEffect(() => {
    if (jobStatus === undefined || !TERMINAL.has(jobStatus)) return;
    void queryClient.invalidateQueries({ queryKey: queryKeys.researchRuns.all });
    void queryClient.invalidateQueries({ queryKey: queryKeys.briefs.all });
  }, [jobStatus, queryClient]);

  const started = formatDateTime(run.startedAt ?? run.createdAt);
  const cancelRun = () => {
    if (!etag) return;
    cancel.mutate(
      { params: { path: { run_id: run.id } }, ifMatch: etag },
      {
        onSuccess: () => {
          setConfirming(false);
          setNotice(undefined);
        },
        onError: (error) => {
          setConfirming(false);
          if (isEtagMismatch(error)) {
            setNotice('The run moved on since this page loaded. Check where it stands, then cancel again if you still want to.');
            void queryClient.invalidateQueries({ queryKey: queryKeys.researchRuns.lists() });
          } else if (isProblem(error, 'invalid_transition')) {
            setNotice('It already finished, so there is nothing to cancel.');
            void queryClient.invalidateQueries({ queryKey: queryKeys.researchRuns.lists() });
          }
        },
      },
    );
  };

  return (
    <section aria-labelledby={headingId} className="rounded-lg border border-line bg-sunken p-5">
      <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
        Research in progress
      </h2>
      <p className="mt-2 max-w-measure text-ink-muted">
        <span className="text-ink">{run.topic}</span>
        {started ? ` Started ${started}.` : null} It takes a few minutes; you can leave this page and come back.
      </p>
      {jobId ? (
        <JobTimeline jobId={jobId} title="Steps" labels={RESEARCH_STEP_LABELS} className="mt-5" />
      ) : (
        <p className="mt-4 text-ink-muted">Queued. It starts when a slot is free.</p>
      )}
      {notice ? (
        <Alert variant="warn" role="status" className="mt-4">
          <AlertDescription>
            <p>{notice}</p>
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="mt-5">
        <Button
          variant="secondary"
          disabled={!etag || cancel.isPending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          {cancel.isPending ? 'Cancelling…' : 'Cancel research'}
        </Button>
      </div>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel this research run?</AlertDialogTitle>
            <AlertDialogDescription>
              {forPipeline
                ? 'It stops after the step it is on, and the pipeline waiting for it stops too: the article will need you. No brief comes out of it.'
                : 'It stops after the step it is on. No brief comes out of it; you can start a new run any time.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep researching</AlertDialogCancel>
            <Button variant="danger" disabled={cancel.isPending} onClick={cancelRun}>
              Cancel research
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

/** The newest run ended without a brief: what stopped it, and a new run with the same settings. */
export function RunProblem({ run, onRetry }: { run: ResearchRun; onRetry: () => void }) {
  const cancelled = run.status === 'cancelled';
  const ended = formatDateTime(run.finishedAt ?? run.statusChangedAt);
  return (
    <Alert variant={cancelled ? 'info' : 'error'} role={cancelled ? undefined : 'alert'}>
      <AlertTitle>{cancelled ? 'The last research run was cancelled.' : 'The last research run stopped.'}</AlertTitle>
      <AlertDescription>
        <p>{runErrorSentence(run)}</p>
        <p className="text-xs">
          {`Topic: ${run.topic}`}
          {ended ? `. Ended ${ended}.` : '.'}
        </p>
      </AlertDescription>
      <AlertActions>
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <RotateCw aria-hidden="true" />
          Run it again
        </Button>
      </AlertActions>
    </Alert>
  );
}
