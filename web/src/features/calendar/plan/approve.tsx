import { CircleCheck } from 'lucide-react';
import { useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { describeConflict } from '@/features/data/conflict-dialog';
import { conflictOf, type ConflictInfo } from '@/lib/api/envelope';
import { ApiError } from '@/lib/api/errors';
import { isProblem } from '@/lib/api/problems';
import { toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { cn } from '@/lib/cn';

import { countLabel } from '../lib/labels';
import { blockingFindings, problemFindings, type Finding, type WeekPlan } from '../lib/plan';
import type { CadenceView } from '../lib/settings';
import { addDays, localToUtcMs, momentLabel, parseTimestamp, WEEKDAY_IDS } from '../lib/time';

import { FindingList } from './findings';

interface ApproveSectionProps {
  plan: WeekPlan;
  etag: string | null;
  cadence: CadenceView;
  /** False while a move or another edit is saving. */
  ready: boolean;
  /** The plan machine allows `proposed -> approved` (`/api/meta/machines`). */
  canApprove: boolean;
}

interface Outcome {
  readonly research?: string;
  readonly posts: number;
  readonly takes: number;
  readonly away: boolean;
}

function researchLabel(plan: WeekPlan, cadence: CadenceView): string {
  const date = addDays(plan.weekStart, WEEKDAY_IDS.indexOf(cadence.researchStart.day));
  return momentLabel(localToUtcMs(date, cadence.researchStart.time, plan.timezone), plan.timezone);
}

function outcomeText(outcome: Outcome): string {
  if (outcome.away) return 'Plan approved. It’s an away week, so nothing new is generated.';
  const parts = ['Plan approved.'];
  if (outcome.research) parts.push(`Research for the flagship starts ${outcome.research}.`);
  if (outcome.posts > 0) parts.push(`Writing ${countLabel(outcome.posts, 'post')} now.`);
  if (outcome.takes > 0) parts.push(`${countLabel(outcome.takes, 'quick take')} ${outcome.takes === 1 ? 'waits' : 'wait'} for your take.`);
  return parts.join(' ');
}

/**
 * Approving the week (`POST /api/plan/weeks/{weekStart}/approve`, If-Match): the one action of
 * the Sunday review. On phones it sits in a bar above the tab bar, within thumb reach. The
 * server checks the spacing and mix rules again: a 409 lists what to fix, a 412 says the plan
 * changed since it was opened (nothing is approved unseen).
 */
export function ApproveSection({ plan, etag, cadence, ready, canApprove }: ApproveSectionProps) {
  const { queryClient } = useAppData();
  const [refused, setRefused] = useState<{ findings: Finding[]; message: string } | null>(null);
  const [stale, setStale] = useState<ConflictInfo | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const planKey = queryKeys.plans.detail(plan.id);
  const approve = useApiMutation('post', '/api/plan/weeks/{week_start}/approve', {
    invalidates: [queryKeys.posts.lists(), queryKeys.articles.lists(), queryKeys.plans.lists(), queryKeys.today],
    errorToast: 'Couldn’t approve the plan.',
    handles: ['etag_mismatch', 'slot_conflict', 'mix_rules'],
    onSuccess: (data) => {
      queryClient.setQueryData(planKey, data.plan);
    },
  });

  const blocking = blockingFindings(plan.warnings);
  const takes = (plan.slots ?? []).filter((slot) => slot.lane === 'quick_take' && !slot.postId).length;

  const onApprove = () => {
    if (etag === null) return;
    setRefused(null);
    setStale(null);
    approve.mutate(
      { params: { path: { week_start: plan.weekStart } }, ifMatch: etag },
      {
        onSuccess: (data) => {
          const posts = (data.postIds ?? []).length;
          const result: Outcome = {
            research: data.articleId ? researchLabel(plan, cadence) : undefined,
            posts,
            takes,
            away: plan.mode === 'away',
          };
          setOutcome(result);
          toastSuccess(outcomeText(result));
        },
        onError: (error) => {
          if (isProblem(error, ['slot_conflict', 'mix_rules'])) {
            const extensions = error instanceof ApiError ? error.problem?.extensions : undefined;
            const findings = problemFindings(extensions);
            setRefused({
              findings: findings.length > 0 ? findings : blocking,
              message: error instanceof ApiError && error.problem?.detail ? error.problem.detail : 'Fix these first.',
            });
            void queryClient.invalidateQueries({ queryKey: planKey });
          } else if (conflictOf(error)) {
            setStale(conflictOf(error) ?? {});
            void queryClient.invalidateQueries({ queryKey: planKey });
          }
        },
      },
    );
  };

  const approvedAt = parseTimestamp(plan.approvedAt);

  // A fragment, not a wrapper: on phones the bar must be a child of the tall plan container to
  // stick above the tab bar while the week scrolls under it.
  return (
    <>
      {outcome ? (
        // The toast reads it out; this keeps it on the page.
        <Alert variant="success">
          <AlertDescription>
            <p className="text-ink">{outcomeText(outcome)}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {refused ? (
        <Alert variant="error">
          <AlertTitle>The plan isn’t approved yet</AlertTitle>
          <AlertDescription>
            <p>{refused.message}</p>
            <FindingList findings={refused.findings} plan={plan} />
          </AlertDescription>
        </Alert>
      ) : null}

      {stale ? (
        <Alert variant="warn" role="alert">
          <AlertTitle>The plan changed while you had it open</AlertTitle>
          <AlertDescription>
            <p>{`${describeConflict(stale)} Check it again, then approve.`}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {canApprove ? (
        <div
          className={cn(
            'sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 -mx-5 border-t border-line bg-sheet px-5 py-3',
            'sm:-mx-8 sm:px-8 md:static md:mx-0 md:rounded-lg md:border md:px-4',
          )}
        >
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 text-sm">
              {blocking.length > 0 ? (
                <>
                  <p className="font-medium text-danger">{`${countLabel(blocking.length, 'issue')} ${blocking.length === 1 ? 'blocks' : 'block'} approval`}</p>
                  <FindingList findings={blocking} plan={plan} className="mt-1" />
                </>
              ) : (
                <p className="text-ink">
                  {plan.mode === 'away'
                    ? 'Away week: approving it generates nothing.'
                    : 'Approving starts the flagship’s research and writes the native posts. Quick takes wait for your take.'}
                </p>
              )}
            </div>
            <Button className="w-full shrink-0 sm:w-auto" size="lg" disabled={!ready || approve.isPending} onClick={onApprove}>
              {approve.isPending ? 'Approving…' : 'Approve plan'}
            </Button>
          </div>
        </div>
      ) : null}

      {plan.status === 'approved' && !outcome ? (
        <p className="flex items-center gap-2 text-sm text-ink-muted">
          <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-success" />
          {approvedAt !== undefined ? `Approved ${momentLabel(approvedAt, plan.timezone)}.` : 'Approved.'}
          {' Moving a post keeps it approved; changing what a slot holds sends the plan back to you.'}
        </p>
      ) : null}
    </>
  );
}
