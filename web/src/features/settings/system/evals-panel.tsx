import { CircleAlert, CircleCheck, CircleX, FlaskConical, Pencil, Plus, Trash2, X } from 'lucide-react';
import { useState, type SubmitEvent } from 'react';
import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, Fieldset } from '@/components/ui/field';
import { Progress } from '@/components/ui/progress';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { Versioned } from '@/lib/api/envelope';
import { confidentialFindings, fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import { useApiInfiniteQuery, useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { PillarBadge } from '@/components/ui/pillar-badge';

import { LiveJob } from '../components/live-job';
import { BlockLoading, SubHeading } from '../components/settings-block';
import { formatDateTime, formatNumber, formatPercent } from '../format';
import { settingsKeys, useFormatPacks, usePillars, type FormatPack, type Pillar, type Schemas } from '../queries';

type EvalTopic = Schemas['EvalTopic'];
type EvalResult = Schemas['EvalResult'];
type EvalLabel = Schemas['EvalLabel'];

const EVAL_STEPS: Readonly<Record<string, string>> = {
  brief: 'Research a brief per topic',
  draft: 'Draft each topic with the real pipeline',
  judge: 'Judge each draft',
  notify: 'Tell you the drafts are ready',
};

const LABELS: readonly { value: EvalLabel; label: string }[] = [
  { value: 'as_is', label: 'As is' },
  { value: 'needs_edits', label: 'Needs edits' },
  { value: 'reject', label: 'Reject' },
];

/** Why a topic has no draft to label (`analytics.eval` codes). */
function resultErrorSentence(code: string): string {
  switch (code) {
    case 'blocklist_hit':
      return 'Its research touched your blocklist, so it stopped.';
    case 'brief_missing':
    case 'not_drafted':
      return 'No brief could be researched for it.';
    case 'pipeline_failed':
      return 'The drafting pipeline failed for it.';
    case 'topic_missing':
      return 'The topic was deleted during the run.';
    case 'judge_missing':
      return 'The judge couldn’t score it.';
    case 'cancelled':
      return 'The run was cancelled before it got here.';
    default:
      return `It didn’t finish (${code}).`;
  }
}

function errorCodeOf(result: EvalResult): string | undefined {
  return result.errorCode ?? undefined;
}

// ------------------------------------------------------------------------------------------------
// Topics
// ------------------------------------------------------------------------------------------------

interface TopicTarget {
  pillar: Pillar;
  format: FormatPack;
  topic?: Versioned<EvalTopic>;
}

function TopicDialog({ target, onClose }: { target: TopicTarget; onClose: () => void }) {
  const existing = target.topic;
  const [text, setText] = useState(existing?.item.topic ?? '');
  const [active, setActive] = useState(existing?.item.active ?? true);
  const [refreshBrief, setRefreshBrief] = useState(false);
  const refresh = { invalidates: [settingsKeys.evalTopics] } as const;
  const create = useApiMutation('post', '/api/evals/topics', {
    ...refresh,
    successToast: 'Topic added.',
    errorToast: 'Couldn’t add the topic.',
    handles: ['validation_failed', 'topic_too_short', 'confidential_text', 'blocklist_not_configured', 'too_many_topics'],
  });
  const update = useApiMutation('put', '/api/evals/topics/{topic_id}', {
    ...refresh,
    successToast: 'Topic saved.',
    errorToast: 'Couldn’t save the topic.',
    handles: ['validation_failed', 'topic_too_short', 'confidential_text', 'blocklist_not_configured', 'too_many_topics'],
  });
  const mutation = existing ? update : create;
  const error = mutation.error;
  const topicError =
    fieldErrors(error).topic ??
    (isProblem(error, 'topic_too_short') ? 'Describe the topic in a few words.' : undefined) ??
    (confidentialFindings(error).length > 0 ? 'This names something on your blocklist. Rephrase it.' : undefined);
  const formError =
    error && !topicError && isProblem(error, ['blocklist_not_configured', 'too_many_topics']) ? problemMessage(error) : undefined;

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body = { pillar: target.pillar.id, format: target.format.id, topic: text.trim(), active };
    if (existing) {
      update.mutate(
        { params: { path: { topic_id: existing.item.id } }, body: { ...body, refreshBrief }, ifMatch: existing.etag },
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{existing ? 'Edit the topic' : 'Add a topic'}</DialogTitle>
          <DialogDescription>{`${target.pillar.name}, ${target.format.name}. It becomes a research topic, so it is checked against your blocklist.`}</DialogDescription>
        </DialogHeader>
        <form noValidate onSubmit={submit} className="grid gap-5" aria-label="Evaluation topic">
          <Field label="Topic" error={topicError} required>
            <Textarea
              value={text}
              onChange={(event) => {
                if (mutation.isError) mutation.reset();
                setText(event.target.value);
              }}
            />
          </Field>
          <Field orientation="horizontal" label="Include it in runs">
            <Switch checked={active} onCheckedChange={setActive} />
          </Field>
          {existing ? (
            <Field
              orientation="horizontal"
              label="Research it again on the next run"
              description="Changing the topic does this anyway."
            >
              <Checkbox
                checked={refreshBrief}
                onCheckedChange={(checked) => {
                  setRefreshBrief(checked === true);
                }}
              />
            </Field>
          ) : null}
          {formError ? (
            <p role="alert" className="text-sm font-medium text-danger">
              {formError}
            </p>
          ) : mutation.isError && !topicError ? (
            <ProblemAlert error={error} />
          ) : null}
          <DialogFooter>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending || text.trim() === ''}>
              {mutation.isPending ? 'Saving…' : existing ? 'Save the topic' : 'Add the topic'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteTopic({ topic, onDone }: { topic: Versioned<EvalTopic>; onDone: () => void }) {
  const remove = useApiMutation('delete', '/api/evals/topics/{topic_id}', {
    invalidates: [settingsKeys.evalTopics],
    successToast: 'Topic deleted. Its earlier results stay.',
    errorToast: 'Couldn’t delete the topic.',
  });
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open) onDone();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this topic?</AlertDialogTitle>
          <AlertDialogDescription>{`“${topic.item.topic}” leaves the evaluation set. Results already labelled stay.`}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep it</AlertDialogCancel>
          <AlertDialogAction
            variant="danger"
            onClick={() => {
              remove.mutate({ params: { path: { topic_id: topic.item.id } }, ifMatch: topic.etag }, { onSettled: onDone });
            }}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function TopicGrid({
  pillars,
  formats,
  topics,
}: {
  pillars: readonly Pillar[];
  formats: readonly FormatPack[];
  topics: readonly Versioned<EvalTopic>[];
}) {
  const [editing, setEditing] = useState<TopicTarget | undefined>();
  const [deleting, setDeleting] = useState<Versioned<EvalTopic> | undefined>();
  const find = (pillar: string, format: string) =>
    topics.find((entry) => entry.item.pillar === pillar && entry.item.format === format);
  const placed = new Set(
    pillars.flatMap((pillar) => formats.map((format) => find(pillar.id, format.id)?.item.id)).filter(Boolean),
  );
  const others = topics.filter((entry) => !placed.has(entry.item.id));

  return (
    <div className="grid gap-8">
      {pillars.map((pillar) => (
        <section key={pillar.id} aria-label={`Topics for ${pillar.name}`} className="grid gap-2">
          <div>
            <PillarBadge id={pillar.id} name={pillar.name} />
          </div>
          <ul className="border-t border-line">
            {formats.map((format) => {
              const topic = find(pillar.id, format.id);
              return (
                <li
                  key={format.id}
                  className="grid gap-x-6 gap-y-1 border-b border-line py-3 sm:grid-cols-[12rem_minmax(0,1fr)_auto] sm:items-center"
                >
                  <span className="text-sm font-medium text-ink-muted">{format.name}</span>
                  <span className={topic ? 'text-ink' : 'text-ink-subtle'}>
                    {topic ? topic.item.topic : 'No topic yet'}
                    {topic && !topic.item.active ? (
                      <Badge variant="neutral" className="ml-2 align-middle">
                        Left out of runs
                      </Badge>
                    ) : null}
                  </span>
                  <span className="flex gap-1 sm:justify-self-end">
                    <Button
                      size="sm"
                      variant={topic ? 'ghost' : 'secondary'}
                      aria-label={topic ? `Edit the ${pillar.name} ${format.name} topic` : `Add a ${pillar.name} ${format.name} topic`}
                      onClick={() => {
                        setEditing({ pillar, format, topic });
                      }}
                    >
                      {topic ? <Pencil aria-hidden="true" /> : <Plus aria-hidden="true" />}
                      {topic ? 'Edit' : 'Add'}
                    </Button>
                    {topic ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Delete the ${pillar.name} ${format.name} topic`}
                        onClick={() => {
                          setDeleting(topic);
                        }}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {others.length > 0 ? (
        <section aria-label="Other topics" className="grid gap-2">
          <p className="text-sm font-medium text-ink-muted">
            Topics on a pillar or format that no longer exists (rename them, or delete them)
          </p>
          <ul className="border-t border-line">
            {others.map((entry) => (
              <li key={entry.item.id} className="flex items-center justify-between gap-3 border-b border-line py-3">
                <span className="text-ink">{entry.item.topic}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Delete the topic “${entry.item.topic}”`}
                  onClick={() => {
                    setDeleting(entry);
                  }}
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {editing ? (
        <TopicDialog
          key={`${editing.pillar.id}-${editing.format.id}`}
          target={editing}
          onClose={() => {
            setEditing(undefined);
          }}
        />
      ) : null}
      {deleting ? (
        <DeleteTopic
          topic={deleting}
          onDone={() => {
            setDeleting(undefined);
          }}
        />
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------
// Results and labels
// ------------------------------------------------------------------------------------------------

function ResultRow({ entry, topic }: { entry: Versioned<EvalResult>; topic: EvalTopic | undefined }) {
  const result = entry.item;
  const label = useApiMutation('post', '/api/evals/results/{result_id}/label', {
    updates: (data) => queryKeys.evalResults.detail(data.item.id),
    invalidates: [queryKeys.evalResults.lists(), settingsKeys.autonomy],
    successToast: 'Label saved.',
    errorToast: 'Couldn’t save the label.',
  });
  const code = errorCodeOf(result);
  const name = topic?.topic ?? 'A deleted topic';
  const ready = !!result.articleId;
  return (
    <li className="grid gap-3 border-b border-line py-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          {topic ? <PillarBadge id={topic.pillar} /> : null}
          <p className="font-medium text-ink">{name}</p>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
          {result.judgePassed === true ? (
            <span className="inline-flex items-center gap-1">
              <CircleCheck aria-hidden="true" className="size-4 text-success" />
              Judge passed
            </span>
          ) : result.judgePassed === false ? (
            <span className="inline-flex items-center gap-1">
              <CircleX aria-hidden="true" className="size-4 text-danger" />
              Judge failed
            </span>
          ) : (
            <span className="inline-flex items-center gap-1">
              <CircleAlert aria-hidden="true" className="size-4 text-warn" />
              Not judged
            </span>
          )}
          {typeof result.judgeScore === 'number' ? <span>{`Score ${formatNumber(result.judgeScore)}`}</span> : null}
          {result.articleId ? (
            <Link
              to={`/studio/${result.articleId}/draft`}
              className="text-ink underline decoration-line-strong underline-offset-4"
            >
              Read the draft
            </Link>
          ) : null}
        </div>
        {code ? <p className="mt-1 text-sm text-ink-muted">{resultErrorSentence(code)}</p> : null}
      </div>
      {ready ? (
        <Fieldset legend={`Your label for “${name}”`} hideLegend>
          {(group) => (
            <SegmentedControl
              {...group}
              value={result.ownerLabel ?? ''}
              onValueChange={(value) => {
                const next = LABELS.find((item) => item.value === value);
                if (next) label.mutate({ params: { path: { result_id: result.id } }, body: { label: next.value }, ifMatch: entry.etag });
              }}
              className="pointer-coarse:w-full"
            >
              {LABELS.map((item) => (
                <SegmentedControlItem key={item.value} value={item.value} disabled={label.isPending}>
                  {item.label}
                </SegmentedControlItem>
              ))}
            </SegmentedControl>
          )}
        </Fieldset>
      ) : null}
    </li>
  );
}

function Results({ topics }: { topics: readonly Versioned<EvalTopic>[] }) {
  const results = useApiInfiniteQuery({
    queryKey: settingsKeys.evalResults({}),
    path: '/api/evals/results',
    pageSize: 50,
  });
  const autonomy = useApiQuery({ queryKey: settingsKeys.autonomy, path: '/api/kb/autonomy' });
  if (results.isPending) return <BlockLoading label="Loading the evaluation results" />;
  if (results.isError) {
    return (
      <ProblemAlert
        error={results.error}
        title="Couldn’t load the evaluation results."
        onRetry={() => void results.refetch()}
        retryLabel="Try loading the evaluation results again"
      />
    );
  }
  const all = results.data;
  const latestRun = all[0]?.item.runId;
  if (!latestRun) {
    return <p className="text-ink-muted">No run yet. Run the evaluation once the topics are in place.</p>;
  }
  const run = all.filter((entry) => entry.item.runId === latestRun);
  const ready = run.filter((entry) => !!entry.item.articleId);
  const labelled = ready.filter((entry) => !!entry.item.ownerLabel).length;
  const judge = autonomy.data?.settings.item.judge;
  const topicById = new Map(topics.map((entry) => [entry.item.id, entry.item]));
  const startedAt = run.reduce((first, entry) => (entry.item.createdAt < first ? entry.item.createdAt : first), run[0]?.item.createdAt ?? '');

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-4">
        <Progress
          value={labelled}
          max={Math.max(ready.length, 1)}
          tone={labelled === ready.length && ready.length > 0 ? 'success' : 'ink'}
          aria-label="Drafts labelled"
          getValueLabel={() => `${String(labelled)} of ${String(ready.length)} labelled`}
          className="max-w-48"
        />
        <p className="text-sm text-ink-muted">{`${String(labelled)} of ${String(ready.length)} drafts labelled, run of ${formatDateTime(startedAt)}`}</p>
      </div>
      {judge && typeof judge.asIsRate === 'number' ? (
        <p className="max-w-measure text-sm text-ink-muted">
          {`Judge agreement: of the drafts it passed, you labelled ${formatPercent(judge.asIsRate)} “as is” (needs ${formatPercent(judge.threshold)} before auto-publish can turn on).`}
        </p>
      ) : null}
      <ul aria-label="Evaluation drafts" className="max-w-4xl border-t border-line">
        {run.map((entry) => (
          <ResultRow key={entry.item.id} entry={entry} topic={topicById.get(entry.item.topicId)} />
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------
// The panel
// ------------------------------------------------------------------------------------------------

/**
 * The evaluation set (plan: "Verification"): 12 topics (four formats by three pillars), a run that
 * drafts each with the real pipeline and judges it with gate 1, and the owner's labels, which
 * calibrate the judge auto-publish depends on.
 */
export function EvalsPanel() {
  const pillars = usePillars();
  const formats = useFormatPacks();
  const topics = useApiInfiniteQuery({ queryKey: settingsKeys.evalTopics, path: '/api/evals/topics', pageSize: 50 });
  const [jobId, setJobId] = useState<string | undefined>();
  const run = useApiMutation('post', '/api/evals/runs', {
    invalidates: [queryKeys.jobs.all],
    successToast: 'Evaluation run started. You get a notification when the drafts are ready to label.',
    errorToast: 'Couldn’t start the evaluation run.',
    handles: ['voice_guide_missing', 'no_eval_topics', 'blocklist_not_configured', 'ai_use_not_confirmed'],
  });
  const refusal = isProblem(run.error, ['voice_guide_missing', 'no_eval_topics', 'blocklist_not_configured', 'ai_use_not_confirmed'])
    ? run.error
    : undefined;

  if (pillars.isPending || formats.isPending || topics.isPending) return <BlockLoading label="Loading the evaluation set" />;
  const failed = pillars.error ?? formats.error ?? topics.error;
  if (failed) {
    return (
      <ProblemAlert
        error={failed}
        title="Couldn’t load the evaluation set."
        onRetry={() => {
          void pillars.refetch();
          void formats.refetch();
          void topics.refetch();
        }}
        retryLabel="Try loading the evaluation set again"
      />
    );
  }
  const pillarList = pillars.data?.item.pillars ?? [];
  const formatList = formats.data?.item.packs ?? [];
  const topicList = topics.data ?? [];
  const active = topicList.filter((entry) => entry.item.active).length;
  const expected = pillarList.length * formatList.length;

  return (
    <div className="grid gap-10">
      <div className="grid max-w-measure gap-3">
        <p className="text-ink-muted">
          {`${String(active)} active ${active === 1 ? 'topic' : 'topics'}${expected > 0 ? ` of the ${String(expected)} the set calls for` : ''}.`}{' '}
          A run drafts each one the way real articles are drafted, then the judge scores it. Label each draft: the labels tell
          how far the judge can be trusted.
        </p>
        <Button
          className="justify-self-start"
          disabled={run.isPending || active === 0}
          onClick={() => {
            run.mutate(undefined, {
              onSuccess: (accepted) => {
                setJobId(accepted.jobId);
              },
            });
          }}
        >
          <FlaskConical aria-hidden="true" />
          {run.isPending ? 'Starting…' : 'Run the evaluation'}
        </Button>
        {refusal ? (
          <Alert variant="error">
            <AlertTitle>The run can’t start yet.</AlertTitle>
            <AlertDescription>
              <p>
                {isProblem(refusal, 'no_eval_topics')
                  ? 'Add the topics first.'
                  : isProblem(refusal, 'voice_guide_missing')
                    ? 'Activate a voice guide first, in Library > Voice guide: the drafts are written with it.'
                    : problemMessage(refusal)}
              </p>
            </AlertDescription>
          </Alert>
        ) : null}
        {jobId ? (
          <div className="flex items-start justify-between gap-4 rounded-lg border border-line p-4">
            <LiveJob jobId={jobId} title="Evaluation run" labels={EVAL_STEPS} />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Hide the evaluation progress"
              onClick={() => {
                setJobId(undefined);
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </div>
        ) : null}
      </div>

      <div className="grid gap-4">
        <SubHeading>Topics</SubHeading>
        <TopicGrid pillars={pillarList} formats={formatList} topics={topicList} />
      </div>

      <div className="grid gap-4">
        <SubHeading>Latest run</SubHeading>
        <Results topics={topicList} />
      </div>
    </div>
  );
}
