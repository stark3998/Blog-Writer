import { CircleCheck } from 'lucide-react';
import { useId, useState } from 'react';
import { Link } from 'react-router';

import { DetailList, type DetailItem } from '@/components/shell/detail-list';
import { EmptyState } from '@/components/shell/empty-state';
import { Fieldset } from '@/components/ui/field';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Skeleton } from '@/components/ui/skeleton';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { components } from '@/lib/api/schema';
import { toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { useNow } from '@/lib/use-now';

import { SettingsNotice } from './components/settings-notice';
import { StatusBadge } from './components/status-badge';
import { useCalendarSettings, usePlansFrom, type CalendarSettings } from './data';
import { MODE_LABEL, modeSummary, PLAN_STATUS } from './lib/labels';
import { planIdFor, planJobMs } from './lib/plan';
import type { CadenceView, WeekMode } from './lib/settings';
import { addDays, currentWeekStart, momentLabel, shortDate, weekRangeLabel } from './lib/time';

type PlanEntry = components['schemas']['Versioned_WeekPlan_'];

const WEEKS_SHOWN = 6;
const MODES: readonly WeekMode[] = ['normal', 'light', 'away'];

function modeItems(cadence: CadenceView): DetailItem[] {
  return [
    { id: 'normal', term: 'Normal', detail: `${modeSummary('normal', cadence.postsPerWeek)}. The default.` },
    { id: 'light', term: 'Light', detail: `${modeSummary('light', cadence.postsPerWeek)}.` },
    {
      id: 'away',
      term: 'Away',
      detail:
        'Nothing new is generated. Posts you already approved can go out through the ready-to-post kit, scheduled natively in LinkedIn.',
    },
  ];
}

/**
 * Calendar > Availability (`/calendar/availability`): the mode of this week and the next five
 * (normal, light, away), phone-first. A week without a plan gets one that holds only its mode
 * (`PUT /api/plan/weeks/{weekStart}/mode` with `If-None-Match: *`); the planner fills it in on
 * Sunday. An existing plan changes with If-Match, and a stale one opens the conflict dialog.
 */
export function AvailabilityView() {
  const settings = useCalendarSettings();
  if (settings.pending) {
    return (
      <div className="space-y-4">
        <p role="status" className="sr-only">
          Loading your availability
        </p>
        <Skeleton className="h-8 w-[min(24rem,80%)]" />
        <Skeleton className="h-40 w-full max-w-measure" />
      </div>
    );
  }
  return <AvailabilityReady settings={settings} />;
}

function AvailabilityReady({ settings }: { settings: CalendarSettings }) {
  const { cadence } = settings;
  const now = useNow();
  const zone = cadence.timezone;
  const thisWeek = currentWeekStart(now, zone);
  const plans = usePlansFrom(thisWeek, 10);
  const headingId = useId();
  const weeks = Array.from({ length: WEEKS_SHOWN }, (_, index) => addDays(thisWeek, 7 * index));
  const byWeek = new Map((plans.data ?? []).map((entry) => [entry.item.weekStart, entry]));

  return (
    <div className="space-y-12">
      <SettingsNotice settings={settings} />
      <EmptyState
        title="Set how much you can give each week."
        yourPart="Switch the mode before Sunday’s plan whenever your week changes."
      >
        <p>Three modes. The planner follows the one you choose for each week.</p>
        <DetailList items={modeItems(cadence)} className="mt-6" />
      </EmptyState>

      <section aria-labelledby={headingId} className="max-w-measure">
        <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
          The next six weeks
        </h2>
        {plans.isPending ? (
          <div className="mt-6 space-y-6">
            <p role="status" className="sr-only">
              Loading the weeks
            </p>
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-24 w-full" />
            ))}
          </div>
        ) : plans.isError ? (
          <ProblemAlert
            className="mt-6"
            error={plans.error}
            title="Couldn’t load your weeks."
            onRetry={() => void plans.refetch()}
            retryLabel="Try loading the weeks again"
          />
        ) : (
          <ol className="mt-6 border-t border-line">
            {weeks.map((week) => (
              <WeekModeRow
                key={week}
                weekStart={week}
                entry={byWeek.get(week)}
                cadence={cadence}
                label={week === thisWeek ? 'This week' : week === addDays(thisWeek, 7) ? 'Next week' : undefined}
                now={now}
              />
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

interface WeekModeRowProps {
  weekStart: string;
  entry: PlanEntry | undefined;
  cadence: CadenceView;
  label: string | undefined;
  now: number;
}

function WeekModeRow({ weekStart, entry, cadence, label, now }: WeekModeRowProps) {
  const { queryClient } = useAppData();
  const conflict = useConflict();
  const [attempt, setAttempt] = useState<WeekMode | null>(null);
  const [saved, setSaved] = useState(false);
  /** The mode a 412 refused, for the conflict dialog's "Keep mine". */
  const [retryMode, setRetryMode] = useState<WeekMode | null>(null);
  const plan = entry?.item;
  const zone = plan?.timezone ?? cadence.timezone;
  const saving = attempt !== null;
  const mode = attempt ?? plan?.mode ?? cadence.defaultMode;
  const closed = plan?.status === 'closed';
  const save = useApiMutation('put', '/api/plan/weeks/{week_start}/mode', {
    updates: (data) => queryKeys.plans.detail(data.item.id),
    invalidates: [queryKeys.plans.lists(), queryKeys.today],
    errorToast: 'Couldn’t change the mode.',
    handles: ['etag_mismatch'],
  });
  const title = label ?? `Week of ${shortDate(weekStart)}`;

  const send = (value: WeekMode, ifMatch: string | null) => {
    setAttempt(value);
    setSaved(false);
    save.mutate(
      { params: { path: { week_start: weekStart } }, body: { mode: value }, ifMatch },
      {
        onSuccess: () => {
          setAttempt(null);
          setSaved(true);
          conflict.close();
          toastSuccess(`${title}: ${MODE_LABEL[value]}.`);
        },
        onError: (error) => {
          setAttempt(null);
          if (conflict.show(error)) {
            setRetryMode(value);
            // Load the week as it is now, for the comparison.
            void queryClient.invalidateQueries({ queryKey: queryKeys.plans.lists() });
          }
        },
      },
    );
  };

  const scheduled = planJobMs(weekStart, cadence, zone);
  let status: string;
  if (!plan || (plan.status === 'generating' && !plan.jobId)) {
    status = scheduled > now ? `Not planned yet. The planner proposes it ${momentLabel(scheduled, zone)}.` : 'Not planned yet.';
  } else {
    status = PLAN_STATUS[plan.status].label;
  }

  return (
    <li className="space-y-3 border-b border-line py-5">
      <Fieldset
        legend={
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-base font-semibold text-ink">{title}</span>
            <span className="font-normal text-ink-muted">{weekRangeLabel(weekStart)}</span>
          </span>
        }
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {plan && plan.status !== 'generating' ? (
              <StatusBadge status={PLAN_STATUS[plan.status]} />
            ) : (
              <span>{status}</span>
            )}
            {plan && (plan.status === 'proposed' || plan.status === 'approved') ? (
              <Link
                to={`/calendar/plan?week=${weekStart}`}
                className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
              >
                {plan.status === 'proposed' ? 'Review the plan' : 'Open the plan'}
              </Link>
            ) : null}
          </span>
        }
      >
        {(group) => (
          <SegmentedControl
            {...group}
            value={mode}
            disabled={closed || saving}
            onValueChange={(value) => {
              const next = MODES.find((candidate) => candidate === value);
              if (!next || next === mode) return;
              send(next, entry ? entry.etag : null);
            }}
            fullWidth
            className="sm:w-auto sm:min-w-80"
          >
            {MODES.map((value) => (
              <SegmentedControlItem key={value} value={value}>
                {MODE_LABEL[value]}
              </SegmentedControlItem>
            ))}
          </SegmentedControl>
        )}
      </Fieldset>
      <p className="flex items-center gap-1.5 text-sm text-ink-muted">
        {saved && !saving ? <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-success" /> : null}
        {closed ? 'This week is closed.' : saving ? 'Saving…' : `${modeSummary(mode, cadence.postsPerWeek)}.`}
      </p>
      {plan?.status === 'approved' && !closed ? (
        <p className="text-sm text-ink-muted">
          {mode === 'away'
            ? 'The plan is already approved: its posts stay. If you won’t be there to tap Publish, switch them to the ready-to-post kit on their Go-live pages.'
            : 'The plan is already approved: changing the mode updates its checks, and the posts it holds stay.'}
        </p>
      ) : null}
      <ConflictDialog
        {...conflict}
        subject={`${title}’s mode`}
        mine={retryMode ? MODE_LABEL[retryMode] : undefined}
        theirs={retryMode ? MODE_LABEL[plan?.mode ?? cadence.defaultMode] : undefined}
        saving={saving}
        onKeepMine={() => {
          const etag = conflict.conflict?.etag;
          if (retryMode && etag) send(retryMode, etag);
          else void queryClient.invalidateQueries({ queryKey: queryKeys.plans.lists() });
        }}
        onTakeTheirs={() => {
          setRetryMode(null);
          conflict.close();
          void queryClient.invalidateQueries({ queryKey: queryKeys.plans.lists() });
          void queryClient.invalidateQueries({ queryKey: queryKeys.plans.detail(planIdFor(weekStart)) });
        }}
      />
    </li>
  );
}
