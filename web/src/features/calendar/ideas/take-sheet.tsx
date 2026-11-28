import { CircleCheck, ExternalLink, Settings2 } from 'lucide-react';
import { useId, useState } from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { ProblemAlert } from '@/features/data/problem-alert';
import { IntakeFlow, POLICY_SETTINGS_PATH, useIntakeState } from '@/features/intake';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { apiRequest } from '@/lib/api/request';
import { confidentialFindings, isProblem } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';
import { toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';
import { queryKeys } from '@/lib/query/keys';

import { planIdFor } from '../lib/plan';
import type { LanesView, PillarView } from '../lib/settings';
import { slotLabel } from '../lib/time';

type Idea = components['schemas']['Idea'];

/** The plan slot a take fills (a quick-take slot from the week plan, `?take=`). */
export interface TakeSlot {
  /** The plan's week (its Monday) and the slot's id: the post is linked to that slot. */
  readonly weekStart: string;
  readonly slotId: string;
  readonly localDate: string;
  readonly localTime: string;
}

export type TakeGoal = 'pov' | 'quick_take';

interface TakeSheetProps {
  /** The idea to take a position on; `undefined` closes the sheet. */
  idea: Idea | undefined;
  /** What the owner came to do: save a take, or start a quick take from it. */
  goal: TakeGoal;
  slot: TakeSlot | undefined;
  pillars: readonly PillarView[];
  lanes: LanesView;
  onClose: () => void;
  /** Called once the take is saved (and the quick take started, when asked). */
  onDone: (result: TakeResult) => void;
}

export interface TakeResult {
  readonly povId: string;
  readonly postId?: string;
}

/** The `posts.generate` job's steps, for the timeline after a quick take starts. */
const POST_STEP_LABELS: Readonly<Record<string, string>> = {
  generate: 'Write the post from your take',
  check: 'Check it: lint, claims and near-duplicates',
};

/**
 * "What's your take?" on an idea: the owner writes or dictates a position (private: it stays in
 * this component), the shared intake flow redacts it and the owner confirms public-safe wording;
 * only that wording is saved, as a POV (`POST /api/povs`, origin `idea_take`) and, when asked, as
 * the take a quick take is written from (`POST /api/posts {lane: quick_take, takeText}`). Nothing
 * is generated before the owner's tap.
 */
export function TakeSheet({ idea, goal, slot, pillars, lanes, onClose, onDone }: TakeSheetProps) {
  const desktop = useMediaQuery(DESKTOP_QUERY);
  return (
    <Sheet
      open={idea !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent
        side={desktop ? 'right' : 'bottom'}
        className="overflow-y-auto md:w-[min(40rem,92vw)]"
        // Raw words would be lost by a stray tap outside: only the close button and Escape close it.
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        {idea ? (
          // A fresh flow per idea: nothing typed for one idea carries over to the next.
          <TakeFlow key={idea.id} idea={idea} goal={goal} slot={slot} pillars={pillars} lanes={lanes} onClose={onClose} onDone={onDone} />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

interface TakeFlowProps extends Omit<TakeSheetProps, 'idea'> {
  idea: Idea;
}

function TakeFlow({ idea, goal: initialGoal, slot, pillars, lanes, onClose, onDone }: TakeFlowProps) {
  const { api, queryClient } = useAppData();
  const [intake, setIntake] = useIntakeState();
  const [goal, setGoal] = useState<TakeGoal>(initialGoal);
  const [pillar, setPillar] = useState(idea.pillar ?? '');
  const [pillarError, setPillarError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>();
  const [povId, setPovId] = useState<string>();
  const [started, setStarted] = useState<{ postId: string; jobId: string } | null>(null);
  const goalId = useId();

  const confirmed = intake.stage === 'confirmed' ? intake.confirmed : null;
  const quickTake = goal === 'quick_take';

  const save = async () => {
    if (!confirmed) return;
    if (pillar === '') {
      setPillarError('Pick the pillar this take belongs to.');
      return;
    }
    setPillarError(undefined);
    setError(undefined);
    setSaving(true);
    try {
      let savedPov = povId;
      if (!savedPov) {
        const pov = await apiRequest(api, 'post', '/api/povs', {
          body: {
            pillar,
            statement: confirmed.text,
            origin: 'idea_take',
            ideaId: idea.id,
            confidentiality: 'anonymized',
          },
        });
        savedPov = pov.item.id;
        setPovId(savedPov);
        void queryClient.invalidateQueries({ queryKey: queryKeys.povs.lists() });
      }
      if (!quickTake) {
        toastSuccess('Take saved to your POV bank.');
        onDone({ povId: savedPov });
        return;
      }
      const created = await apiRequest(api, 'post', '/api/posts', {
        body: {
          lane: 'quick_take',
          pillar,
          format: 'text',
          ideaId: idea.id,
          takeText: confirmed.text,
          povIds: [savedPov],
          // The slot's own time, and the plan's slot names the post (the plan is not re-approved).
          ...(slot ? { planSlot: { weekStart: slot.weekStart, slotId: slot.slotId } } : {}),
        },
      });
      setStarted({ postId: created.post.item.id, jobId: created.jobId });
      toastSuccess('Quick take started.');
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.posts.lists() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.ideas.lists() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.ideas.detail(idea.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.today }),
        // The plan's slot now names the post, so the plan has a new etag and no event says so:
        // the Calendar must not edit it with the one it read before (a 412 and a false conflict).
        ...(slot
          ? [
              queryClient.invalidateQueries({ queryKey: queryKeys.plans.detail(planIdFor(slot.weekStart)) }),
              queryClient.invalidateQueries({ queryKey: queryKeys.plans.lists() }),
            ]
          : []),
      ]);
      onDone({ povId: savedPov, postId: created.post.item.id });
    } catch (caught) {
      setError(caught);
    } finally {
      setSaving(false);
    }
  };

  if (started) {
    return (
      <div className="grid gap-5">
        <SheetHeader>
          <SheetTitle>Your quick take is being written</SheetTitle>
          <SheetDescription>{idea.title}</SheetDescription>
        </SheetHeader>
        <p className="flex items-start gap-2 text-ink">
          <CircleCheck aria-hidden="true" className="mt-1 size-4 shrink-0 text-success" />
          Your take is in your POV bank, and the post is written from it. You review and approve it before anything
          goes out.
        </p>
        <JobTimeline jobId={started.jobId} title="Writing the post" labels={POST_STEP_LABELS} />
        <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onClose}>
            Done
          </Button>
          <Button asChild>
            <Link to={`/studio/${started.postId}/posts`}>
              <ExternalLink aria-hidden="true" />
              Open the post
            </Link>
          </Button>
        </div>
      </div>
    );
  }

  const confidential = confidentialFindings(error);
  const ideaPillarMissing = !idea.pillar;

  return (
    <div className="grid gap-6">
      <SheetHeader>
        <SheetTitle>What’s your take?</SheetTitle>
        <SheetDescription>{idea.title}</SheetDescription>
      </SheetHeader>
      {slot ? (
        <p className="rounded-md bg-sunken px-3 py-2 text-sm text-ink">
          {`For the quick-take slot on ${slotLabel(slot.localDate, slot.localTime)}.`}
        </p>
      ) : null}

      <IntakeFlow
        purpose="take"
        pillar={pillar === '' ? null : pillar}
        state={intake}
        onStateChange={setIntake}
        label="Your take"
        description="What do you think about it, and why? A sentence or two is enough. Type, or dictate with your keyboard’s microphone."
        headingLevel={3}
        // A POV statement holds at most 1,000 characters (`PovIn`).
        maxLength={1000}
        confirmLabel="Use this wording"
        disabled={saving}
      />

      {confirmed ? (
        <div className="grid gap-5">
          {ideaPillarMissing ? (
            <Field label="Pillar" error={pillarError} required>
              {(control) => (
                <Select value={pillar} onValueChange={setPillar}>
                  <SelectTrigger {...control}>
                    <SelectValue placeholder="Choose a pillar" />
                  </SelectTrigger>
                  <SelectContent>
                    {pillars.map((option) => (
                      <SelectItem key={option.id} value={option.id}>
                        {option.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
          ) : null}
          <Fieldset legend="What to do with it">
            {(group) => (
              <RadioGroup
                {...group}
                value={goal}
                onValueChange={(value) => {
                  setGoal(value === 'quick_take' ? 'quick_take' : 'pov');
                }}
              >
                <div className="flex items-start gap-3">
                  <RadioGroupItem id={`${goalId}pov`} value="pov" className="mt-1" />
                  <div className="grid gap-0.5">
                    <Label htmlFor={`${goalId}pov`}>Save it to my POV bank</Label>
                    <p className="text-sm text-ink-muted">The planner and the writer draw on it for future posts.</p>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <RadioGroupItem id={`${goalId}quick`} value="quick_take" className="mt-1" />
                  <div className="grid gap-0.5">
                    <Label htmlFor={`${goalId}quick`}>Save it and start a quick take</Label>
                    <p className="text-sm text-ink-muted">
                      {`A ${lanes.quickTake.minChars.toLocaleString()}–${lanes.quickTake.maxChars.toLocaleString()} character post from your take, out within ${String(lanes.quickTake.maxAgeHours)} hours. You approve it first.`}
                    </p>
                  </div>
                </div>
              </RadioGroup>
            )}
          </Fieldset>
          {povId && error ? (
            <p className="text-sm text-ink-muted">Your take is saved. Only the quick take still needs to start.</p>
          ) : null}
          {error ? <SaveProblem error={error} confidential={confidential.length > 0} onRetry={() => void save()} /> : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="secondary" disabled={saving} onClick={onClose}>
              Cancel
            </Button>
            <Button disabled={saving} onClick={() => void save()}>
              {saving ? 'Saving…' : quickTake ? 'Save and start the quick take' : 'Save my take'}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SaveProblem({ error, confidential, onRetry }: { error: unknown; confidential: boolean; onRetry: () => void }) {
  if (isProblem(error, ['ai_use_not_confirmed', 'blocklist_not_configured'])) {
    const ai = isProblem(error, 'ai_use_not_confirmed');
    return (
      <Alert variant="warn">
        <AlertTitle>{ai ? 'Confirm your employer’s AI-use policy first' : 'Save your blocklist first'}</AlertTitle>
        <AlertDescription>
          <p>
            {ai
              ? 'No model writes from your take until the AI-use policy is confirmed. Your take is kept in this sheet.'
              : 'Takes are checked against your blocklist before they are saved. An empty list is fine.'}
          </p>
        </AlertDescription>
        <AlertActions>
          <Button asChild size="sm" variant="secondary">
            <Link to={POLICY_SETTINGS_PATH}>
              <Settings2 aria-hidden="true" />
              Open Blocklist & policy
            </Link>
          </Button>
        </AlertActions>
      </Alert>
    );
  }
  if (confidential) {
    return (
      <Alert variant="error">
        <AlertTitle>The wording matches your blocklist</AlertTitle>
        <AlertDescription>
          <p>Change the wording and use it again: the term itself is never shown or sent anywhere.</p>
        </AlertDescription>
      </Alert>
    );
  }
  return <ProblemAlert error={error} title="Couldn’t save your take." onRetry={onRetry} retryLabel="Try saving the take again" />;
}
