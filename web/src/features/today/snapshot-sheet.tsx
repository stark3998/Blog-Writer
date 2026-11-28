import { useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, Fieldset, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { StickyActions } from '@/features/engagement/components/sticky-actions';
import { firstLine } from '@/features/engagement/lib/text';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { momentWords, timeOf } from './lib/time';
import { useNow } from '@/lib/use-now';

type Post = components['schemas']['Post'];
type Offset = components['schemas']['SnapshotOffset'];

export const METRICS = [
  { name: 'impressions', label: 'Impressions' },
  { name: 'reactions', label: 'Reactions' },
  { name: 'comments', label: 'Comments' },
  { name: 'reposts', label: 'Reposts' },
  { name: 'saves', label: 'Saves' },
  { name: 'sends', label: 'Sends' },
] as const;

type Metric = (typeof METRICS)[number]['name'];
type Values = Record<Metric, string>;

/** The server's cap per number (`analytics.snapshots.MAX_COUNT`). */
const MAX_COUNT = 1_000_000_000;
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

/** The offset a snapshot read now stands for, as the server decides it (`offset_for`). */
export function offsetFor(postedAt: string | null | undefined, now: number): Offset {
  const posted = timeOf(postedAt);
  if (posted === undefined) return 'other';
  const age = now - posted;
  if (age >= 36 * HOUR && age < 72 * HOUR) return 'h48';
  if (age >= 6 * DAY && age < 8 * DAY) return 'd7';
  return 'other';
}

function valuesFrom(post: Post, offset: Offset): Values {
  // The 48-hour and 7-day numbers are corrected in place: start from what was logged.
  const logged = offset === 'other' ? undefined : post.snapshots?.find((snapshot) => snapshot.offset === offset);
  const empty: Values = { impressions: '', reactions: '', comments: '', reposts: '', saves: '', sends: '' };
  if (!logged) return empty;
  const values = { ...empty };
  for (const { name } of METRICS) {
    const value = logged[name];
    values[name] = typeof value === 'number' ? String(value) : '';
  }
  return values;
}

type Errors = Partial<Record<Metric | 'form', string>>;

function SnapshotForm({ postId, onDone }: { postId: string; onDone: () => void }) {
  const now = useNow();
  const formRef = useRef<HTMLFormElement>(null);
  const post = useApiQuery({
    queryKey: queryKeys.posts.detail(postId),
    path: '/api/posts/{post_id}',
    init: { params: { path: { post_id: postId } } },
  });
  const save = useApiMutation('post', '/api/posts/{post_id}/snapshots', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates: [queryKeys.today],
    successToast: 'Numbers saved.',
    errorToast: 'Couldn’t save the numbers.',
    handles: ['etag_mismatch', 'not_posted', 'validation_failed', 'snapshot_in_future', 'snapshot_before_post', 'too_many_snapshots'],
  });
  const conflict = useConflict();
  const [offset, setOffset] = useState<Offset | null>(null);
  const [values, setValues] = useState<Values | null>(null);
  const [errors, setErrors] = useState<Errors>({});

  if (post.isPending) {
    return (
      <div aria-busy="true" className="grid gap-3">
        <span className="sr-only">Loading the post</span>
        <Skeleton className="h-12" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (post.isError) {
    return <ProblemAlert error={post.error} title="Couldn’t load the post." onRetry={() => void post.refetch()} />;
  }

  const { item, etag } = post.data;
  if (item.status !== 'posted') {
    return (
      <Alert variant="info">
        <AlertTitle>This post isn’t live</AlertTitle>
        <AlertDescription>
          <p>Numbers can be added once it is posted on LinkedIn.</p>
        </AlertDescription>
      </Alert>
    );
  }

  const chosen = offset ?? offsetFor(item.postedAt, now);
  const current = values ?? valuesFrom(item, chosen);
  const posted = timeOf(item.postedAt);

  function update(name: Metric, value: string) {
    setValues({ ...current, [name]: value });
  }

  function send(ifMatch: string | null) {
    const body: components['schemas']['MetricsSnapshotIn'] = { offset: chosen };
    for (const { name } of METRICS) {
      const text = current[name].trim();
      body[name] = text === '' ? null : Number(text);
    }
    save.mutate(
      { params: { path: { post_id: postId } }, body, ifMatch },
      {
        onSuccess: () => {
          conflict.close();
          onDone();
        },
        onError: (error) => {
          if (conflict.show(error)) return;
          const fields = fieldErrors(error);
          const next: Errors = {};
          for (const { name } of METRICS) if (fields[name]) next[name] = fields[name];
          if (isProblem(error, ['not_posted', 'snapshot_in_future', 'snapshot_before_post', 'too_many_snapshots', 'validation_failed'])) {
            next.form = problemMessage(error);
          }
          setErrors(next);
        },
      },
    );
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const next: Errors = {};
    let filled = 0;
    for (const { name } of METRICS) {
      const text = current[name].trim();
      if (text === '') continue;
      filled += 1;
      const number = Number(text);
      if (!/^\d+$/.test(text) || !Number.isSafeInteger(number)) next[name] = 'Use a whole number, without commas.';
      else if (number > MAX_COUNT) next[name] = 'That number is too large.';
    }
    if (filled === 0) next.form = 'Enter at least one number.';
    if (Object.keys(next).length > 0) {
      flushSync(() => {
        setErrors(next);
      });
      if (!focusFirstInvalid(formRef.current)) formRef.current?.querySelector<HTMLInputElement>('input')?.focus();
      return;
    }
    setErrors({});
    send(etag);
  }

  return (
    <form ref={formRef} noValidate onSubmit={submit} className="grid gap-6">
      <div className="grid gap-1 rounded-md bg-sunken px-3 py-2 text-sm">
        <p className="text-ink">“{firstLine(item.finalText ?? item.displayText)}”</p>
        {posted !== undefined ? <p className="text-ink-muted">Posted {momentWords(posted, now)}</p> : null}
      </div>

      <Fieldset legend="When are these numbers from?">
        {(group) => (
          <SegmentedControl
            {...group}
            fullWidth
            value={chosen}
            onValueChange={(value) => {
              const next: Offset = value === 'h48' || value === 'd7' ? value : 'other';
              setOffset(next);
              setValues(valuesFrom(item, next));
            }}
          >
            <SegmentedControlItem value="h48">48 hours</SegmentedControlItem>
            <SegmentedControlItem value="d7">7 days</SegmentedControlItem>
            <SegmentedControlItem value="other">Another time</SegmentedControlItem>
          </SegmentedControl>
        )}
      </Fieldset>

      <Fieldset
        legend="The numbers"
        description="Read them from the post’s analytics on LinkedIn. Leave a box empty when LinkedIn doesn’t show it."
      >
        <div className="grid grid-cols-2 gap-x-3 gap-y-4">
          {METRICS.map(({ name, label }) => (
            <Field key={name} label={label} error={errors[name]}>
              <Input
                inputMode="numeric"
                pattern="[0-9]*"
                autoComplete="off"
                value={current[name]}
                onChange={(event) => {
                  update(name, event.target.value);
                }}
              />
            </Field>
          ))}
        </div>
      </Fieldset>

      {errors.form ? (
        <Alert variant="error">
          <AlertDescription>
            <p>{errors.form}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <StickyActions>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save the numbers'}
        </Button>
      </StickyActions>

      <ConflictDialog
        {...conflict}
        subject="This post"
        saving={save.isPending}
        onKeepMine={() => {
          const latest = conflict.conflict?.etag;
          if (latest) send(latest);
          else {
            conflict.close();
            void post.refetch();
          }
        }}
        onTakeTheirs={() => {
          conflict.close();
          setValues(null);
          void post.refetch();
        }}
      />
    </form>
  );
}

interface SnapshotSheetProps {
  /** The post whose numbers to log; `null` closes the sheet. */
  postId: string | null;
  onClose: () => void;
}

/**
 * The 30-second snapshot form (+48 h and +7 d after a post goes live: impressions, reactions,
 * comments, reposts, saves, sends), opened from a snapshot reminder (`/today?snapshot=<postId>`).
 * `POST /api/posts/{id}/snapshots` with the post's If-Match.
 */
export function SnapshotSheet({ postId, onClose }: SnapshotSheetProps) {
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  return (
    <Sheet
      open={postId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent side={isDesktop ? 'right' : 'bottom'} className="overflow-y-auto md:w-[min(30rem,92vw)]">
        <SheetHeader>
          <SheetTitle>Log the numbers</SheetTitle>
          <SheetDescription>What LinkedIn shows for your post. About 30 seconds.</SheetDescription>
        </SheetHeader>
        {postId !== null ? <SnapshotForm key={postId} postId={postId} onDone={onClose} /> : null}
      </SheetContent>
    </Sheet>
  );
}
