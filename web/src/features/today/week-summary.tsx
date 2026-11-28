import { Link } from 'react-router';

import { DetailList, type DetailItem } from '@/components/shell/detail-list';

import { Skeleton } from '@/components/ui/skeleton';
import { StatTile, type StatDelta } from '@/components/ui/stat-tile';
import type { components } from '@/lib/api/schema';

import type { Scorecard, TodayWeek } from './lib/items';
import { shortDate } from './lib/time';

type WeekScorecard = components['schemas']['WeekScorecard'];

const MODE_LABELS: Readonly<Record<TodayWeek['mode'], string>> = {
  normal: 'Normal',
  light: 'Light',
  away: 'Away',
};

const MODE_NOTES: Readonly<Record<TodayWeek['mode'], string>> = {
  normal: 'Three posts and the flagship article.',
  light: 'Two posts; the flagship is optional.',
  away: 'Nothing new is written. Approved posts go out through the kit.',
};

/** The owner time budget from the plan: about 2.5 to 3 hours a week. */
const TIME_BUDGET: readonly DetailItem[] = [
  { id: 'sunday', term: 'Sunday', meta: '30 min', detail: 'Approve next week’s plan and answer the micro-interviews.' },
  { id: 'mid-week', term: 'Mid-week', meta: '30 min', detail: 'Review the flagship article and its posts.' },
  { id: 'slots', term: 'Each post slot', meta: 'About 1 min', detail: 'Publish with one tap.' },
  { id: 'daily', term: 'Daily', meta: '15 min', detail: 'Engage on LinkedIn: replies and comments.' },
];

function TimeBudget() {
  return (
    <div className="mt-12 max-w-measure">
      <h3 className="text-base font-semibold text-ink">Where your time goes</h3>
      <p className="mt-1 text-sm text-ink-muted">A default week, from the plan.</p>
      <DetailList items={TIME_BUDGET} className="mt-4" />
      <p className="mt-4 font-semibold text-ink">About 2½ to 3 hours a week in total.</p>
    </div>
  );
}

function planWords(status: TodayWeek['planStatus']): string {
  switch (status) {
    case 'generating':
      return 'Being drafted';
    case 'proposed':
      return 'Waiting for your approval';
    case 'approved':
      return 'Approved';
    case 'closed':
      return 'Closed';
    case null:
    case undefined:
      return 'No plan yet';
  }
}

const percent = (value: number) => `${String(Math.round(value * 100))}%`;
const points = (value: number) => {
  const rounded = Math.round(value * 100);
  return `${rounded > 0 ? '+' : ''}${String(rounded)} pts`;
};

function change(now: number, before: number | null | undefined, format: StatDelta['format'] = 'number'): StatDelta | undefined {
  if (before === null || before === undefined) return undefined;
  return { value: now - before, label: 'vs all of last week', format, good: 'up' };
}

interface WeekSummaryProps {
  week: TodayWeek | undefined;
  /** Complete weeks, newest first (`GET /api/plan/scorecard`); the first is last week. */
  history: readonly WeekScorecard[] | undefined;
  /** Today could not be loaded (the inbox shows the error): leave out what depends on it. */
  failed?: boolean;
}

/**
 * This week at a glance: its mode and plan, and the quality scorecard so far (posts with the
 * owner's signal, replies, substantive comments, pillar balance), compared with all of last week.
 * It replaces daily streaks (plan, "Tracking").
 */
export function WeekSummary({ week, history, failed = false }: WeekSummaryProps) {
  const score: Scorecard | null | undefined = week?.scorecard;
  const last = history?.[0]?.scorecard;

  return (
    <section aria-labelledby="your-week" aria-busy={!week && !failed} className="mt-16 md:mt-20">
      <div className="flex max-w-measure flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h2 id="your-week" className="font-serif text-2xl font-medium text-ink">
          Your week
        </h2>
        <Link
          to="/calendar/plan"
          className="inline-flex min-h-11 items-center text-sm text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink md:min-h-0"
        >
          Open the plan
        </Link>
      </div>

      {week ? (
        <dl className="mt-3 grid max-w-measure gap-x-8 gap-y-2 text-sm sm:flex sm:flex-wrap">
          <div className="flex gap-2">
            <dt className="text-ink-subtle">Week of</dt>
            <dd className="font-medium text-ink">{shortDate(week.weekStart)}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-ink-subtle">Mode</dt>
            <dd className="font-medium text-ink">
              {MODE_LABELS[week.mode]}
              <span className="font-normal text-ink-muted">. {MODE_NOTES[week.mode]}</span>
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-ink-subtle">Plan</dt>
            <dd className="font-medium text-ink">{planWords(week.planStatus)}</dd>
          </div>
        </dl>
      ) : null}

      {!week && !failed ? (
        <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <span className="sr-only">Loading the week</span>
          {[0, 1, 2, 3, 4].map((index) => (
            <Skeleton key={index} className="h-28" />
          ))}
        </div>
      ) : null}

      {week ? (
        <div className="mt-8">
          <h3 className="text-base font-semibold text-ink">The scorecard so far</h3>
          {score ? (
            <>
              <p className="mt-1 max-w-measure text-sm text-ink-muted">
                It counts what carries your own signal, not how often you showed up.
              </p>
              <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
                <StatTile
                  label="Posts published"
                  value={score.postsPublished}
                  delta={change(score.postsPublished, last?.postsPublished)}
                />
                <StatTile
                  label="With your signal"
                  value={score.postsWithOwnerSignal}
                  caption={`Of ${String(score.postsPublished)} ${score.postsPublished === 1 ? 'post' : 'posts'}: a story, a position or your take`}
                />
                <StatTile label="Replies" value={score.replies} delta={change(score.replies, last?.replies)} />
                <StatTile
                  label="Substantive comments"
                  value={score.substantiveComments}
                  delta={change(score.substantiveComments, last?.substantiveComments)}
                />
                <StatTile
                  label="On a pillar"
                  value={score.onPillarShare ?? null}
                  format={percent}
                  delta={
                    score.onPillarShare === null || score.onPillarShare === undefined
                      ? undefined
                      : change(score.onPillarShare, last?.onPillarShare, points)
                  }
                  caption="The plan aims for 80% or more"
                  className="col-span-2 lg:col-span-1"
                />
              </div>
            </>
          ) : (
            <p className="mt-1 max-w-measure text-sm text-ink-muted">
              No scorecard yet. It fills in as posts go out and you reply.
            </p>
          )}
        </div>
      ) : null}

      <TimeBudget />
    </section>
  );
}
