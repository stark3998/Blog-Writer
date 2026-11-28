import { CalendarClock, ExternalLink } from 'lucide-react';
import { useState, type SubmitEvent } from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import type { components } from '@/lib/api/schema';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { DialogActions, ResponsiveDialog } from '../components/responsive-dialog';
import {
  addDays,
  isLocalTime,
  localDateOf,
  momentLabel,
  parseLocalDate,
  parseTimestamp,
  slotLabel,
  zoneCity,
} from '../lib/time';

import { postHref } from './slot-model';

type PostEntry = components['schemas']['Versioned_PostSummary_'];

interface MissedPostsProps {
  entries: readonly PostEntry[];
  zone: string;
  nowMs: number;
}

/**
 * Posts that missed their slot (slot + 2 h passed without going out). The offer is the next
 * free slot (`POST /api/posts/{id}/reslot {action: next_slot}`): never a day another post holds,
 * and never after a time-sensitive post expires. Or the owner picks a time.
 */
export function MissedPosts({ entries, zone, nowMs }: MissedPostsProps) {
  const missed = entries.filter((entry) => entry.item.status === 'missed');
  if (missed.length === 0) return null;
  return (
    <div className="space-y-3">
      {missed.map((entry) => (
        <MissedPost key={entry.item.id} entry={entry} zone={zone} nowMs={nowMs} />
      ))}
    </div>
  );
}

function MissedPost({ entry, zone, nowMs }: { entry: PostEntry; zone: string; nowMs: number }) {
  const post = entry.item;
  const [picking, setPicking] = useState(false);
  const reslot = useApiMutation('post', '/api/posts/{post_id}/reslot', {
    invalidates: [queryKeys.posts.lists(), queryKeys.plans.all, queryKeys.today],
    errorToast: 'Couldn’t move the post.',
    successToast: (data) => {
      const slot = data.item.slot;
      return slot ? `Moved to ${slotLabel(slot.localDate, slot.localTime)}.` : 'Moved.';
    },
  });
  const slotMs = parseTimestamp(post.slot?.utc);
  const when = slotMs !== undefined ? momentLabel(slotMs, zone) : 'its slot';
  const title = post.hook.trim() || 'Your post';

  const send = (body: components['schemas']['ReslotIn'], onDone?: () => void) => {
    reslot.mutate(
      { params: { path: { post_id: post.id } }, body, ifMatch: entry.etag ?? undefined },
      { onSuccess: () => onDone?.() },
    );
  };

  return (
    <Alert variant="warn">
      <AlertTitle>{`Missed ${when}: ${title}`}</AlertTitle>
      <AlertDescription>
        <p>Nothing went out. Take the next free slot, or pick a time: two posts never share a day.</p>
      </AlertDescription>
      <AlertActions>
        <Button
          size="sm"
          disabled={reslot.isPending}
          onClick={() => {
            send({ action: 'next_slot' });
          }}
        >
          <CalendarClock aria-hidden="true" />
          Take the next free slot
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setPicking(true);
          }}
        >
          Pick a time…
        </Button>
        <Button size="sm" variant="ghost" asChild>
          <Link to={postHref(post)}>
            <ExternalLink aria-hidden="true" />
            Open the post
          </Link>
        </Button>
      </AlertActions>
      <ResponsiveDialog
        open={picking}
        onOpenChange={setPicking}
        title="Pick a new time"
        description={`For “${title}”. Times are ${zoneCity(zone)} time.`}
      >
        {picking ? (
          <PickTimeForm
            today={localDateOf(nowMs, zone)}
            saving={reslot.isPending}
            onCancel={() => {
              setPicking(false);
            }}
            onSave={(localDate, localTime) => {
              send({ action: 'set', slot: { localDate, localTime } }, () => {
                setPicking(false);
              });
            }}
          />
        ) : null}
      </ResponsiveDialog>
    </Alert>
  );
}

interface PickTimeFormProps {
  today: string;
  saving: boolean;
  onCancel: () => void;
  onSave: (localDate: string, localTime: string) => void;
}

function PickTimeForm({ today, saving, onCancel, onSave }: PickTimeFormProps) {
  const [date, setDate] = useState(addDays(today, 1));
  const [time, setTime] = useState('10:00');
  const [submitted, setSubmitted] = useState(false);
  const errors = {
    date: parseLocalDate(date) === undefined || date < today ? 'Pick today or a later day.' : undefined,
    time: isLocalTime(time) ? undefined : 'Enter a time as HH:MM.',
  };
  const shown: Partial<typeof errors> = submitted ? errors : {};
  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitted(true);
    if (errors.date || errors.time) return;
    onSave(date, time);
  };
  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Day" error={shown.date}>
          <Input type="date" min={today} max={addDays(today, 56)} value={date} onChange={(event) => { setDate(event.target.value); }} />
        </Field>
        <Field label="Time" error={shown.time}>
          <Input type="time" value={time} onChange={(event) => { setTime(event.target.value); }} />
        </Field>
      </div>
      <p className="text-sm text-ink-muted">If another post already goes out that day, the move is refused.</p>
      <DialogActions>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={saving}>
          {saving ? 'Moving…' : 'Move the post'}
        </Button>
      </DialogActions>
    </form>
  );
}
