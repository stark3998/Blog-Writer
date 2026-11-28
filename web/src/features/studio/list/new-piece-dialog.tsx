import { useQueryClient } from '@tanstack/react-query';
import { Clock } from 'lucide-react';
import { useId, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { Link, useNavigate } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { components } from '@/lib/api/schema';
import { fieldErrors, isProblem, problemCode } from '@/lib/api/problems';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { PillarBadge } from '../piece/piece-chips';
import { useFormatPacks, usePillars } from '../piece/piece-queries';
import { LANE_LABEL, POST_FORMAT_LABEL } from '../piece/piece-status';
import { pillarView } from '../piece/pillars';
import type { FormatPack, Pillar } from '../piece/types';

import { matchesSearch } from './list-model';

type Idea = components['schemas']['Idea'];
type PovEntry = components['schemas']['PovEntry'];

type Source = 'idea' | 'blank';
type Make = 'article' | 'post';
/** Polls are written by hand for now (the server answers 422 `unsupported_format`). */
type CreatablePostFormat = 'text' | 'carousel' | 'image_card';

const POST_FORMATS: readonly CreatablePostFormat[] = ['text', 'carousel', 'image_card'];
const IDEA_STATUSES: ('new' | 'accepted')[] = ['new', 'accepted'];
/** `ArticleMetaIn.title` (1-150 characters). */
const TITLE_MAX = 150;

function isPostFormat(value: string | null | undefined): value is CreatablePostFormat {
  return POST_FORMATS.some((format) => format === value);
}

/** Where the owner fixes a refused create. */
function setupFix(error: unknown): { readonly to: string; readonly label: string } | undefined {
  switch (problemCode(error)) {
    case 'ai_use_not_confirmed':
    case 'blocklist_not_configured':
      return { to: '/settings/policy', label: 'Open Blocklist & policy' };
    case 'unknown_pillar':
      return { to: '/library/pillars', label: 'Open Pillars' };
    case 'format_disabled':
      return { to: '/library/pillars', label: 'Open the lane rules' };
    case 'unusable_material':
      return { to: '/library/stories', label: 'Open Stories & POV' };
    case 'owner_take_required':
      return { to: '/calendar/ideas', label: 'Open Ideas' };
    case undefined:
    default:
      return undefined;
  }
}

/** Focuses the first control a failed submit marked invalid (a radio group focuses its chosen or first radio). */
function focusFirstProblem(form: HTMLFormElement | null): void {
  const target = form?.querySelector<HTMLElement>('[aria-invalid="true"]');
  if (!target) return;
  if (target.getAttribute('role') === 'radiogroup') {
    const radio =
      target.querySelector<HTMLElement>('[role="radio"][data-state="checked"]') ??
      target.querySelector<HTMLElement>('[role="radio"]');
    radio?.focus();
    return;
  }
  target.focus();
}

function IdeaPicker({
  value,
  error,
  onSelect,
  onStartBlank,
}: {
  value: string | undefined;
  error: string | undefined;
  onSelect: (idea: Idea) => void;
  onStartBlank: () => void;
}) {
  const [search, setSearch] = useState('');
  const pillars = usePillars();
  const ideas = useApiInfiniteQuery({
    queryKey: queryKeys.ideas.list({ status: IDEA_STATUSES }),
    path: '/api/ideas',
    init: { params: { query: { status: IDEA_STATUSES } } },
    pageSize: 20,
  });
  const all = (ideas.data ?? []).map((entry) => entry.item);
  const shown = all.filter((idea) => matchesSearch(idea, search));

  if (ideas.isPending) {
    return (
      <div className="grid gap-2">
        <p role="status" className="sr-only">
          Loading ideas
        </p>
        <Skeleton className="h-14" />
        <Skeleton className="h-14" />
        <Skeleton className="h-14" />
      </div>
    );
  }
  if (ideas.isError && all.length === 0) {
    return (
      <ProblemAlert
        error={ideas.error}
        title="Couldn’t load your ideas."
        onRetry={() => {
          void ideas.refetch();
        }}
        retryLabel="Try loading the ideas again"
      />
    );
  }
  if (all.length === 0) {
    return (
      <Alert>
        <AlertTitle>Your ideas inbox is empty.</AlertTitle>
        <AlertDescription>
          <p>Capture an idea on Today, or start this piece from a blank page.</p>
        </AlertDescription>
        <AlertActions>
          <Button size="sm" variant="secondary" onClick={onStartBlank}>
            Start from a blank page
          </Button>
        </AlertActions>
      </Alert>
    );
  }

  return (
    <Fieldset legend="Which idea?" error={error} required>
      {(group) => (
        <div className="grid gap-3">
          <Input
            type="search"
            aria-label="Search your ideas"
            placeholder="Search your ideas"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
            }}
          />
          {shown.length === 0 ? (
            <p role="status" className="text-sm text-ink-muted">
              No idea matches that search.
            </p>
          ) : (
            <RadioGroup
              {...group}
              value={value ?? ''}
              onValueChange={(id) => {
                const idea = all.find((item) => item.id === id);
                if (idea) onSelect(idea);
              }}
              className="max-h-72 gap-2 overflow-y-auto rounded-md border border-line p-2"
            >
              {shown.map((idea) => (
                <IdeaOption key={idea.id} idea={idea} pillars={pillars.data?.item.pillars} />
              ))}
            </RadioGroup>
          )}
          {ideas.hasNextPage ? (
            <Button
              size="sm"
              variant="ghost"
              className="justify-self-start"
              disabled={ideas.isFetchingNextPage}
              onClick={() => {
                void ideas.fetchNextPage();
              }}
            >
              {ideas.isFetchingNextPage ? 'Loading more ideas…' : 'Show more ideas'}
            </Button>
          ) : null}
        </div>
      )}
    </Fieldset>
  );
}

function IdeaOption({ idea, pillars }: { idea: Idea; pillars: readonly Pillar[] | undefined }) {
  const id = useId();
  const pillar = pillarView(idea.pillar, pillars);
  const lane =
    idea.suggestedLane === 'flagship'
      ? 'Suggested: article'
      : idea.suggestedLane
        ? `Suggested: ${LANE_LABEL[idea.suggestedLane].toLowerCase()}`
        : undefined;
  return (
    <div className="flex items-start gap-3 rounded-md px-2 py-2.5 has-[[data-state=checked]]:bg-wash-strong">
      <RadioGroupItem id={`${id}radio`} value={idea.id} aria-describedby={`${id}details`} className="mt-1" />
      <div className="grid min-w-0 gap-1">
        <Label htmlFor={`${id}radio`} className="leading-snug">
          {idea.title}
        </Label>
        <div id={`${id}details`} className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
          {pillar ? <PillarBadge pillar={pillar} /> : null}
          {lane ? <span>{lane}</span> : null}
          {idea.timeSensitive ? (
            <Badge variant="warn">
              <Clock aria-hidden="true" />
              Time-sensitive
            </Badge>
          ) : null}
          {idea.angle ? <span className="basis-full">{idea.angle}</span> : null}
        </div>
      </div>
    </div>
  );
}

function PillarField({
  value,
  onChange,
  error,
  pillars,
  loading,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
  pillars: readonly Pillar[];
  loading: boolean;
}) {
  return (
    <Field label="Pillar" error={error} required>
      {(control) => (
        <Select value={value} onValueChange={onChange} disabled={loading}>
          <SelectTrigger {...control}>
            <SelectValue placeholder={loading ? 'Loading pillars…' : 'Choose a pillar'} />
          </SelectTrigger>
          <SelectContent>
            {pillars.map((pillar) => (
              <SelectItem key={pillar.id} value={pillar.id}>
                {pillar.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </Field>
  );
}

function FormatField({
  value,
  onChange,
  error,
  packs,
  loading,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
  packs: readonly FormatPack[];
  loading: boolean;
}) {
  const pack = packs.find((item) => item.id === value);
  const description = pack
    ? `${pack.minWords.toLocaleString()} to ${pack.maxWords.toLocaleString()} words, with a ${
        pack.ownerInput === 'full_interview' ? 'full interview' : 'two-question interview'
      }.`
    : undefined;
  return (
    <Field label="Format" description={description} error={error} required>
      {(control) => (
        <Select value={value} onValueChange={onChange} disabled={loading}>
          <SelectTrigger {...control}>
            <SelectValue placeholder={loading ? 'Loading formats…' : 'Choose a format'} />
          </SelectTrigger>
          <SelectContent>
            {packs.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </Field>
  );
}

function TakePicker({
  pillar,
  ideaId,
  value,
  onChange,
  error,
}: {
  pillar: string;
  ideaId: string | undefined;
  value: string;
  onChange: (id: string) => void;
  error: string | undefined;
}) {
  const povs = useApiInfiniteQuery(
    {
      queryKey: queryKeys.povs.list({ pillar, status: 'approved' }),
      path: '/api/povs',
      init: { params: { query: { pillar, status: 'approved' } } },
      pageSize: 50,
    },
    { enabled: pillar !== '' },
  );
  if (pillar === '') {
    return <p className="text-sm text-ink-muted">Choose a pillar to see your takes on it.</p>;
  }
  if (povs.isPending) {
    return (
      <div className="grid gap-2">
        <p role="status" className="sr-only">
          Loading your takes
        </p>
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
      </div>
    );
  }
  if (povs.isError) {
    return (
      <ProblemAlert
        error={povs.error}
        title="Couldn’t load your takes."
        onRetry={() => {
          void povs.refetch();
        }}
        retryLabel="Try loading your takes again"
      />
    );
  }
  const all: PovEntry[] = povs.data.map((entry) => entry.item);
  const usable = all
    .filter((pov) => pov.classifier?.passed === true)
    .toSorted((a, b) => Number(b.ideaId === ideaId) - Number(a.ideaId === ideaId));
  const waiting = all.length - usable.length;

  if (usable.length === 0) {
    return (
      <Alert variant="warn">
        <AlertTitle>No cleared take on this pillar yet.</AlertTitle>
        <AlertDescription>
          <p>
            A quick take argues your own position. Write it with “What’s your take?” on the idea, then come back.
            {waiting > 0 ? ` ${String(waiting)} of your takes ${waiting === 1 ? 'is' : 'are'} still waiting for the confidentiality check.` : ''}
          </p>
        </AlertDescription>
        <AlertActions>
          <Button asChild size="sm" variant="secondary">
            <Link to="/calendar/ideas">Open Ideas</Link>
          </Button>
        </AlertActions>
      </Alert>
    );
  }

  return (
    <Fieldset
      legend="Your take"
      description="The position this quick take argues, in your confirmed words."
      error={error}
      required
    >
      {(group) => (
        <RadioGroup {...group} value={value} onValueChange={onChange} className="max-h-64 gap-2 overflow-y-auto">
          {usable.map((pov) => (
            <TakeOption key={pov.id} pov={pov} onIdea={pov.ideaId === ideaId && ideaId !== undefined} />
          ))}
        </RadioGroup>
      )}
    </Fieldset>
  );
}

function TakeOption({ pov, onIdea }: { pov: PovEntry; onIdea: boolean }) {
  const id = useId();
  return (
    <div className="flex items-start gap-3 rounded-md px-2 py-2 has-[[data-state=checked]]:bg-wash-strong">
      <RadioGroupItem id={`${id}radio`} value={pov.id} className="mt-1" />
      <div className="grid min-w-0 gap-1">
        <Label htmlFor={`${id}radio`} className="line-clamp-3 font-normal leading-snug">
          {pov.statement}
        </Label>
        {onIdea ? <span className="text-xs text-ink-subtle">Written for this idea</span> : null}
      </div>
    </div>
  );
}

function NewPieceForm({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const formRef = useRef<HTMLFormElement>(null);
  const pillars = usePillars();
  const packs = useFormatPacks();
  const [source, setSource] = useState<Source>('idea');
  const [make, setMake] = useState<Make>('article');
  const [idea, setIdea] = useState<Idea | undefined>(undefined);
  const [title, setTitle] = useState('');
  const [pillar, setPillar] = useState('');
  const [format, setFormat] = useState('');
  const [postFormat, setPostFormat] = useState<CreatablePostFormat>('text');
  const [povId, setPovId] = useState('');
  const [errors, setErrors] = useState<Readonly<Record<string, string>>>({});

  // An idea another piece took in the meantime is refused (409 invalid_transition): reload the inbox.
  const reloadIdeas = (error: unknown) => {
    if (isProblem(error, 'invalid_transition')) void queryClient.invalidateQueries({ queryKey: queryKeys.ideas.lists() });
  };
  const createArticle = useApiMutation('post', '/api/articles', {
    updates: (data) => queryKeys.articles.detail(data.item.id),
    invalidates: [queryKeys.articles.lists(), queryKeys.ideas.lists(), queryKeys.today],
    successToast: 'Article created.',
    errorToast: false,
    onError: reloadIdeas,
  });
  const createPost = useApiMutation('post', '/api/posts', {
    invalidates: [queryKeys.posts.lists(), queryKeys.ideas.lists(), queryKeys.today],
    successToast: (_data, variables) =>
      variables.body.lane === 'quick_take' ? 'Quick take started. It’s being written now.' : 'Post started. It’s being written now.',
    errorToast: false,
    onError: reloadIdeas,
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.posts.detail(data.post.item.id), data.post);
    },
  });
  const mutation = make === 'article' ? createArticle : createPost;
  const pending = createArticle.isPending || createPost.isPending;
  const server = fieldErrors(mutation.error);
  const fieldProblem = isProblem(mutation.error, 'validation_failed');

  const pillarList = pillars.data?.item.pillars ?? [];
  const packList = packs.data?.item.packs ?? [];
  const isQuickTake = make === 'post' && source === 'idea';

  const chooseIdea = (next: Idea) => {
    setIdea(next);
    setTitle(next.title.slice(0, TITLE_MAX));
    setPillar(next.pillar ?? '');
    setFormat(packList.some((pack) => pack.id === next.suggestedFormat) ? (next.suggestedFormat ?? '') : '');
    setPostFormat(isPostFormat(next.suggestedFormat) ? next.suggestedFormat : 'text');
    setMake(next.suggestedLane === 'quick_take' ? 'post' : 'article');
    setPovId('');
    setErrors({});
  };

  const validate = (): Record<string, string> => {
    const problems: Record<string, string> = {};
    if (source === 'idea' && !idea) problems.idea = 'Choose an idea.';
    if (!pillar) problems.pillar = 'Choose a pillar.';
    if (make === 'article') {
      const trimmed = title.trim();
      if (trimmed === '') problems.title = 'Give it a working title.';
      else if (trimmed.length > TITLE_MAX) problems.title = `Keep the title to ${String(TITLE_MAX)} characters.`;
      if (!format) problems.format = 'Choose a format.';
    } else if (source === 'idea' && !povId) {
      problems.take = 'Choose the take it argues.';
    }
    return problems;
  };

  const submit = () => {
    const problems = validate();
    flushSync(() => {
      setErrors(problems);
    });
    if (Object.keys(problems).length > 0) {
      focusFirstProblem(formRef.current);
      return;
    }
    const ideaId = source === 'idea' ? idea?.id : undefined;
    if (make === 'article') {
      createArticle.mutate(
        { body: { meta: { title: title.trim(), pillar, format }, ...(ideaId ? { ideaId } : {}) } },
        {
          onSuccess: (data) => {
            onDone();
            void navigate(`/studio/${encodeURIComponent(data.item.id)}/brief`);
          },
        },
      );
      return;
    }
    const body =
      source === 'idea' && ideaId
        ? { lane: 'quick_take' as const, pillar, format: postFormat, ideaId, povIds: [povId] }
        : { lane: 'native' as const, pillar, format: postFormat };
    createPost.mutate(
      { body },
      {
        onSuccess: (data) => {
          onDone();
          // The Posts tab can follow the `posts.generate` job (the post document does not name it).
          void navigate(`/studio/${encodeURIComponent(data.post.item.id)}/posts`, { state: { jobId: data.jobId } });
        },
      },
    );
  };

  const fix = setupFix(mutation.error);
  const submitLabel = make === 'article' ? 'Create article' : isQuickTake ? 'Start the quick take' : 'Start the post';

  return (
    <form
      ref={formRef}
      noValidate
      className="grid gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Fieldset legend="Start from">
        {(group) => (
          <SegmentedControl
            {...group}
            value={source}
            onValueChange={(value) => {
              setSource(value === 'blank' ? 'blank' : 'idea');
              setErrors({});
            }}
            fullWidth
          >
            <SegmentedControlItem value="idea">An idea</SegmentedControlItem>
            <SegmentedControlItem value="blank">A blank page</SegmentedControlItem>
          </SegmentedControl>
        )}
      </Fieldset>

      {source === 'idea' ? (
        <IdeaPicker
          value={idea?.id}
          error={errors.idea}
          onSelect={chooseIdea}
          onStartBlank={() => {
            setSource('blank');
          }}
        />
      ) : null}

      {source === 'blank' || idea ? (
        <>
          <Fieldset legend="Make it">
            {(group) => (
              <SegmentedControl
                {...group}
                value={make}
                onValueChange={(value) => {
                  setMake(value === 'post' ? 'post' : 'article');
                  setErrors({});
                }}
                fullWidth
              >
                <SegmentedControlItem value="article">An article</SegmentedControlItem>
                <SegmentedControlItem value="post">{source === 'idea' ? 'A quick take' : 'A LinkedIn post'}</SegmentedControlItem>
              </SegmentedControl>
            )}
          </Fieldset>

          {make === 'article' ? (
            <>
              <Field
                label="Working title"
                description="You can change it in the draft."
                error={errors.title ?? (fieldProblem ? server['meta.title'] : undefined)}
                required
              >
                <Input
                  value={title}
                  maxLength={TITLE_MAX}
                  autoComplete="off"
                  onChange={(event) => {
                    setTitle(event.target.value);
                  }}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <PillarField
                  value={pillar}
                  onChange={setPillar}
                  error={errors.pillar ?? (fieldProblem ? server['meta.pillar'] : undefined)}
                  pillars={pillarList}
                  loading={pillars.isPending}
                />
                <FormatField
                  value={format}
                  onChange={setFormat}
                  error={errors.format ?? (fieldProblem ? server['meta.format'] : undefined)}
                  packs={packList}
                  loading={packs.isPending}
                />
              </div>
            </>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <PillarField
                  value={pillar}
                  onChange={(value) => {
                    setPillar(value);
                    setPovId('');
                  }}
                  error={errors.pillar ?? (fieldProblem ? server.pillar : undefined)}
                  pillars={pillarList}
                  loading={pillars.isPending}
                />
                <Fieldset legend="Post format" error={fieldProblem ? server.format : undefined}>
                  {(group) => (
                    <SegmentedControl
                      {...group}
                      value={postFormat}
                      onValueChange={(value) => {
                        if (isPostFormat(value)) setPostFormat(value);
                      }}
                      fullWidth
                    >
                      {POST_FORMATS.map((item) => (
                        <SegmentedControlItem key={item} value={item}>
                          {POST_FORMAT_LABEL[item]}
                        </SegmentedControlItem>
                      ))}
                    </SegmentedControl>
                  )}
                </Fieldset>
              </div>
              {isQuickTake ? (
                <TakePicker pillar={pillar} ideaId={idea?.id} value={povId} onChange={setPovId} error={errors.take} />
              ) : (
                <p className="text-sm text-ink-muted">
                  It draws on your approved stories and positions for this pillar. You review it before anything is
                  posted.
                </p>
              )}
            </>
          )}
        </>
      ) : null}

      {mutation.isError && isProblem(mutation.error, 'invalid_transition') ? (
        <Alert variant="error">
          <AlertTitle>{make === 'article' ? 'The article wasn’t created.' : 'The post wasn’t started.'}</AlertTitle>
          <AlertDescription>
            <p>This idea already has a piece, so it can’t start another one. Pick another idea.</p>
          </AlertDescription>
        </Alert>
      ) : null}
      {mutation.isError && !fieldProblem && !isProblem(mutation.error, 'invalid_transition') ? (
        <div className="grid gap-2">
          <ProblemAlert error={mutation.error} title={make === 'article' ? 'The article wasn’t created.' : 'The post wasn’t started.'} />
          {fix ? (
            <Button asChild size="sm" variant="secondary" className="justify-self-start">
              <Link to={fix.to}>{fix.label}</Link>
            </Button>
          ) : null}
        </div>
      ) : null}

      <DialogFooter>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? 'Creating…' : submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}

export interface NewPieceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * "New piece": an article or a quick take from an idea in the inbox, or an article or a native
 * LinkedIn post from a blank page (`POST /api/articles`, `POST /api/posts`). The form starts over
 * each time the dialog opens.
 */
export function NewPieceDialog({ open, onOpenChange }: NewPieceDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>New piece</DialogTitle>
          <DialogDescription>Start from an idea in your inbox, or from a blank page.</DialogDescription>
        </DialogHeader>
        <NewPieceForm
          onDone={() => {
            onOpenChange(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
