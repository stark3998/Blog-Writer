import { useQueryClient } from '@tanstack/react-query';
import { CircleCheck } from 'lucide-react';
import { useId, useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, Fieldset, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isEtagMismatch, type Versioned } from '@/lib/api/envelope';
import { fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { useNow } from '@/lib/use-now';
import { formatDateTime, formatRelative } from '../settings/format';
import { usePostedPosts, type MetricsSnapshot, type Post, type PostSummary } from './queries';

export type SnapshotOffset = MetricsSnapshot['offset'];

const OFFSETS: readonly { value: SnapshotOffset; label: string }[] = [
  { value: 'h48', label: '+48 h' },
  { value: 'd7', label: '+7 days' },
  { value: 'other', label: 'Other' },
];

const METRIC_FIELDS = [
  { key: 'impressions', label: 'Impressions' },
  { key: 'reactions', label: 'Reactions' },
  { key: 'comments', label: 'Comments' },
  { key: 'reposts', label: 'Reposts' },
  { key: 'saves', label: 'Saves' },
  { key: 'sends', label: 'Sends' },
] as const;
type MetricKey = (typeof METRIC_FIELDS)[number]['key'];
type Numbers = Record<MetricKey, string>;

const EMPTY: Numbers = { impressions: '', reactions: '', comments: '', reposts: '', saves: '', sends: '' };
const MAX_COUNT = 1_000_000_000;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/**
 * Which snapshot a post is due for, from when it went live (the server's `offset_for`): `h48`
 * between 36 and 72 hours, `d7` between 6 and 8 days, otherwise none.
 */
export function dueOffset(postedAt: string | null | undefined, now: number): SnapshotOffset | undefined {
  if (!postedAt) return undefined;
  const age = now - Date.parse(postedAt);
  if (Number.isNaN(age)) return undefined;
  if (age >= 36 * HOUR && age < 72 * HOUR) return 'h48';
  if (age >= 6 * DAY && age < 8 * DAY) return 'd7';
  return undefined;
}

/** Posts in the order the form offers them: those due for a snapshot first, then the newest. */
export function snapshotOrder(posts: readonly PostSummary[], now: number): PostSummary[] {
  return [...posts].sort((a, b) => {
    const due = Number(dueOffset(b.postedAt, now) !== undefined) - Number(dueOffset(a.postedAt, now) !== undefined);
    return due || (b.postedAt ?? '').localeCompare(a.postedAt ?? '');
  });
}

/**
 * A whole count, `null` when left empty, `undefined` when it is not a whole number in range.
 * Thousands separators are read where they group digits ("2,400", "2.400", "2 400"), so a
 * decimal such as "1.5" is never mistaken for 15.
 */
export function countOf(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  const grouped = /^\d{1,3}(?:([,.\s'\u00a0\u202f])\d{3}(?:\1\d{3})*)?$/.test(trimmed);
  if (!/^\d+$/.test(trimmed) && !grouped) return undefined;
  const value = Number(trimmed.replace(/\D/g, ''));
  return value <= MAX_COUNT ? value : undefined;
}

function numbersFrom(snapshot: MetricsSnapshot | undefined): Numbers {
  if (!snapshot) return EMPTY;
  const text = (value: number | null | undefined) => (typeof value === 'number' ? String(value) : '');
  return {
    impressions: text(snapshot.impressions),
    reactions: text(snapshot.reactions),
    comments: text(snapshot.comments),
    reposts: text(snapshot.reposts),
    saves: text(snapshot.saves),
    sends: text(snapshot.sends),
  };
}

function hookOf(post: Pick<Post, 'displayText'>): string {
  return post.displayText.split('\n').find((line) => line.trim() !== '')?.trim() ?? 'Untitled post';
}

function SnapshotEditor({ entry, hook, onDone }: { entry: Versioned<Post>; hook: string; onDone?: () => void }) {
  const now = useNow();
  const queryClient = useQueryClient();
  const post = entry.item;
  const formRef = useRef<HTMLFormElement>(null);
  const [offset, setOffset] = useState<SnapshotOffset>(() => dueOffset(post.postedAt, now) ?? 'other');
  const existing = (value: SnapshotOffset) =>
    value === 'other' ? undefined : (post.snapshots ?? []).find((snapshot) => snapshot.offset === value && snapshot.source === 'form');
  const [numbers, setNumbers] = useState<Numbers>(() => numbersFrom(existing(offset)));
  const [touched, setTouched] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [stale, setStale] = useState(false);
  const save = useApiMutation('post', '/api/posts/{post_id}/snapshots', {
    updates: (data) => queryKeys.posts.detail(data.item.id),
    invalidates: [queryKeys.posts.lists(), queryKeys.today],
    successToast: 'Numbers saved.',
    errorToast: 'Couldn’t save the numbers.',
    handles: ['etag_mismatch', 'validation_failed', 'not_posted', 'snapshot_in_future', 'snapshot_before_post', 'too_many_snapshots'],
  });

  const values = Object.fromEntries(METRIC_FIELDS.map(({ key }) => [key, countOf(numbers[key])])) as Record<
    MetricKey,
    number | null | undefined
  >;
  const invalid = METRIC_FIELDS.filter(({ key }) => values[key] === undefined).map(({ key }) => key);
  const empty = METRIC_FIELDS.every(({ key }) => values[key] === null);
  const serverErrors = fieldErrors(save.error);
  const logged = existing(offset);

  const chooseOffset = (next: SnapshotOffset) => {
    setOffset(next);
    // Untouched fields show what is already logged for that offset, ready to correct.
    if (!touched) setNumbers(numbersFrom(existing(next)));
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (invalid.length > 0 || empty) {
      flushSync(() => {
        setAttempted(true);
      });
      focusFirstInvalid(formRef.current);
      return;
    }
    const count = (key: MetricKey) => values[key] ?? null;
    save.mutate(
      {
        params: { path: { post_id: post.id } },
        body: {
          offset,
          impressions: count('impressions'),
          reactions: count('reactions'),
          comments: count('comments'),
          reposts: count('reposts'),
          saves: count('saves'),
          sends: count('sends'),
        },
        ifMatch: entry.etag,
      },
      {
        onSuccess: () => {
          setStale(false);
          setTouched(false);
          setAttempted(false);
          onDone?.();
        },
        onError: (error) => {
          if (!isEtagMismatch(error)) return;
          setStale(true);
          // The next save needs the current version: read it again (the numbers stay as typed).
          void queryClient.invalidateQueries({ queryKey: queryKeys.posts.detail(post.id) });
        },
      },
    );
  };

  const refusal = isProblem(save.error, ['not_posted', 'snapshot_in_future', 'snapshot_before_post', 'too_many_snapshots'])
    ? problemMessage(save.error)
    : undefined;

  return (
    <form ref={formRef} noValidate onSubmit={submit} aria-label="Snapshot numbers" className="grid gap-5">
      <p className="text-sm text-ink-muted">
        {`“${hook}”, live ${post.postedAt ? formatRelative(post.postedAt, now) : 'recently'}.`}
        {post.postUrl ? (
          <>
            {' '}
            <a
              href={post.postUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-ink underline decoration-line-strong underline-offset-4"
            >
              Open it on LinkedIn
            </a>{' '}
            to read the numbers.
          </>
        ) : null}
      </p>

      <Fieldset legend="Which snapshot">
        {(group) => (
          <SegmentedControl
            {...group}
            fullWidth
            value={offset}
            onValueChange={(value) => {
              const next = OFFSETS.find((item) => item.value === value);
              if (next) chooseOffset(next.value);
            }}
          >
            {OFFSETS.map((item) => (
              <SegmentedControlItem key={item.value} value={item.value}>
                {item.label}
              </SegmentedControlItem>
            ))}
          </SegmentedControl>
        )}
      </Fieldset>
      {logged ? (
        <p className="flex items-start gap-2 text-sm text-ink-muted">
          <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-success" />
          {`Logged ${formatDateTime(logged.takenAt)}. Saving again corrects it.`}
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-x-3 gap-y-4 sm:grid-cols-3">
        {METRIC_FIELDS.map(({ key, label }) => (
          <Field
            key={key}
            label={label}
            className={key === 'impressions' ? 'col-span-2 sm:col-span-3' : undefined}
            description={key === 'impressions' ? 'Rates need it: everything is compared per 1,000 impressions.' : undefined}
            error={
              (attempted && values[key] === undefined ? 'A whole number, please.' : undefined) ?? serverErrors[key]
            }
          >
            <Input
              inputMode="numeric"
              autoComplete="off"
              enterKeyHint={key === 'sends' ? 'done' : 'next'}
              value={numbers[key]}
              className={key === 'impressions' ? 'text-lg' : undefined}
              onChange={(event) => {
                if (save.isError) save.reset();
                setTouched(true);
                setNumbers((current) => ({ ...current, [key]: event.target.value }));
              }}
            />
          </Field>
        ))}
      </div>
      {attempted && empty ? (
        <p role="alert" className="text-sm font-medium text-danger">
          Enter at least one number.
        </p>
      ) : null}

      {stale ? (
        <Alert variant="warn">
          <AlertTitle>This post changed since you opened it.</AlertTitle>
          <AlertDescription>
            <p>Another snapshot or an import was saved meanwhile. Your numbers are still here: check them and save again.</p>
          </AlertDescription>
        </Alert>
      ) : refusal ? (
        <Alert variant="error">
          <AlertTitle>The numbers weren’t saved.</AlertTitle>
          <AlertDescription>
            <p>{refusal}</p>
          </AlertDescription>
        </Alert>
      ) : null}
      {save.isSuccess && !touched ? (
        <p role="status" className="flex items-center gap-2 text-sm text-ink-muted">
          <CircleCheck aria-hidden="true" className="size-4 text-success" />
          Saved. The rates in Insights include it now.
        </p>
      ) : null}

      <div className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] -mx-1 bg-sheet px-1 py-2 md:static md:mx-0 md:p-0">
        <Button type="submit" size="lg" className="w-full sm:w-auto" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save the numbers'}
        </Button>
      </div>
    </form>
  );
}

export interface SnapshotFormProps {
  /** Start on this post (a reminder's link, `/today?snapshot=<postId>`). */
  postId?: string;
  /** Called after the numbers are saved (closes a sheet, moves on). */
  onDone?: () => void;
}

/**
 * The 30-second snapshot form (phone-first): pick a live post, the +48 h or +7 day reading, and
 * type what LinkedIn's post analytics show. Sending the same offset again corrects it.
 */
export function SnapshotForm({ postId, onDone }: SnapshotFormProps) {
  const now = useNow();
  const pickerId = useId();
  const posts = usePostedPosts();
  const [picked, setPicked] = useState(postId);
  const ordered = snapshotOrder((posts.data ?? []).map((entry) => entry.item), now);
  const chosen = picked ?? ordered[0]?.id;
  const summary = ordered.find((post) => post.id === chosen);

  if (posts.isPending && !postId) {
    return (
      <div className="grid gap-3">
        <p role="status" className="sr-only">
          Loading your live posts
        </p>
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (posts.isError && !postId) {
    return (
      <ProblemAlert
        error={posts.error}
        title="Couldn’t load your live posts."
        onRetry={() => void posts.refetch()}
        retryLabel="Try loading your live posts again"
      />
    );
  }
  if (!chosen) {
    return <p className="text-ink-muted">No post is live on LinkedIn yet. Snapshots start with the first one.</p>;
  }

  // A post opened from a link may be older than the list: then it is the only one offered.
  const listed = ordered.some((post) => post.id === chosen);
  return (
    <div className="grid gap-5">
      {listed ? (
        <Field label="Post" id={pickerId}>
          {(control) => (
            <Select value={chosen} onValueChange={setPicked}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ordered.map((post) => {
                  const due = dueOffset(post.postedAt, now);
                  return (
                    <SelectItem key={post.id} value={post.id}>
                      {`${post.hook}${due ? ` (due: ${due === 'h48' ? '+48 h' : '+7 days'})` : ''}`}
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          )}
        </Field>
      ) : null}
      <PostEditor key={chosen} postId={chosen} summaryHook={summary?.hook} onDone={onDone} />
    </div>
  );
}

function PostEditor({ postId, summaryHook, onDone }: { postId: string; summaryHook?: string; onDone?: () => void }) {
  const detail = useApiQuery({
    queryKey: queryKeys.posts.detail(postId),
    path: '/api/posts/{post_id}',
    init: { params: { path: { post_id: postId } } },
  });
  if (detail.isPending) {
    return (
      <div className="grid gap-3">
        <p role="status" className="sr-only">
          Loading the post
        </p>
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (detail.isError) {
    return (
      <ProblemAlert
        error={detail.error}
        title="Couldn’t load the post."
        onRetry={() => void detail.refetch()}
        retryLabel="Try loading the post again"
      />
    );
  }
  if (detail.data.item.status !== 'posted') {
    return <p className="text-ink-muted">This post isn’t live on LinkedIn, so it has no numbers to log yet.</p>;
  }
  return <SnapshotEditor entry={detail.data} hook={summaryHook ?? hookOf(detail.data.item)} onDone={onDone} />;
}
