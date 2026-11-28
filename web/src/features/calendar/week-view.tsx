import { ChevronDown, Plus } from 'lucide-react';
import { useId, useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { Timeline, type TimelineStep } from '@/components/shell/detail-list';
import { EmptyState } from '@/components/shell/empty-state';
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
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import { StatTile } from '@/components/ui/stat-tile';
import { ConflictDialog } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useLiveEvents } from '@/features/jobs/use-job';
import { ApiError } from '@/lib/api/errors';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';
import { useNow } from '@/lib/use-now';

import { SettingsNotice } from './components/settings-notice';
import { StatusBadge } from './components/status-badge';
import {
  useCalendarSettings,
  usePlanArticles,
  useTransitions,
  useWeekPlan,
  useWeekPosts,
  type CalendarSettings,
} from './data';
import { countLabel, MODE_LABEL, modeSummary, PLAN_STATUS, percentLabel } from './lib/labels';
import {
  dayOptions,
  flagshipMilestones,
  GONE_STATUSES,
  isPastWeek,
  newSlotId,
  outsidePosts,
  planJobMs,
  previewPlan,
  slotInputs,
  withAddedSlot,
  withEditedSlot,
  withMovedSlot,
  withoutSlot,
  type MoveContext,
  type PlannedSlot,
  type WeekPlan,
} from './lib/plan';
import type { CadenceView } from './lib/settings';
import {
  addDays,
  currentWeekStart,
  isMonday,
  localDateOf,
  momentLabel,
  slotLabel,
  weekDates,
  weekRangeLabel,
  WEEKDAY_IDS,
} from './lib/time';
import { ApproveSection } from './plan/approve';
import { PillarBalanceSection } from './plan/balance';
import { AdvisorySection } from './plan/findings';
import { FlagshipSection, type FlagshipInput } from './plan/flagship';
import { GenerateButton, PlanJobFailed, PlanProgress, usePlanJob } from './plan/generate';
import { MissedPosts } from './plan/missed';
import type { SlotActions } from './plan/slot-card';
import { SlotDialog } from './plan/slot-dialog';
import { describePlan, slotModels, takeHref, type SlotModel } from './plan/slot-model';
import { usePlanEditor } from './plan/use-plan-editor';
import { WeekBoard, type DayModel } from './plan/week-board';
import { WeekList } from './plan/week-list';
import { WeekPicker } from './plan/week-picker';

/**
 * Calendar > Plan (`/calendar/plan`, `?week=<Monday>`): the production calendar for this week
 * and next week, in the plan's zone with DST-correct labels. The planned slots by day with their
 * lanes, pillars and post states, the flagship's week from research to publication, the pillar
 * balance with the 80% on-pillar rule, the planner's warnings, missed-slot offers, and approval.
 * Slots move by drag and drop on desktop, and by each slot's "Move to" menu (keyboard) or sheet
 * (phones); a day another post holds is a hard block, under 24 hours apart is a warning.
 */
export function WeekView() {
  const settings = useCalendarSettings();
  if (settings.pending) return <PlanSkeleton />;
  return <WeekViewReady settings={settings} />;
}

function PlanSkeleton() {
  return (
    <div className="space-y-6">
      <p role="status" className="sr-only">
        Loading the plan
      </p>
      <Skeleton className="h-11 w-full max-w-96" />
      <Skeleton className="h-8 w-[min(24rem,80%)]" />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-7">
        {Array.from({ length: 7 }, (_, index) => (
          <Skeleton key={index} className="h-24 md:h-56" />
        ))}
      </div>
    </div>
  );
}

function WeekViewReady({ settings }: { settings: CalendarSettings }) {
  const now = useNow();
  const zone = settings.cadence.timezone;
  const [params, setParams] = useSearchParams();
  const thisWeek = currentWeekStart(now, zone);
  const nextWeek = addDays(thisWeek, 7);
  const thisPlan = useWeekPlan(thisWeek);
  const nextPlan = useWeekPlan(nextWeek);
  const requested = params.get('week');
  const linked = requested !== null && isMonday(requested) ? requested : undefined;

  const statusOf = (week: string) =>
    week === thisWeek ? thisPlan.data?.item.status : week === nextWeek ? nextPlan.data?.item.status : undefined;

  // Next week while it waits for approval (Sunday's review), or while this week has no plan (the
  // planner plans the week ahead); otherwise this week. Decided once: a refetch (which puts a
  // missing plan back to pending) must not swap the week, or unmount the panel that caused it.
  const [defaultWeek, setDefaultWeek] = useState<string | undefined>();
  const settled = !thisPlan.isPending && !nextPlan.isPending;
  if (defaultWeek === undefined && settled) {
    setDefaultWeek(nextPlan.data?.item.status === 'proposed' || !thisPlan.data ? nextWeek : thisWeek);
  }
  const selected = linked ?? defaultWeek;
  if (selected === undefined) return <PlanSkeleton />;

  return (
    <div className="space-y-8">
      <WeekPicker
        thisWeek={thisWeek}
        nextWeek={nextWeek}
        selected={selected}
        statusOf={statusOf}
        zone={zone}
        onSelect={(week) => {
          setParams({ week }, { replace: true, preventScrollReset: true });
        }}
      />
      <SettingsNotice settings={settings} />
      <WeekPlanPanel key={selected} weekStart={selected} thisWeek={thisWeek} settings={settings} now={now} />
    </div>
  );
}

interface PanelProps {
  weekStart: string;
  thisWeek: string;
  settings: CalendarSettings;
  now: number;
}

function WeekPlanPanel({ weekStart, thisWeek, settings, now }: PanelProps) {
  const headingId = useId();
  const planQuery = useWeekPlan(weekStart);
  const [startedJobId, setStartedJobId] = useState<string | undefined>();
  const plan = planQuery.data?.item;
  const notFound = planQuery.error instanceof ApiError && planQuery.error.status === 404;
  const planJobId = plan?.status === 'generating' ? (plan.jobId ?? undefined) : undefined;
  const jobId = startedJobId ?? planJobId;
  const job = usePlanJob(jobId);
  const zone = plan?.timezone ?? settings.cadence.timezone;
  const past = isPastWeek(weekStart, now, zone);
  const weekName = weekStart === thisWeek ? 'this week' : weekStart === addDays(thisWeek, 7) ? 'next week' : 'that week';

  let body;
  if (planQuery.isPending) {
    body = <PlanSkeleton />;
  } else if (planQuery.isError && !notFound) {
    body = (
      <ProblemAlert
        error={planQuery.error}
        title="Couldn’t load the plan."
        onRetry={() => void planQuery.refetch()}
        retryLabel="Try loading the plan again"
      />
    );
  } else if (job.running && jobId) {
    body = <PlanProgress jobId={jobId} weekStart={weekStart} />;
  } else if (!plan || plan.status === 'generating') {
    body = (
      <div className="space-y-8">
        {startedJobId && job.status && !job.running && job.status !== 'succeeded' ? (
          <PlanJobFailed
            status={job.status}
            detail={job.detail}
            action={<GenerateButton weekStart={weekStart} label="Try again" confirm={false} onStarted={setStartedJobId} />}
          />
        ) : null}
        <NoPlan
          weekStart={weekStart}
          weekName={weekName}
          mode={plan?.mode ?? settings.cadence.defaultMode}
          cadence={settings.cadence}
          zone={zone}
          now={now}
          past={past}
          onStarted={setStartedJobId}
        />
      </div>
    );
  } else {
    body = (
      <PlanBody
        plan={plan}
        etag={planQuery.data?.etag ?? null}
        settings={settings}
        now={now}
        past={past}
        onRegenerate={setStartedJobId}
      />
    );
  }

  // Without a plan the empty state's own heading names the week.
  if (!job.running && !planQuery.isPending && (notFound || plan?.status === 'generating')) return body;
  return (
    <section aria-labelledby={headingId} className="space-y-6">
      <h2 id={headingId} className="font-serif text-2xl font-medium text-ink md:text-3xl">
        {`Week of ${weekRangeLabel(weekStart)}`}
      </h2>
      {body}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------
// No plan yet
// ------------------------------------------------------------------------------------------------

function rhythm(cadence: CadenceView): TimelineStep[] {
  const day = (id: string) =>
    ({ mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday' })[
      id
    ] ?? id;
  const slots = cadence.postSlots.filter((slot) => slot.kind === 'primary');
  return [
    { when: `${day(cadence.planWeek.day)} ${cadence.planWeek.time}`, what: 'The planner proposes next week, and you approve it.' },
    { when: `${day(cadence.researchStart.day)} ${cadence.researchStart.time}`, what: 'Research for the flagship starts in the background.' },
    { when: `${day(cadence.interviewDue.day)} ${cadence.interviewDue.time}`, what: 'Your interview answers are due.' },
    { when: `${day(cadence.draftDay.day)} ${cadence.draftDay.time}`, what: 'The draft is written, critiqued and revised.' },
    {
      when: `${day(cadence.vetoNotice.day)} ${cadence.vetoNotice.time}`,
      what: `Veto notice for the article, at least ${String(cadence.minVetoLeadHours)} hours before it goes out.`,
    },
    { when: `Next ${day(cadence.articlePublish.day)} ${cadence.articlePublish.time}`, what: 'The article publishes.' },
    {
      when: slots.map((slot) => `${day(slot.day).slice(0, 3)} ${slot.time}`).join(', ') || 'Post slots',
      what: 'Post slots, one tap each. Never two posts on one day.',
    },
  ];
}

interface NoPlanProps {
  weekStart: string;
  weekName: string;
  mode: WeekPlan['mode'];
  cadence: CadenceView;
  zone: string;
  now: number;
  past: boolean;
  onStarted: (jobId: string) => void;
}

function NoPlan({ weekStart, weekName, mode, cadence, zone, now, past, onStarted }: NoPlanProps) {
  const scheduled = planJobMs(weekStart, cadence, zone);
  const headingId = useId();
  return (
    <div className="space-y-12">
      <EmptyState
        title={`No plan for ${weekName} yet.`}
        yourPart="Approve the plan on Sunday, or move pieces to other slots first. About 30 minutes, including the micro-interviews."
      >
        <p className="font-medium text-ink">{`Week of ${weekRangeLabel(weekStart)}`}</p>
        <p>
          {past
            ? 'This week is over, so the planner won’t plan it any more.'
            : scheduled > now
              ? `The planner proposes it on ${momentLabel(scheduled, zone)}: one flagship article and the week’s posts, balanced across your pillars. Two posts never land on the same day.`
              : 'The planner proposes one flagship article and the week’s posts from your ideas and banks, balanced across your pillars. Two posts never land on the same day.'}
        </p>
        <p>
          {`Mode: ${MODE_LABEL[mode]} (${modeSummary(mode, cadence.postsPerWeek)}). `}
          <Link to="/calendar/availability" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
            Change it in Availability
          </Link>
        </p>
        {!past ? (
          <div className="pt-2">
            <GenerateButton weekStart={weekStart} label="Plan it now" confirm={false} onStarted={onStarted} />
          </div>
        ) : null}
      </EmptyState>
      <section aria-labelledby={headingId} className="max-w-measure">
        <h3 id={headingId} className="font-serif text-2xl font-medium text-ink">
          How a week runs
        </h3>
        <Timeline steps={rhythm(cadence)} className="mt-6" />
      </section>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------
// The plan
// ------------------------------------------------------------------------------------------------

interface PlanBodyProps {
  plan: WeekPlan;
  etag: string | null;
  settings: CalendarSettings;
  now: number;
  past: boolean;
  onRegenerate: (jobId: string) => void;
}

type SlotDialogState = { kind: 'edit'; slotId: string } | { kind: 'add' } | null;

function PlanBody({ plan, etag, settings, now, past, onRegenerate }: PlanBodyProps) {
  const { cadence, pillars, formats, lanes } = settings;
  const zone = plan.timezone;
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const postsQuery = useWeekPosts(plan.weekStart, zone);
  const articlesQuery = usePlanArticles(plan.id);
  const editor = usePlanEditor(plan, etag);
  const [dialog, setDialog] = useState<SlotDialogState>(null);
  const [removing, setRemoving] = useState<SlotModel | null>(null);

  const entries = postsQuery.data ?? [];
  const posts = entries.map((entry) => entry.item);
  const anyWriting = posts.some((post) => post.status === 'generating');
  // Keep the event stream open while posts are being written, so their states arrive.
  useLiveEvents(anyWriting);

  const today = localDateOf(now, zone);
  const models = slotModels(plan, posts, now);
  const context: MoveContext = { plan, posts, cadence, zone, nowMs: now };
  // Posts a slot shows already (its own, or a quick take written for it) are not repeated beside it.
  const shown = new Set(models.flatMap((model) => (model.post ? [model.post.id] : [])));
  const outside = outsidePosts(plan, posts).filter(
    (post) =>
      !shown.has(post.id) &&
      post.slot !== null &&
      weekDates(plan.weekStart).includes(localDateOf(Date.parse(post.slot.utc), zone)),
  );
  const milestones = plan.flagship ? flagshipMilestones(plan.weekStart, cadence, zone, plan.flagship.publishAt) : [];
  const days: DayModel[] = weekDates(plan.weekStart).map((date) => ({
    date,
    slots: models.filter((model) => model.date === date),
    outside: outside.filter((post) => post.slot !== null && localDateOf(Date.parse(post.slot.utc), zone) === date),
    milestones: milestones.filter((milestone) => milestone.date === date),
    isToday: date === today,
    isPast: date < today,
  }));
  const flagged = new Set(models.filter((model) => model.findings.length > 0).map((model) => model.slot.id));
  const editable = plan.status === 'proposed' || plan.status === 'approved';
  const flagshipArticleId = plan.flagship?.articleId ?? undefined;
  const article = (articlesQuery.data ?? [])
    .map((entry) => entry.item)
    .find((item) => (flagshipArticleId ? item.id === flagshipArticleId : item.purpose === 'flagship'));

  const actions: SlotActions = {
    options: (model) => dayOptions(model.slot, context),
    onMove: (model, option) => {
      const slots = withMovedSlot(plan, model.slot.id, option.date, option.time);
      editor.apply({
        body: { slots },
        preview: previewPlan(plan, slots, cadence),
        success: `Moved to ${slotLabel(option.date, option.time)}.`,
      });
    },
    onEdit: (model) => {
      setDialog({ kind: 'edit', slotId: model.slot.id });
    },
    onRemove: (model) => {
      setRemoving(model);
    },
    takeHref: (model) => takeHref(model.slot, plan.weekStart),
  };

  const editing = dialog?.kind === 'edit' ? models.find((model) => model.slot.id === dialog.slotId) : undefined;
  const firstFree = dayOptions(newSlotProbe(plan, cadence), context).find((option) => !option.block);
  const canAdd = editable && (plan.slots ?? []).length < cadence.postsPerWeek.max && plan.mode !== 'away';

  const saveFlagship = (flagship: FlagshipInput | null, success: string) => {
    const preview: WeekPlan = {
      ...plan,
      flagship: flagship
        ? { ...plan.flagship, ...flagship, articleId: plan.flagship?.articleId ?? null, publishAt: plan.flagship?.publishAt ?? null }
        : null,
    };
    editor.apply({ body: { slots: slotInputs(plan), flagship }, preview, success });
  };

  const can = useTransitions();
  const approve = (
    <ApproveSection
      plan={plan}
      etag={etag}
      cadence={cadence}
      ready={!editor.saving}
      canApprove={can('week_plan', plan.status, 'approved')}
    />
  );
  const posted = models.filter((model) => model.post?.status === 'posted').length;

  return (
    <div className="space-y-8">
      <PlanHeader
        plan={plan}
        cadence={cadence}
        canAdd={canAdd}
        canRegenerate={!past && can('week_plan', plan.status, 'generating')}
        posted={plan.status === 'approved' || plan.status === 'closed' ? { done: posted, total: models.length } : undefined}
        onAdd={() => {
          setDialog({ kind: 'add' });
        }}
        onRegenerate={onRegenerate}
      />

      {desktop ? approve : null}

      <MissedPosts entries={entries} zone={zone} nowMs={now} />

      {postsQuery.isError ? (
        <ProblemAlert
          error={postsQuery.error}
          title="Couldn’t load this week’s posts."
          onRetry={() => void postsQuery.refetch()}
          retryLabel="Try loading the posts again"
        />
      ) : null}

      {desktop ? (
        <WeekBoard days={days} zone={zone} weekStart={plan.weekStart} pillars={pillars} actions={actions} flagged={flagged} />
      ) : (
        <WeekList days={days} zone={zone} pillars={pillars} actions={actions} flagged={flagged} />
      )}

      <div className="grid gap-10 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <FlagshipSection
          plan={plan}
          article={article}
          cadence={cadence}
          pillars={pillars}
          formats={formats}
          nowMs={now}
          editable={editable}
          saving={editor.saving}
          onSave={saveFlagship}
        />
        <div className="space-y-8">
          <PillarBalanceSection slots={plan.slots ?? []} pillars={pillars} minShare={lanes.onPillarMinShare} />
          <AdvisorySection plan={plan} />
        </div>
      </div>

      {plan.scorecard ? <ScorecardSection plan={plan} /> : null}

      {desktop ? null : approve}

      <SlotDialog
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
        slot={editing?.slot}
        hasPost={Boolean(editing?.slot.postId) && editing?.post !== undefined && !GONE_STATUSES.has(editing.post.status)}
        approved={plan.status === 'approved'}
        context={context}
        pillars={pillars}
        imageCardEnabled={lanes.imageCardEnabled}
        initialDate={firstFree?.date ?? plan.weekStart}
        initialTime={firstFree?.time ?? '10:00'}
        saving={editor.saving}
        onSave={(content) => {
          const current = dialog;
          setDialog(null);
          if (current?.kind === 'edit' && editing) {
            const hasPost = Boolean(editing.slot.postId);
            const slots = withEditedSlot(plan, editing.slot.id, content, hasPost);
            editor.apply({ body: { slots }, preview: previewPlan(plan, slots, cadence), success: 'Slot saved.' });
          } else {
            const slots = withAddedSlot(plan, newSlotId(), content);
            editor.apply({ body: { slots }, preview: previewPlan(plan, slots, cadence), success: 'Slot added.' });
          }
        }}
      />

      <AlertDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{removing ? `Remove the ${removing.label} slot?` : 'Remove the slot?'}</AlertDialogTitle>
            <AlertDialogDescription>
              {plan.status === 'approved'
                ? 'The week keeps its other posts. Removing a slot that was still to be written sends the plan back to you for approval.'
                : 'The week keeps its other posts. You can add a slot again before you approve.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              onClick={() => {
                if (!removing) return;
                const slots = withoutSlot(plan, removing.slot.id);
                editor.apply({ body: { slots }, preview: previewPlan(plan, slots, cadence), success: 'Slot removed.' });
              }}
            >
              Remove slot
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ConflictDialog
        {...editor.conflict}
        subject="This week’s plan"
        mine={editor.refused?.preview ? describePlan(editor.refused.preview, pillars) : undefined}
        theirs={editor.refused?.preview ? describePlan(plan, pillars) : undefined}
        saving={editor.saving}
        onKeepMine={editor.keepMine}
        onTakeTheirs={editor.takeTheirs}
      />
    </div>
  );
}

/** A stand-in slot for "where could a new slot go": at a template time, so each day offers its own. */
function newSlotProbe(plan: WeekPlan, cadence: CadenceView): PlannedSlot {
  const template = cadence.postSlots.find((candidate) => candidate.kind === 'primary') ?? cadence.postSlots[0];
  const date = template ? addDays(plan.weekStart, WEEKDAY_IDS.indexOf(template.day)) : plan.weekStart;
  return {
    id: 'new-slot',
    slot: { localDate: date, localTime: template?.time ?? '10:00', timezone: plan.timezone, utc: '', kind: 'primary' },
    postId: null,
    lane: 'native',
    pillar: null,
    format: 'text',
    title: null,
  };
}

interface PlanHeaderProps {
  plan: WeekPlan;
  cadence: CadenceView;
  canAdd: boolean;
  canRegenerate: boolean;
  /** How many of the week's posts went out, once the plan is approved. */
  posted: { readonly done: number; readonly total: number } | undefined;
  onAdd: () => void;
  onRegenerate: (jobId: string) => void;
}

function PlanHeader({ plan, cadence, canAdd, canRegenerate, posted, onAdd, onRegenerate }: PlanHeaderProps) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <StatusBadge status={PLAN_STATUS[plan.status]} />
          {posted && posted.total > 0 ? (
            <span className="font-medium text-ink">{`${String(posted.done)} of ${countLabel(posted.total, 'post')} out`}</span>
          ) : null}
          <span className="text-ink-muted">
            {`${MODE_LABEL[plan.mode]} week: ${modeSummary(plan.mode, cadence.postsPerWeek)}. `}
            {plan.status !== 'closed' ? (
              <Link
                to="/calendar/availability"
                className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
              >
                Change the mode
              </Link>
            ) : null}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {canAdd ? (
            <Button variant="secondary" size="sm" onClick={onAdd}>
              <Plus aria-hidden="true" />
              Add a slot
            </Button>
          ) : null}
          {canRegenerate ? (
            <GenerateButton weekStart={plan.weekStart} label="Plan it again" confirm variant="secondary" onStarted={onRegenerate} />
          ) : null}
        </div>
      </div>
      {plan.rationale ? (
        <Collapsible className="max-w-measure">
          <CollapsibleTrigger className="group inline-flex items-center gap-1.5 text-sm font-medium text-ink">
            <ChevronDown aria-hidden="true" className="size-4 transition-transform duration-150 group-data-[state=open]:rotate-180" />
            Why the planner chose this week
          </CollapsibleTrigger>
          <CollapsibleContent>
            <p className="mt-2 font-serif text-lg leading-prose text-ink-muted">{plan.rationale}</p>
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}

function ScorecardSection({ plan }: { plan: WeekPlan }) {
  const headingId = useId();
  const card = plan.scorecard;
  if (!card) return null;
  return (
    <section aria-labelledby={headingId} className="space-y-4">
      <h3 id={headingId} className="font-semibold text-ink">
        The week’s scorecard
      </h3>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatTile label="Posts published" value={card.postsPublished} />
        <StatTile label="With your own signal" value={card.postsWithOwnerSignal} />
        <StatTile label="Replies" value={card.replies} />
        <StatTile label="Substantive comments" value={card.substantiveComments} />
        <StatTile
          label="On a pillar"
          value={card.onPillarShare ?? null}
          format={(value) => percentLabel(value)}
        />
      </div>
    </section>
  );
}
