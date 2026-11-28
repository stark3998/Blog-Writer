import { CalendarArrowUp, CalendarClock } from 'lucide-react';
import { useId, useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { PostStatusBadge } from '../posts/components/status-badge';
import { useNow } from '@/lib/use-now';
import { canMove } from '../posts/model/machines';
import { formatInstant, formatSlot, formatTime, relativeTime } from '../posts/model/time';
import type { GoLiveView } from '../posts/model/types';
import { useMachines } from '../posts/queries';

import { ActionBar } from './action-bar';
import { Proof } from './proof';
import { RESLOT_CODES } from './publish-panel';
import { goLiveKey, kitKey } from './queries';
import { PanelHeading } from './panel-heading';

/**
 * A slot that passed without the post (`missed`): take the next free slot (never stacked on
 * another post's day) or pick a time. A time-sensitive post is not offered a slot after it
 * expires. Moving it makes it Approved again, staged an hour before the new slot.
 */
export function MissedPanel({ view }: { view: GoLiveView }) {
  const post = view.post.item;
  const postId = post.id;
  const now = useNow();
  const machines = useMachines();
  const formId = useId();
  const [picking, setPicking] = useState(false);
  const [localDate, setLocalDate] = useState('');
  const [localTime, setLocalTime] = useState('');
  const reslot = useApiMutation('post', '/api/posts/{post_id}/reslot', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates: [goLiveKey(postId), kitKey(postId), queryKeys.posts.lists(), queryKeys.today],
    handles: RESLOT_CODES,
    successToast: (data) => (data.item.slot ? `Moved to ${formatSlot(data.item.slot)}.` : 'Moved.'),
  });
  const movable = canMove(machines, 'post', post.status, 'approved');
  const slot = post.slot;
  const zone = slot?.timezone;
  const slotFieldError =
    isProblem(reslot.error, ['slot_in_past', 'slot_conflict', 'expires_first', 'invalid_slot', 'slot_required']) && picking
      ? problemMessage(reslot.error)
      : undefined;

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (localDate === '' || localTime === '') return;
    reslot.mutate({
      params: { path: { post_id: postId } },
      body: { action: 'set', slot: { localDate, localTime } },
      ifMatch: view.post.etag,
    });
  }

  return (
    <div className="grid gap-8">
      <header className="grid gap-2">
        <div>
          <PostStatusBadge status={post.status} />
        </div>
        <PanelHeading postId={postId}>
          {slot ? `The ${formatTime(slot.utc, slot.timezone)} slot passed` : 'The slot passed'}
        </PanelHeading>
        <p className="text-lg text-ink-muted">
          {slot ? `It was due ${formatSlot(slot)}, ${relativeTime(slot.utc, now)}. It didn’t go out.` : 'It didn’t go out.'}
          {post.timeSensitive && post.expiresAt ? ` It expires ${formatInstant(post.expiresAt, zone)}.` : ''}
        </p>
      </header>

      {reslot.error && !slotFieldError ? (
        <Alert variant="error">
          <AlertTitle>Couldn’t move the post.</AlertTitle>
          <AlertDescription>
            <p>{problemMessage(reslot.error)}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {picking ? (
        <form id={formId} onSubmit={submit} className="grid max-w-measure gap-3 rounded-lg border border-line p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Date" required error={slotFieldError}>
              <Input
                type="date"
                value={localDate}
                onChange={(event) => {
                  setLocalDate(event.target.value);
                }}
              />
            </Field>
            <Field label="Time" required description={zone ? `In ${zone}.` : undefined}>
              <Input
                type="time"
                value={localTime}
                onChange={(event) => {
                  setLocalTime(event.target.value);
                }}
              />
            </Field>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={reslot.isPending || localDate === '' || localTime === ''}>
              Move it to this time
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setPicking(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : null}

      <Proof view={view} />

      <ActionBar
        label="Move the post"
        primary={
          <Button
            size="lg"
            className="w-full md:w-auto"
            disabled={!movable || reslot.isPending}
            onClick={() => {
              reslot.mutate({ params: { path: { post_id: postId } }, body: { action: 'next_slot' }, ifMatch: view.post.etag });
            }}
          >
            <CalendarArrowUp aria-hidden="true" />
            {reslot.isPending ? 'Moving…' : 'Take the next free slot'}
          </Button>
        }
        secondary={
          <Button
            variant="secondary"
            className="min-h-11"
            disabled={!movable || reslot.isPending}
            onClick={() => {
              setPicking(true);
            }}
          >
            <CalendarClock aria-hidden="true" />
            Pick a time
          </Button>
        }
      />
    </div>
  );
}
