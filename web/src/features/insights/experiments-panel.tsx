import { Ban, CircleCheck, CircleDot, CirclePlay, FlaskConical, ListPlus, Pencil, Plus, Trash2 } from 'lucide-react';
import { useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { Versioned } from '@/lib/api/envelope';
import { ApiError } from '@/lib/api/errors';
import { fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { BlockLoading } from '../settings/components/settings-block';
import { StateBadge, type StateLook } from '../settings/components/status-badge';
import { useNow } from '@/lib/use-now';
import { formatDate, formatRelative } from '../settings/format';
import { machineAllows, useMachines } from '../settings/queries';
import { METRICS, QUESTIONS, metricLabel, questionLabel } from './labels';
import { insightsKeys, usePostedPosts, type Experiment, type ExperimentStatus } from './queries';

type ExperimentIn = components['schemas']['ExperimentIn'];
type Question = Experiment['question'];
type Metric = Experiment['metric'];

const MIN_PER_ARM = 8;
const STATUS_LOOKS: Readonly<Record<ExperimentStatus, StateLook>> = {
  draft: { label: 'Not started', tone: 'neutral', icon: CircleDot },
  running: { label: 'Running', tone: 'success', icon: CirclePlay },
  concluded: { label: 'Concluded', tone: 'outline', icon: CircleCheck },
  abandoned: { label: 'Abandoned', tone: 'neutral', icon: Ban },
};
const STATUS_ORDER: readonly ExperimentStatus[] = ['running', 'draft', 'concluded', 'abandoned'];

/** An arm id from its label: lowercase letters, digits and dashes, unique in the experiment. */
export function armIds(labels: readonly string[]): string[] {
  const used = new Set<string>();
  return labels.map((label, index) => {
    const base =
      label
        .toLocaleLowerCase()
        .normalize('NFKD')
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 32) || `arm-${String(index + 1)}`;
    let id = base;
    for (let suffix = 2; used.has(id); suffix += 1) id = `${base}-${String(suffix)}`;
    used.add(id);
    return id;
  });
}

const refresh = { invalidates: [queryKeys.experiments.lists()] } as const;

function ExperimentDialog({ experiment, onClose }: { experiment?: Versioned<Experiment>; onClose: () => void }) {
  const current = experiment?.item;
  const [question, setQuestion] = useState<Question>(current?.question ?? 'links_vs_none');
  const [hypothesis, setHypothesis] = useState(current?.hypothesis ?? '');
  const [metric, setMetric] = useState<Metric>(current?.metric ?? 'engagement_rate');
  const [arms, setArms] = useState<string[]>(current?.arms.map((arm) => arm.label) ?? [...(QUESTIONS[0]?.arms ?? ['A', 'B'])]);
  const [minPosts, setMinPosts] = useState(String(current?.minPostsPerArm ?? MIN_PER_ARM));
  const [attempted, setAttempted] = useState(false);
  const create = useApiMutation('post', '/api/analytics/experiments', {
    ...refresh,
    successToast: 'Experiment written down. Start it when you are ready.',
    errorToast: 'Couldn’t save the experiment.',
    handles: ['validation_failed'],
  });
  const update = useApiMutation('put', '/api/analytics/experiments/{experiment_id}', {
    ...refresh,
    updates: (data) => queryKeys.experiments.detail(data.item.id),
    successToast: 'Experiment saved.',
    errorToast: 'Couldn’t save the experiment.',
    handles: ['validation_failed'],
  });
  const mutation = experiment ? update : create;
  const serverErrors = fieldErrors(mutation.error);
  const minimum = Number(minPosts);
  const labelsOk = arms.every((label) => label.trim() !== '' && label.trim().length <= 120);
  const errors = {
    hypothesis:
      (attempted && hypothesis.trim() === '' ? 'Write down what you expect to happen.' : undefined) ??
      (hypothesis.length > 500 ? 'At most 500 characters.' : undefined) ??
      serverErrors.hypothesis,
    arms: (attempted && !labelsOk ? 'Name every arm (at most 120 characters).' : undefined) ?? serverErrors.arms,
    minPosts:
      (!Number.isInteger(minimum) || minimum < MIN_PER_ARM || minimum > 100
        ? `A whole number from ${String(MIN_PER_ARM)} to 100.`
        : undefined) ?? serverErrors.minPostsPerArm,
  };

  const pickQuestion = (value: string) => {
    const preset = QUESTIONS.find((item) => item.value === value);
    if (!preset) return;
    // A new question brings its usual arms, unless the owner already named their own.
    const untouched = arms.every((label, index) => QUESTIONS.some((item) => item.arms[index] === label));
    setQuestion(preset.value);
    if (untouched) setArms([...preset.arms]);
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAttempted(true);
    if (hypothesis.trim() === '' || !labelsOk || errors.minPosts) return;
    const ids = armIds(arms);
    const body: ExperimentIn = {
      question,
      hypothesis: hypothesis.trim(),
      metric,
      minPostsPerArm: minimum,
      arms: arms.map((label, index) => ({
        id: current?.arms[index]?.id ?? ids[index] ?? `arm-${String(index + 1)}`,
        label: label.trim(),
        postIds: current?.arms[index]?.postIds ?? [],
      })),
    };
    if (experiment) {
      update.mutate(
        { params: { path: { experiment_id: experiment.item.id } }, body, ifMatch: experiment.etag },
        { onSuccess: onClose },
      );
    } else {
      create.mutate({ body }, { onSuccess: onClose });
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{experiment ? 'Edit the experiment' : 'Write down an experiment'}</DialogTitle>
          <DialogDescription>
            Decide the question, what you expect and how you will measure it before any post goes out. One runs at a time,
            with at least {String(MIN_PER_ARM)} measured posts per arm.
          </DialogDescription>
        </DialogHeader>
        <form noValidate onSubmit={submit} className="grid gap-5" aria-label="Experiment">
          <Fieldset legend="Question">
            {(group) => (
              <RadioGroup {...group} value={question} onValueChange={pickQuestion}>
                {QUESTIONS.map((item) => (
                  <div key={item.value} className="flex items-center gap-3">
                    <RadioGroupItem id={`question-${item.value}`} value={item.value} />
                    <Label htmlFor={`question-${item.value}`}>{item.label}</Label>
                  </div>
                ))}
              </RadioGroup>
            )}
          </Fieldset>
          <Field label="What you expect" description="One or two sentences you can be wrong about." error={errors.hypothesis} required>
            <Textarea
              value={hypothesis}
              onChange={(event) => {
                setHypothesis(event.target.value);
              }}
            />
          </Field>
          <Field label="Measured by">
            {(control) => (
              <Select
                value={metric}
                onValueChange={(value) => {
                  const next = METRICS.find((item) => item.value === value);
                  if (next) setMetric(next.value);
                }}
              >
                <SelectTrigger {...control}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {METRICS.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>
          <Fieldset legend="Arms" description="Two to four. Each post you assign goes in one arm." error={errors.arms}>
            <div className="grid gap-2">
              {arms.map((label, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Field label={`Arm ${String(index + 1)}`} hideLabel className="flex-1">
                    <Input
                      value={label}
                      onChange={(event) => {
                        const next = [...arms];
                        next[index] = event.target.value;
                        setArms(next);
                      }}
                    />
                  </Field>
                  {arms.length > 2 ? (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove arm ${String(index + 1)}`}
                      onClick={() => {
                        setArms(arms.filter((_, position) => position !== index));
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  ) : null}
                </div>
              ))}
              {arms.length < 4 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="justify-self-start"
                  onClick={() => {
                    setArms([...arms, `Arm ${String(arms.length + 1)}`]);
                  }}
                >
                  <Plus aria-hidden="true" />
                  Add an arm
                </Button>
              ) : null}
            </div>
          </Fieldset>
          <Field label="Measured posts per arm before it can conclude" error={errors.minPosts}>
            <Input
              type="number"
              inputMode="numeric"
              min={MIN_PER_ARM}
              max={100}
              value={minPosts}
              className="max-w-28"
              onChange={(event) => {
                setMinPosts(event.target.value);
              }}
            />
          </Field>
          {mutation.isError && !isProblem(mutation.error, 'validation_failed') ? <ProblemAlert error={mutation.error} /> : null}
          <DialogFooter>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? 'Saving…' : experiment ? 'Save the experiment' : 'Write it down'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AssignDialog({ experiment, onClose }: { experiment: Versioned<Experiment>; onClose: () => void }) {
  const now = useNow();
  const posts = usePostedPosts();
  const [assignment, setAssignment] = useState<Record<string, string>>(() =>
    Object.fromEntries(experiment.item.arms.flatMap((arm) => (arm.postIds ?? []).map((postId) => [postId, arm.id]))),
  );
  const save = useApiMutation('put', '/api/analytics/experiments/{experiment_id}', {
    ...refresh,
    updates: (data) => queryKeys.experiments.detail(data.item.id),
    successToast: 'Posts assigned.',
    errorToast: 'Couldn’t assign the posts.',
  });
  const NONE = 'none';
  const listed = (posts.data ?? []).map((entry) => entry.item);
  const unknown = Object.keys(assignment).filter((id) => !listed.some((post) => post.id === id));
  const send = () => {
    const item = experiment.item;
    save.mutate(
      {
        params: { path: { experiment_id: item.id } },
        body: {
          question: item.question,
          hypothesis: item.hypothesis,
          metric: item.metric,
          minPostsPerArm: item.minPostsPerArm,
          arms: item.arms.map((arm) => ({
            id: arm.id,
            label: arm.label,
            postIds: Object.entries(assignment)
              .filter(([, armId]) => armId === arm.id)
              .map(([postId]) => postId),
          })),
        },
        ifMatch: experiment.etag,
      },
      { onSuccess: onClose },
    );
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Assign posts to the arms</DialogTitle>
          <DialogDescription>
            Your newest live posts. A post counts once it has a snapshot with impressions.
          </DialogDescription>
        </DialogHeader>
        {posts.isPending ? (
          <BlockLoading label="Loading your live posts" />
        ) : posts.isError ? (
          <ProblemAlert error={posts.error} title="Couldn’t load your live posts." onRetry={() => void posts.refetch()} />
        ) : listed.length === 0 ? (
          <p className="text-ink-muted">No post is live yet.</p>
        ) : (
          <ul aria-label="Live posts" className="border-t border-line">
            {listed.map((post) => (
              <li key={post.id} className="grid gap-2 border-b border-line py-3 sm:grid-cols-[minmax(0,1fr)_12rem] sm:items-center">
                <div className="min-w-0">
                  <p className="truncate text-ink">{post.hook}</p>
                  <p className="text-xs text-ink-subtle">{post.postedAt ? `Live ${formatRelative(post.postedAt, now)}` : 'Live'}</p>
                </div>
                <Field label={`Arm for “${post.hook}”`} hideLabel>
                  {(control) => (
                    <Select
                      value={assignment[post.id] ?? NONE}
                      onValueChange={(value) => {
                        setAssignment((current) => {
                          const rest = Object.fromEntries(Object.entries(current).filter(([id]) => id !== post.id));
                          return value === NONE ? rest : { ...rest, [post.id]: value };
                        });
                      }}
                    >
                      <SelectTrigger {...control} className="h-9 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>Not in this experiment</SelectItem>
                        {experiment.item.arms.map((arm) => (
                          <SelectItem key={arm.id} value={arm.id}>
                            {arm.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </Field>
              </li>
            ))}
          </ul>
        )}
        {unknown.length > 0 ? (
          <p className="text-sm text-ink-muted">{`${String(unknown.length)} older ${unknown.length === 1 ? 'post stays' : 'posts stay'} assigned as before.`}</p>
        ) : null}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={save.isPending} onClick={send}>
            {save.isPending ? 'Saving…' : 'Save the assignment'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The arm counts a 409 `too_few_posts` carries (`{required, arms: [{id, posts}]}`). */
function shortArms(error: unknown, experiment: Experiment): string | undefined {
  if (!(error instanceof ApiError) || !isProblem(error, 'too_few_posts')) return undefined;
  const arms = error.problem?.extensions?.arms;
  if (!Array.isArray(arms)) return undefined;
  const parts = arms.flatMap((entry: unknown) => {
    if (typeof entry !== 'object' || entry === null) return [];
    const { id, posts } = entry as { id?: unknown; posts?: unknown };
    if (typeof id !== 'string' || typeof posts !== 'number') return [];
    const label = experiment.arms.find((arm) => arm.id === id)?.label ?? id;
    return [`${label}: ${String(posts)}`];
  });
  return parts.length > 0 ? `Measured posts so far: ${parts.join(', ')}.` : undefined;
}

function TransitionDialog({
  experiment,
  to,
  onClose,
}: {
  experiment: Versioned<Experiment>;
  to: 'running' | 'concluded' | 'abandoned';
  onClose: () => void;
}) {
  const [note, setNote] = useState('');
  const transition = useApiMutation('post', '/api/analytics/experiments/{experiment_id}/transition', {
    ...refresh,
    updates: (data) => queryKeys.experiments.detail(data.item.id),
    successToast:
      to === 'running' ? 'Experiment started.' : to === 'concluded' ? 'Experiment concluded.' : 'Experiment abandoned.',
    errorToast: 'Couldn’t change the experiment.',
    handles: ['experiment_running', 'too_few_posts'],
  });
  const copy = {
    running: {
      title: 'Start this experiment?',
      text: 'From now on assign each relevant post to an arm. Its question, expectation, measure and arms are fixed while it runs.',
      action: 'Start it',
    },
    concluded: {
      title: 'Conclude this experiment?',
      text: `Every arm needs ${String(experiment.item.minPostsPerArm)} measured posts. Without a note, the conclusion is a numbers-only summary.`,
      action: 'Conclude it',
    },
    abandoned: {
      title: 'Abandon this experiment?',
      text: 'It stops without a conclusion and frees the slot for another one.',
      action: 'Abandon it',
    },
  }[to];
  const refusal = isProblem(transition.error, ['experiment_running', 'too_few_posts']) ? transition.error : undefined;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>{copy.text}</DialogDescription>
        </DialogHeader>
        {to === 'running' ? null : (
          <Field label={to === 'concluded' ? 'What you learned (optional)' : 'Why (optional)'}>
            <Textarea
              value={note}
              maxLength={2000}
              onChange={(event) => {
                setNote(event.target.value);
              }}
            />
          </Field>
        )}
        {refusal ? (
          <Alert variant="error">
            <AlertTitle>{to === 'running' ? 'Another experiment is running.' : 'Not enough measured posts yet.'}</AlertTitle>
            <AlertDescription>
              <p>{problemMessage(refusal)}</p>
              {shortArms(refusal, experiment.item) ? <p>{shortArms(refusal, experiment.item)}</p> : null}
            </AlertDescription>
          </Alert>
        ) : null}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={to === 'abandoned' ? 'danger' : 'primary'}
            disabled={transition.isPending}
            onClick={() => {
              transition.mutate(
                {
                  params: { path: { experiment_id: experiment.item.id } },
                  body: { to, conclusion: note.trim() === '' ? null : note.trim() },
                  ifMatch: experiment.etag,
                },
                { onSuccess: onClose },
              );
            }}
          >
            {transition.isPending ? 'Saving…' : copy.action}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type Open =
  | { kind: 'create' }
  | { kind: 'edit'; experiment: Versioned<Experiment> }
  | { kind: 'assign'; experiment: Versioned<Experiment> }
  | { kind: 'transition'; experiment: Versioned<Experiment>; to: 'running' | 'concluded' | 'abandoned' };

/**
 * Pre-registered experiments, one at a time: links against none, slot timing, carousel against
 * image card, or the owner's own question, each with at least eight measured posts per arm.
 */
export function ExperimentsPanel() {
  const now = useNow();
  const machines = useMachines();
  const [open, setOpen] = useState<Open | undefined>();
  const experiments = useApiInfiniteQuery({
    queryKey: insightsKeys.experiments(),
    path: '/api/analytics/experiments',
    pageSize: 20,
  });
  const close = () => {
    setOpen(undefined);
  };

  const list = [...(experiments.data ?? [])].sort(
    (a, b) => STATUS_ORDER.indexOf(a.item.status) - STATUS_ORDER.indexOf(b.item.status),
  );
  const running = list.find((entry) => entry.item.status === 'running');
  const allows = (status: ExperimentStatus, to: ExperimentStatus) =>
    machineAllows(machines.data, 'experiment', status, to) ??
    ((status === 'draft' && (to === 'running' || to === 'abandoned')) ||
      (status === 'running' && (to === 'concluded' || to === 'abandoned')));

  return (
    <div className="grid gap-5">
      <Button
        className="justify-self-start"
        onClick={() => {
          setOpen({ kind: 'create' });
        }}
      >
        <FlaskConical aria-hidden="true" />
        Write down an experiment
      </Button>
      {experiments.isPending ? (
        <BlockLoading label="Loading the experiments" />
      ) : experiments.isError ? (
        <ProblemAlert
          error={experiments.error}
          title="Couldn’t load the experiments."
          onRetry={() => void experiments.refetch()}
          retryLabel="Try loading the experiments again"
        />
      ) : list.length === 0 ? (
        <p className="max-w-measure text-ink-muted">
          No experiments yet. A first one worth running: links against no links, with eight posts in each arm.
        </p>
      ) : (
        <ul aria-label="Experiments" className="max-w-4xl border-t border-line">
          {list.map((entry) => {
            const item = entry.item;
            const assigned = item.arms.reduce((sum, arm) => sum + (arm.postIds?.length ?? 0), 0);
            return (
              <li key={item.id} className="grid gap-3 border-b border-line py-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="font-semibold text-ink">{questionLabel(item.question)}</p>
                  <StateBadge look={STATUS_LOOKS[item.status]} />
                </div>
                <blockquote className="max-w-measure border-l-2 border-line-strong pl-4 font-serif text-lg text-ink">
                  {item.hypothesis}
                </blockquote>
                <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
                  <dt className="text-ink-subtle">Measured by</dt>
                  <dd className="text-ink">{metricLabel(item.metric)}</dd>
                  <dt className="text-ink-subtle">Arms</dt>
                  <dd className="text-ink">
                    {item.arms
                      .map((arm) => `${arm.label} (${String(arm.postIds?.length ?? 0)} assigned)`)
                      .join(', ')}
                  </dd>
                  <dt className="text-ink-subtle">Needs</dt>
                  <dd className="text-ink">{`${String(item.minPostsPerArm)} measured posts per arm`}</dd>
                  {item.startedAt ? (
                    <>
                      <dt className="text-ink-subtle">Started</dt>
                      <dd className="text-ink">{`${formatDate(item.startedAt)} (${formatRelative(item.startedAt, now)})`}</dd>
                    </>
                  ) : null}
                  {item.concludedAt ? (
                    <>
                      <dt className="text-ink-subtle">Concluded</dt>
                      <dd className="text-ink">{formatDate(item.concludedAt)}</dd>
                    </>
                  ) : null}
                </dl>
                {item.conclusion ? <p className="max-w-measure text-ink">{item.conclusion}</p> : null}
                <div className="flex flex-wrap gap-2">
                  {item.status === 'draft' ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setOpen({ kind: 'edit', experiment: entry });
                      }}
                    >
                      <Pencil aria-hidden="true" />
                      Edit
                    </Button>
                  ) : null}
                  {allows(item.status, 'running') ? (
                    <Button
                      size="sm"
                      disabled={running !== undefined}
                      onClick={() => {
                        setOpen({ kind: 'transition', experiment: entry, to: 'running' });
                      }}
                    >
                      <CirclePlay aria-hidden="true" />
                      Start
                    </Button>
                  ) : null}
                  {item.status === 'running' ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setOpen({ kind: 'assign', experiment: entry });
                      }}
                    >
                      <ListPlus aria-hidden="true" />
                      {assigned > 0 ? 'Change the posts' : 'Assign posts'}
                    </Button>
                  ) : null}
                  {allows(item.status, 'concluded') ? (
                    <Button
                      size="sm"
                      onClick={() => {
                        setOpen({ kind: 'transition', experiment: entry, to: 'concluded' });
                      }}
                    >
                      <CircleCheck aria-hidden="true" />
                      Conclude
                    </Button>
                  ) : null}
                  {allows(item.status, 'abandoned') ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setOpen({ kind: 'transition', experiment: entry, to: 'abandoned' });
                      }}
                    >
                      <Ban aria-hidden="true" />
                      Abandon
                    </Button>
                  ) : null}
                </div>
                {item.status === 'draft' && running ? (
                  <p className="text-sm text-ink-muted">Conclude or abandon the running experiment before starting this one.</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {experiments.hasNextPage ? (
        <Button
          variant="secondary"
          className="justify-self-start"
          disabled={experiments.isFetchingNextPage}
          onClick={() => void experiments.fetchNextPage()}
        >
          {experiments.isFetchingNextPage ? 'Loading…' : 'Show more experiments'}
        </Button>
      ) : null}
      {open?.kind === 'create' ? <ExperimentDialog onClose={close} /> : null}
      {open?.kind === 'edit' ? <ExperimentDialog experiment={open.experiment} onClose={close} /> : null}
      {open?.kind === 'assign' ? <AssignDialog experiment={open.experiment} onClose={close} /> : null}
      {open?.kind === 'transition' ? <TransitionDialog experiment={open.experiment} to={open.to} onClose={close} /> : null}
    </div>
  );
}
