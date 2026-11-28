import { MessageSquareQuote, Plus, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { toast } from 'sonner';

import { EmptyState } from '@/components/shell/empty-state';
import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { components } from '@/lib/api/schema';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { useNow } from '@/lib/use-now';
import { pillarThreadClass } from '@/lib/pillars';

import { SettingsNotice } from './components/settings-notice';
import {
  useCalendarSettings,
  useIdea,
  useIdeas,
  useTransitions,
  useWeekPlan,
  type CalendarSettings,
  type IdeaStatus,
} from './data';
import { IdeaCard, type IdeaActions, type IdeaTarget, type SnoozeChoice } from './ideas/idea-card';
import { CaptureIdeaDialog, EditIdeaDialog, StartArticleDialog, StartRecutDialog } from './ideas/idea-dialogs';
import { TakeSheet, type TakeGoal, type TakeSlot } from './ideas/take-sheet';
import { attributeSelector } from './lib/dom';
import { countLabel, LANE_LABEL } from './lib/labels';
import {
  addDays,
  currentWeekStart,
  isMonday,
  localDateOf,
  localToUtcMs,
  momentLabel,
  mondayOf,
  parseTimestamp,
  slotLabel,
  toTimestamp,
} from './lib/time';

type Idea = components['schemas']['Idea'];
type IdeaLane = components['schemas']['IdeaLane'];
type IdeaEntry = components['schemas']['Versioned_Idea_'];

const VIEWS = [
  { id: 'new', label: 'New', statuses: ['new'] },
  { id: 'kept', label: 'Kept', statuses: ['accepted'] },
  { id: 'snoozed', label: 'Snoozed', statuses: ['snoozed'] },
  { id: 'dismissed', label: 'Dismissed', statuses: ['dismissed', 'expired'] },
] as const satisfies readonly { id: string; label: string; statuses: readonly IdeaStatus[] }[];

type ViewId = (typeof VIEWS)[number]['id'];

const LANE_FILTERS: readonly IdeaLane[] = ['flagship', 'quick_take', 'native', 'derivative', 'repurpose'];
const ANY = 'any';

const TRANSITION_TOAST: Record<IdeaTarget, string> = {
  accepted: 'Kept. It waits for a slot in Kept.',
  dismissed: 'Dismissed.',
  new: 'Back in New.',
  snoozed: 'Snoozed.',
};

/**
 * Calendar > Ideas (`/calendar/ideas`): the ideas inbox. Feeds and weekly scouting land here
 * triaged (pillar, relevance, novelty, lane, format, angle); the owner keeps, snoozes or
 * dismisses each, adds a take with one tap ("What's your take?"), or turns it into a piece (a
 * flagship article or a quick take). Feed items never become content without one of those taps.
 * Triage is phone-first. Links from Today (`?take=<slot>&idea=<id>`) open the take for a
 * quick-take slot directly; only a link with `take` opens the take sheet. A link with `idea` alone
 * (the Studio's repurpose toast, "Open it") shows that idea first, focused, with the start that
 * suits it (`repurposeStart`).
 */
export function IdeasView() {
  const settings = useCalendarSettings();
  if (settings.pending) return <IdeasSkeleton />;
  return <IdeasReady settings={settings} />;
}

function IdeasSkeleton() {
  return (
    <div className="space-y-6">
      <p role="status" className="sr-only">
        Loading ideas
      </p>
      <Skeleton className="h-11 w-full max-w-md" />
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className="space-y-3 border-b border-line py-5">
          <Skeleton className="h-3 w-40" />
          <Skeleton className="h-6 w-[min(32rem,90%)]" />
          <Skeleton className="h-4 w-[min(24rem,70%)]" />
        </div>
      ))}
    </div>
  );
}

function snoozeUntil(choice: SnoozeChoice, nowMs: number, zone: string): number {
  const today = localDateOf(nowMs, zone);
  const date =
    choice === 'tomorrow' ? addDays(today, 1) : choice === 'three_days' ? addDays(today, 3) : addDays(mondayOf(today), 7);
  return localToUtcMs(date, '09:00', zone);
}

function IdeasReady({ settings }: { settings: CalendarSettings }) {
  const { pillars, formats, lanes, cadence } = settings;
  const zone = cadence.timezone;
  const now = useNow();
  const can = useTransitions();
  const listHeadingId = useId();
  const [params, setParams] = useSearchParams();
  const viewParam = params.get('view');
  const view: ViewId = VIEWS.some((candidate) => candidate.id === viewParam) ? (viewParam as ViewId) : 'new';
  const pillarParam = params.get('pillar');
  const pillar = pillarParam && pillars.some((option) => option.id === pillarParam) ? pillarParam : undefined;
  const laneParam = params.get('lane');
  const lane = LANE_FILTERS.find((candidate) => candidate === laneParam);
  const statuses = VIEWS.find((candidate) => candidate.id === view)?.statuses ?? ['new'];

  const ideas = useIdeas({ status: statuses, pillar, lane });
  const [capturing, setCapturing] = useState(false);
  const [editing, setEditing] = useState<IdeaEntry | undefined>();
  const [starting, setStarting] = useState<Idea | undefined>();
  const [recutting, setRecutting] = useState<Idea | undefined>();
  const [take, setTake] = useState<{ idea: Idea; goal: TakeGoal } | undefined>();
  const [busyId, setBusyId] = useState<string | null>(null);

  // Today's "What's your take?" link: `?take=<slot id>&week=<Monday>&idea=<idea id>`.
  const takeSlotId = params.get('take') ?? undefined;
  const linkedIdeaId = params.get('idea') ?? undefined;
  const weekParam = params.get('week');
  const thisWeek = currentWeekStart(now, zone);
  const slotWeeks = weekParam && isMonday(weekParam) ? [weekParam] : [thisWeek, addDays(thisWeek, 7)];
  const firstPlan = useWeekPlan(slotWeeks[0] ?? thisWeek, takeSlotId !== undefined);
  const secondPlan = useWeekPlan(slotWeeks[1] ?? slotWeeks[0] ?? thisWeek, takeSlotId !== undefined && slotWeeks.length > 1);
  const planSlot = [firstPlan.data?.item, secondPlan.data?.item]
    .flatMap((plan) => (plan?.slots ?? []).map((slot) => ({ weekStart: plan?.weekStart ?? '', slot })))
    .find((entry) => entry.slot.id === takeSlotId && entry.weekStart !== '');
  const takeSlot: TakeSlot | undefined = planSlot
    ? {
        weekStart: planSlot.weekStart,
        slotId: planSlot.slot.id,
        localDate: planSlot.slot.slot.localDate,
        localTime: planSlot.slot.slot.localTime,
      }
    : undefined;
  const linkedIdea = useIdea(linkedIdeaId);
  const [linkHandled, setLinkHandled] = useState(false);
  // Only Today's take link (`take=`) opens the take sheet by itself.
  const linkedTake =
    takeSlotId !== undefined &&
    !linkHandled &&
    linkedIdea.data &&
    (linkedIdea.data.item.status === 'new' || linkedIdea.data.item.status === 'accepted')
      ? { idea: linkedIdea.data.item, goal: 'quick_take' as const }
      : undefined;
  const openTake = take ?? linkedTake;
  // `?idea=` alone: that idea, shown first (whatever its status or page) and focused.
  const opened = takeSlotId === undefined && linkedIdeaId !== undefined ? linkedIdea.data : undefined;
  const openedId = opened?.item.id;
  const openedHeadingId = useId();

  const clearTakeLink = () => {
    setLinkHandled(true);
    if (!params.has('take') && !params.has('idea')) return;
    const next = new URLSearchParams(params);
    for (const name of ['take', 'idea', 'week']) next.delete(name);
    setParams(next, { replace: true, preventScrollReset: true });
  };

  const setParam = (name: string, value: string | undefined) => {
    const next = new URLSearchParams(params);
    if (value === undefined) next.delete(name);
    else next.set(name, value);
    setParams(next, { replace: true, preventScrollReset: true });
  };

  const transition = useApiMutation('post', '/api/ideas/{idea_id}/transition', {
    updates: (data) => queryKeys.ideas.detail(data.item.id),
    invalidates: [queryKeys.ideas.lists()],
    errorToast: 'Couldn’t update the idea.',
  });

  // After an idea leaves the list, focus moves to the next one (or the list's heading). The idea
  // a link opened is shown above the list instead, and stays there as it changes.
  const entries = sortForView(ideas.data ?? [], view).filter((entry) => entry.item.id !== openedId);
  const pendingFocus = useRef<{ leaving: string; next: string | undefined } | null>(null);
  const ids = entries.map((entry) => entry.item.id).join(' ');
  useEffect(() => {
    const pending = pendingFocus.current;
    if (!pending || ids.split(' ').includes(pending.leaving)) return;
    pendingFocus.current = null;
    const target = pending.next
      ? document.querySelector<HTMLElement>(`${attributeSelector('data-idea-id', pending.next)} h3`)
      : document.getElementById(listHeadingId);
    // The last idea left: the list gave way to its empty state, so the page's heading takes focus.
    (target ?? document.querySelector<HTMLElement>('main h1'))?.focus();
  }, [ids, listHeadingId]);

  // A link that opens an idea moves focus to it once it is shown.
  useEffect(() => {
    if (openedId !== undefined) focusIdea(openedId);
  }, [openedId]);

  const move = (idea: Idea, to: IdeaTarget, snoozedUntilMs?: number) => {
    const pinned = opened?.item.id === idea.id;
    const entry = pinned ? opened : entries.find((candidate) => candidate.item.id === idea.id);
    if (!entry) return;
    setBusyId(idea.id);
    if (pinned) {
      // The opened idea stays on the page as it changes: focus stays on it.
      pendingFocus.current = null;
    } else {
      // Set before the list refetches (the mutation waits for it), so the effect sees it leave.
      const index = entries.indexOf(entry);
      pendingFocus.current = { leaving: idea.id, next: entries[index + 1]?.item.id ?? entries[index - 1]?.item.id };
    }
    transition.mutate(
      {
        params: { path: { idea_id: idea.id } },
        body: { to, ...(snoozedUntilMs !== undefined ? { snoozedUntil: toTimestamp(snoozedUntilMs) } : {}) },
        ifMatch: entry.etag ?? undefined,
      },
      {
        onSuccess: (data) => {
          if (pinned) focusIdea(idea.id);
          const message =
            to === 'snoozed' && snoozedUntilMs !== undefined
              ? `Snoozed until ${momentLabel(snoozedUntilMs, zone)}.`
              : TRANSITION_TOAST[to];
          const undo = idea.status === 'new' && to !== 'new' && can('idea', to, 'new');
          toast.success(message, {
            action: undo
              ? {
                  label: 'Undo',
                  onClick: () => {
                    transition.mutate({ params: { path: { idea_id: idea.id } }, body: { to: 'new' }, ifMatch: data.etag ?? undefined });
                  },
                }
              : undefined,
          });
        },
        onError: () => {
          pendingFocus.current = null;
          // A stale etag or a status the idea left: show it as it is now.
          void ideas.refetch();
          if (pinned) void linkedIdea.refetch();
        },
        onSettled: () => {
          setBusyId(null);
        },
      },
    );
  };

  const actions: IdeaActions = {
    can,
    onTransition: (idea, to) => {
      move(idea, to);
    },
    onSnooze: (idea, choice) => {
      move(idea, 'snoozed', snoozeUntil(choice, now, zone));
    },
    onTake: (idea) => {
      setTake({ idea, goal: takeSlotId ? 'quick_take' : 'pov' });
    },
    onQuickTake: (idea) => {
      setTake({ idea, goal: 'quick_take' });
    },
    onStartArticle: setStarting,
    onStartRecut: setRecutting,
    onEdit: (idea) => {
      setEditing(entries.find((entry) => entry.item.id === idea.id));
    },
  };

  const filtered = pillar !== undefined || lane !== undefined;
  const viewLabel = VIEWS.find((candidate) => candidate.id === view)?.label ?? 'New';
  const expiringSoon = entries.filter((entry) => {
    const expires = parseTimestamp(entry.item.expiresAt);
    return entry.item.timeSensitive && expires !== undefined && expires > now && expires - now < 24 * 3_600_000;
  }).length;

  return (
    <div className="space-y-8">
      <SettingsNotice settings={settings} />
      {takeSlotId ? (
        <Alert variant="info" icon={<MessageSquareQuote aria-hidden="true" />}>
          <AlertTitle>
            {takeSlot
              ? `Your quick-take slot on ${slotLabel(takeSlot.localDate, takeSlot.localTime)} needs your take`
              : 'A quick-take slot needs your take'}
          </AlertTitle>
          <AlertDescription>
            <p>Pick the idea to write about and tap “What’s your take?”. The post is written from your words.</p>
          </AlertDescription>
          <AlertActions>
            <Button size="sm" variant="ghost" onClick={clearTakeLink}>
              <X aria-hidden="true" />
              Not now
            </Button>
          </AlertActions>
        </Alert>
      ) : null}

      {opened ? (
        <section aria-labelledby={openedHeadingId} className="rounded-lg border border-line bg-sunken px-4 pt-4 sm:px-5">
          <div className="flex items-center justify-between gap-3">
            <h2 id={openedHeadingId} className="text-sm font-semibold text-ink">
              The idea you opened
            </h2>
            <Button
              size="sm"
              variant="ghost"
              aria-label="Close the idea you opened"
              onClick={() => {
                setParam('idea', undefined);
              }}
            >
              <X aria-hidden="true" />
              Close
            </Button>
          </div>
          <IdeaCard
            idea={opened.item}
            pillars={pillars}
            formats={formats}
            zone={zone}
            nowMs={now}
            actions={actions}
            busy={busyId === opened.item.id}
          />
        </section>
      ) : null}

      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <SegmentedControl
          aria-label="Ideas to show"
          value={view}
          onValueChange={(value) => {
            setParam('view', value === 'new' ? undefined : value);
          }}
          fullWidth
          className="md:w-auto"
        >
          {VIEWS.map((option) => (
            <SegmentedControlItem key={option.id} value={option.id}>
              {option.label}
            </SegmentedControlItem>
          ))}
        </SegmentedControl>
        <Button
          variant="secondary"
          onClick={() => {
            setCapturing(true);
          }}
        >
          <Plus aria-hidden="true" />
          Add an idea
        </Button>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <ToggleGroup
          type="single"
          aria-label="Filter by pillar"
          value={pillar ?? ''}
          onValueChange={(value) => {
            setParam('pillar', value === '' ? undefined : value);
          }}
          variant="outline"
          size="sm"
        >
          {pillars.map((option) => (
            <ToggleGroupItem key={option.id} value={option.id}>
              <span aria-hidden="true" className={`h-3 w-1 rounded-full ${pillarThreadClass(option.id)}`} />
              {option.name}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="sm:w-52">
          <Select
            value={lane ?? ANY}
            onValueChange={(value) => {
              setParam('lane', value === ANY ? undefined : value);
            }}
          >
            <SelectTrigger aria-label="Filter by lane">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Any lane</SelectItem>
              {LANE_FILTERS.map((value) => (
                <SelectItem key={value} value={value}>
                  {LANE_LABEL[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {filtered ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              const next = new URLSearchParams(params);
              next.delete('pillar');
              next.delete('lane');
              setParams(next, { replace: true, preventScrollReset: true });
            }}
          >
            Clear filters
          </Button>
        ) : null}
      </div>

      {ideas.isPending ? (
        <IdeasSkeleton />
      ) : ideas.isError ? (
        <ProblemAlert
          error={ideas.error}
          title="Couldn’t load the ideas."
          onRetry={() => void ideas.refetch()}
          retryLabel="Try loading the ideas again"
        />
      ) : entries.length === 0 ? (
        <EmptyIdeas
          view={view}
          filtered={filtered}
          onAdd={() => {
            setCapturing(true);
          }}
        />
      ) : (
        <section aria-labelledby={listHeadingId}>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 id={listHeadingId} tabIndex={-1} className="text-sm font-semibold text-ink outline-none">
              {`${countLabel(entries.length, view === 'new' ? 'new idea' : 'idea')}${ideas.hasNextPage ? ' so far' : ''}`}
              {view !== 'new' ? <span className="sr-only">{` (${viewLabel})`}</span> : null}
            </h2>
            {expiringSoon > 0 ? (
              <p className="text-sm text-danger">
                {`${String(expiringSoon)} time-sensitive ${expiringSoon === 1 ? 'idea expires' : 'ideas expire'} within a day.`}
              </p>
            ) : null}
          </div>
          <ul className="mt-2 border-t border-line">
            {entries.map((entry) => (
              <li key={entry.item.id}>
                <IdeaCard
                  idea={entry.item}
                  pillars={pillars}
                  formats={formats}
                  zone={zone}
                  nowMs={now}
                  actions={actions}
                  busy={busyId === entry.item.id}
                />
              </li>
            ))}
          </ul>
          {ideas.hasNextPage ? (
            <Button
              variant="secondary"
              className="mt-6 w-full sm:w-auto"
              disabled={ideas.isFetchingNextPage}
              onClick={() => void ideas.fetchNextPage()}
            >
              {ideas.isFetchingNextPage ? 'Loading…' : 'Show more ideas'}
            </Button>
          ) : null}
        </section>
      )}

      <CaptureIdeaDialog open={capturing} onOpenChange={setCapturing} pillars={pillars} lanes={lanes} />
      <EditIdeaDialog
        idea={editing?.item}
        etag={editing?.etag ?? null}
        onClose={() => {
          setEditing(undefined);
        }}
        pillars={pillars}
        formats={formats}
      />
      <StartArticleDialog
        idea={starting}
        onClose={() => {
          setStarting(undefined);
        }}
        pillars={pillars}
        formats={formats}
      />
      <StartRecutDialog
        idea={recutting}
        onClose={() => {
          setRecutting(undefined);
        }}
        pillars={pillars}
        lanes={lanes}
      />
      <TakeSheet
        idea={openTake?.idea}
        goal={openTake?.goal ?? 'pov'}
        slot={takeSlot}
        pillars={pillars}
        lanes={lanes}
        onClose={() => {
          setTake(undefined);
          clearTakeLink();
        }}
        onDone={(result) => {
          if (result.postId) {
            // The sheet stays open on the post being written; the slot's link has done its job.
            setTake((current) => current ?? linkedTake);
            clearTakeLink();
            return;
          }
          setTake(undefined);
          clearTakeLink();
        }}
      />
    </div>
  );
}

/** Focus an idea's title (its card stays where it is). */
function focusIdea(ideaId: string) {
  document.querySelector<HTMLElement>(`${attributeSelector('data-idea-id', ideaId)} h3`)?.focus();
}

/** Time-sensitive ideas that expire first lead the New view; the rest stay newest first. */
function sortForView(entries: readonly IdeaEntry[], view: ViewId): IdeaEntry[] {
  if (view !== 'new') return [...entries];
  const expiry = (entry: IdeaEntry) =>
    entry.item.timeSensitive ? (parseTimestamp(entry.item.expiresAt) ?? Number.POSITIVE_INFINITY) : Number.POSITIVE_INFINITY;
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => expiry(a.entry) - expiry(b.entry) || a.index - b.index)
    .map(({ entry }) => entry);
}

function EmptyIdeas({ view, filtered, onAdd }: { view: ViewId; filtered: boolean; onAdd: () => void }) {
  if (filtered) {
    return (
      <EmptyState title="No ideas match these filters." yourPart="Clear a filter, or add the idea yourself.">
        <p>Try another pillar or lane, or clear the filters to see the whole inbox.</p>
      </EmptyState>
    );
  }
  switch (view) {
    case 'new':
      return (
        <EmptyState
          title="No ideas yet."
          yourPart="Pick the ideas worth a slot, and add your take with one tap. Your take is what makes a quick post yours."
        >
          <p>
            Feeds and weekly scouting land here, sorted by pillar with notes on relevance, novelty, format and angle.
            Nothing turns into content until you pick it.
          </p>
          <p>Time-sensitive news goes to a quick-take lane that publishes within 72 hours.</p>
          <div className="pt-2">
            <Button variant="secondary" onClick={onAdd}>
              <Plus aria-hidden="true" />
              Add an idea
            </Button>
          </div>
        </EmptyState>
      );
    case 'kept':
      return (
        <EmptyState title="No kept ideas." yourPart="Keep the ideas you want the planner to use.">
          <p>Ideas you keep wait here for a slot. The planner draws on them when it proposes a week.</p>
        </EmptyState>
      );
    case 'snoozed':
      return (
        <EmptyState title="Nothing snoozed." yourPart="Snooze an idea that isn’t ready yet.">
          <p>Snoozed ideas come back to New when their time is up.</p>
        </EmptyState>
      );
    case 'dismissed':
      return (
        <EmptyState title="Nothing dismissed." yourPart="Dismiss what isn’t worth a post; you can bring it back.">
          <p>Dismissed ideas, and time-sensitive ones that expired, wait here in case you change your mind.</p>
        </EmptyState>
      );
  }
}
