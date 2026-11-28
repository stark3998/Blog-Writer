import { CircleCheck, CircleMinus, CirclePause, CirclePlay } from 'lucide-react';
import { useId } from 'react';
import { Link } from 'react-router';
import { toast } from 'sonner';

import { Field } from '@/components/ui/field';
import { Switch } from '@/components/ui/switch';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isEtagMismatch } from '@/lib/api/envelope';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { cn } from '@/lib/cn';

import { BlockLoading, SubHeading } from '../components/settings-block';
import { StateBadge } from '../components/status-badge';
import { useNow } from '@/lib/use-now';
import { formatDate, formatDateTime, formatPercent, formatRelative } from '../format';
import { settingsKeys, type Schemas } from '../queries';

type AutonomyView = Schemas['AutonomyView'];
type CalibrationOutcome = Schemas['CalibrationOutcome'];

const OUTCOME_LABELS: Readonly<Record<CalibrationOutcome, string>> = {
  clean: 'Clean',
  edited: 'Edited',
  gates_failed: 'Gates failed',
  review_only: 'Held for review',
  correction: 'Corrected after publishing',
  vetoed: 'Vetoed',
};

/** Why auto-publish can't run, in the owner's words (`auto_publish_decision` reasons). */
export function decisionReason(code: string, view: AutonomyView): string {
  const calibration = view.settings.item.calibration;
  switch (code) {
    case 'paused':
      return 'Autonomy is paused.';
    case 'not_enabled':
      return 'The switch above is off.';
    case 'calibrating': {
      const needed = Math.max((calibration?.required ?? 6) - (calibration?.cleanStreak ?? 0), 0);
      return `${String(needed)} more clean ${needed === 1 ? 'review' : 'reviews'} in a row needed.`;
    }
    case 'judge_uncalibrated':
      return 'The judge hasn’t been checked against your labels yet: label the evaluation set in System.';
    case 'judge_below_threshold':
      return 'The judge agrees with your labels too rarely.';
    case 'judge_stale':
      return 'The judge’s check is more than a month old: label a fresh evaluation run in System.';
    default:
      return `Not allowed (${code}).`;
  }
}

/** The pause switch (phone-first): one tap stops every scheduled AI job and auto-publish. */
export function PausePanel() {
  const now = useNow();
  const autonomy = useApiQuery({ queryKey: settingsKeys.autonomy, path: '/api/kb/autonomy' });
  const options = { updates: () => settingsKeys.autonomy, invalidates: [queryKeys.today] } as const;
  const pause = useApiMutation('post', '/api/kb/autonomy/pause', {
    ...options,
    successToast: 'Autonomy paused. Nothing runs on its own until you resume.',
    errorToast: 'Couldn’t pause autonomy.',
  });
  const resume = useApiMutation('post', '/api/kb/autonomy/resume', {
    ...options,
    successToast: 'Autonomy resumed.',
    errorToast: 'Couldn’t resume autonomy.',
  });

  if (autonomy.isPending) return <BlockLoading label="Loading the pause switch" lines={1} />;
  if (autonomy.isError) {
    return (
      <ProblemAlert
        error={autonomy.error}
        title="Couldn’t load the pause switch."
        onRetry={() => void autonomy.refetch()}
        retryLabel="Try loading the pause switch again"
      />
    );
  }
  const settings = autonomy.data.settings.item;
  const busy = pause.isPending || resume.isPending;
  const paused = settings.paused;
  return (
    <div
      className={cn(
        'max-w-measure rounded-lg border p-4',
        paused ? 'border-transparent bg-warn-soft' : 'border-line bg-sheet',
      )}
    >
      <Field
        orientation="horizontal"
        label="Pause all autonomy"
        description={
          paused
            ? `Paused${settings.pausedAt ? ` ${formatRelative(settings.pausedAt, now)}` : ''}. Scheduled AI work and auto-publish wait. Anything you start yourself still runs.`
            : 'Stops feed triage, the weekly plan, the digest, suggestions and auto-publish until you switch it back. Anything you start yourself still runs.'
        }
      >
        <Switch
          checked={paused}
          disabled={busy}
          onCheckedChange={(checked) => {
            if (checked) pause.mutate();
            else resume.mutate();
          }}
        />
      </Field>
      <div className="mt-3 flex items-center gap-2 pl-13">
        <StateBadge
          look={
            paused
              ? { label: 'Paused', tone: 'warn', icon: CirclePause }
              : { label: 'Running', tone: 'success', icon: CirclePlay }
          }
        />
      </div>
    </div>
  );
}

/** The streak toward auto-publish as a tally: one mark per clean review needed. */
function StreakTally({ streak, required }: { streak: number; required: number }) {
  const marks = Math.min(Math.max(required, 1), 50);
  return (
    <div
      role="meter"
      aria-label="Clean reviews in a row"
      aria-valuemin={0}
      aria-valuemax={marks}
      aria-valuenow={Math.min(streak, marks)}
      aria-valuetext={`${String(Math.min(streak, marks))} of ${String(marks)} clean reviews in a row`}
      className="flex max-w-80 gap-1"
    >
      {Array.from({ length: marks }, (_, index) => (
        <span
          key={index}
          aria-hidden="true"
          className={cn('h-3 flex-1 rounded-sm', index < streak ? 'bg-ink' : 'bg-wash-strong')}
        />
      ))}
    </div>
  );
}

/**
 * Auto-publish for articles: the owner's switch, the calibration streak, the judge's agreement
 * with the owner's labels, and today's decision with its reasons. LinkedIn posts always need a tap.
 */
export function AutoPublishPanel() {
  const now = useNow();
  const headingId = useId();
  const autonomy = useApiQuery({ queryKey: settingsKeys.autonomy, path: '/api/kb/autonomy' });
  const toggle = useApiMutation('put', '/api/kb/autonomy', {
    updates: () => settingsKeys.autonomy,
    handles: ['etag_mismatch'],
    successToast: (data) =>
      data.settings.item.autoPublishEnabled
        ? 'Auto-publish is allowed once every check passes.'
        : 'Auto-publish is off. Every article waits for your approval.',
    errorToast: 'Couldn’t change auto-publish.',
  });

  if (autonomy.isPending) return <BlockLoading label="Loading auto-publish" />;
  if (autonomy.isError) {
    return (
      <ProblemAlert
        error={autonomy.error}
        title="Couldn’t load auto-publish."
        onRetry={() => void autonomy.refetch()}
        retryLabel="Try loading auto-publish again"
      />
    );
  }
  const view = autonomy.data;
  const { item, etag } = view.settings;
  const calibration = item.calibration ?? { cleanStreak: 0, required: 6, events: [] };
  const judge = item.judge;
  const events = [...(calibration.events ?? [])].reverse().slice(0, 6);

  return (
    <div className="grid max-w-measure gap-8">
      <Field
        orientation="horizontal"
        label="Allow auto-publish for articles"
        description="An article goes live without your approval only when this is on, the streak below is complete, the judge is calibrated and every gate passes on its own. You still get the veto notice first."
      >
        <Switch
          checked={item.autoPublishEnabled}
          disabled={toggle.isPending}
          onCheckedChange={(checked) => {
            toggle.mutate(
              { body: { autoPublishEnabled: checked }, ifMatch: etag },
              {
                onError: (error) => {
                  if (!isEtagMismatch(error)) return;
                  void autonomy.refetch();
                  toast.error('Auto-publish changed elsewhere.', {
                    description: 'The latest setting is loaded. Switch it again if you still want to.',
                  });
                },
              },
            );
          }}
        />
      </Field>

      <div className="grid gap-3">
        <SubHeading>Today’s decision</SubHeading>
        {view.decision.allowed ? (
          <StateBadge look={{ label: 'Auto-publish can run', tone: 'success', icon: CircleCheck }} />
        ) : (
          <>
            <StateBadge look={{ label: 'Auto-publish is held', tone: 'neutral', icon: CircleMinus }} />
            <ul className="list-disc space-y-1 pl-5 text-ink-muted">
              {view.decision.reasons.map((reason) => (
                <li key={reason}>{decisionReason(reason, view)}</li>
              ))}
            </ul>
          </>
        )}
      </div>

      <section aria-labelledby={headingId} className="grid gap-3">
        <SubHeading id={headingId}>Calibration</SubHeading>
        <p className="text-ink-muted">
          {`${String(calibration.cleanStreak)} of ${String(calibration.required)} reviewed articles in a row passed every gate without substantive edits.`}{' '}
          Any edit, failed gate, veto or correction starts the count again.
        </p>
        <StreakTally streak={calibration.cleanStreak} required={calibration.required} />
        {events.length > 0 ? (
          <ol aria-label="Recent reviews" className="mt-2 grid gap-1 text-sm">
            {events.map((event) => (
              <li key={`${event.articleId}-${event.at}`} className="flex flex-wrap justify-between gap-x-4">
                <Link to={`/studio/${event.articleId}/draft`} className="text-ink underline decoration-line-strong underline-offset-4">
                  {OUTCOME_LABELS[event.outcome]}
                </Link>
                <time dateTime={event.at} className="text-ink-subtle">
                  {formatDate(event.at)}
                </time>
              </li>
            ))}
          </ol>
        ) : null}
        {item.lastCorrectionAt ? (
          <p className="text-sm text-ink-muted">
            Last correction {formatDateTime(item.lastCorrectionAt)}: auto-publish switched itself off then.
          </p>
        ) : null}
      </section>

      <div className="grid gap-2">
        <SubHeading>The judge</SubHeading>
        {judge?.asIsRate === null || judge?.asIsRate === undefined || !judge.checkedAt ? (
          <p className="text-ink-muted">
            Not checked yet. Run the evaluation set and label its drafts in{' '}
            <Link to="/settings/system" className="text-ink underline decoration-line-strong underline-offset-4">
              Settings &gt; System
            </Link>
            .
          </p>
        ) : (
          <p className="text-ink-muted">
            {`Of the drafts the judge passed, you labelled ${formatPercent(judge.asIsRate)} “as is” (${String(judge.sampleSize)} labelled, needs ${formatPercent(judge.threshold)}). Checked ${formatRelative(judge.checkedAt, now)}.`}
          </p>
        )}
      </div>
    </div>
  );
}
