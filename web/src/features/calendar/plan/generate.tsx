import { RotateCw, Sparkles } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';

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
import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { useJobError, useJobStatus } from '@/features/jobs/use-job';
import { useAppData } from '@/lib/app-data';
import { isLiveJobStatus } from '@/lib/events/events';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { useJobRecord } from '../data';
import { planIdFor } from '../lib/plan';

/** The plan job's steps (`planner.plan-week`) in the owner's words. */
export const PLAN_STEP_LABELS: Readonly<Record<string, string>> = {
  collect: 'Gather your ideas, stories and recent weeks',
  propose: 'Propose the flagship and the posts',
  slots: 'Fit the posts into free slots',
  save: 'Save the plan',
  notify: 'Tell you it’s ready',
};

interface GenerateButtonProps {
  weekStart: string;
  /** "Plan this week", "Plan it again". */
  label: string;
  /** Ask first: regenerating replaces a proposal and the owner's changes to it. */
  confirm: boolean;
  variant?: 'primary' | 'secondary';
  onStarted: (jobId: string) => void;
}

/** Starts the planner for a week (`POST /api/plan/weeks/{weekStart}/generate`, origin owner). */
export function GenerateButton({ weekStart, label, confirm, variant = 'primary', onStarted }: GenerateButtonProps) {
  const { queryClient } = useAppData();
  const [asking, setAsking] = useState(false);
  const generate = useApiMutation('post', '/api/plan/weeks/{week_start}/generate', {
    errorToast: 'Couldn’t start the planner.',
  });
  const start = () => {
    generate.mutate(
      { params: { path: { week_start: weekStart } } },
      {
        onSuccess: (job) => {
          onStarted(job.jobId);
          void queryClient.invalidateQueries({ queryKey: queryKeys.plans.detail(planIdFor(weekStart)) });
        },
      },
    );
  };
  return (
    <>
      <Button
        variant={variant}
        disabled={generate.isPending}
        onClick={() => {
          if (confirm) setAsking(true);
          else start();
        }}
      >
        {confirm ? <RotateCw aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
        {generate.isPending ? 'Starting…' : label}
      </Button>
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Plan this week again?</AlertDialogTitle>
            <AlertDialogDescription>
              The planner proposes a new week from your ideas and banks. It replaces this proposal, including the
              changes you made to it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep this plan</AlertDialogCancel>
            <AlertDialogAction onClick={start}>Plan it again</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/** The planner's job: live from the event stream, with its record as a fallback. */
export function usePlanJob(jobId: string | undefined) {
  const live = useJobStatus(jobId ?? '');
  const liveKnown = jobId !== undefined && live !== undefined;
  const record = useJobRecord(jobId, jobId !== undefined && isLiveJobStatus(liveKnown ? live : undefined));
  const status = liveKnown ? live : record.data?.status;
  const streamError = useJobError(jobId ?? '');
  const detail = streamError?.detail ?? record.data?.error?.detail ?? undefined;
  return { status, running: jobId !== undefined && isLiveJobStatus(status), detail };
}

interface PlanProgressProps {
  jobId: string;
  weekStart: string;
}

/**
 * The planner at work: its steps as they happen. When it finishes the plan reloads (the job also
 * reports the plan as changed, which refreshes it on its own).
 */
export function PlanProgress({ jobId, weekStart }: PlanProgressProps) {
  const { queryClient } = useAppData();
  const { status } = usePlanJob(jobId);
  const finished = status !== undefined && !isLiveJobStatus(status);
  useEffect(() => {
    if (finished) void queryClient.invalidateQueries({ queryKey: queryKeys.plans.detail(planIdFor(weekStart)) });
  }, [finished, queryClient, weekStart]);
  return (
    <div className="space-y-3">
      <p className="text-ink-muted">The planner is proposing the week. It takes a minute or two; you can leave this page.</p>
      <JobTimeline jobId={jobId} title="Planning the week" labels={PLAN_STEP_LABELS} />
    </div>
  );
}

interface PlanJobFailedProps {
  detail: string | undefined;
  status: string;
  action: ReactNode;
}

/** The planner stopped: what it said, and a way to run it again. */
export function PlanJobFailed({ detail, status, action }: PlanJobFailedProps) {
  return (
    <Alert variant="error">
      <AlertTitle>{status === 'cancelled' ? 'The planner was cancelled' : 'The planner stopped'}</AlertTitle>
      <AlertDescription>
        <p>{detail ?? 'It couldn’t finish the plan. Try again; if it keeps failing, check Settings > System.'}</p>
      </AlertDescription>
      <AlertActions>{action}</AlertActions>
    </Alert>
  );
}
