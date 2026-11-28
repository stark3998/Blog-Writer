import { useQueryClient } from '@tanstack/react-query';
import { CircleCheck, Ellipsis, ExternalLink, Pause, Pencil, Play, Plus, Trash2 } from 'lucide-react';
import { useId, useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';

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
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Field, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isSameDay, sinceWords, timeOf } from '@/features/today/lib/time';
import { useNow } from '@/lib/use-now';
import { fieldErrors } from '@/lib/api/problems';
import { isHttpsUrl } from '@/lib/urls';

import {
  engageKeys,
  useAddVoice,
  useMarkEngaged,
  useRemoveVoice,
  useUpdateVoice,
  useWatchlist,
  type WatchEntryVersioned,
  type WatchIn,
} from './lib/queries';
import { pillarName, pillarVariant, usePillarOptions, type PillarOption } from './lib/pillars';

/** The plan: a daily 15-minute session with 5 to 10 niche voices. */
const SUGGESTED_VOICES = '5 to 10';

interface VoiceFormProps {
  /** The voice to edit; none for a new one. */
  editing: WatchEntryVersioned | null;
  pillars: readonly PillarOption[];
  onDone: () => void;
}

interface VoiceErrors {
  name?: string;
  profileUrl?: string;
  note?: string;
}

/** Add a voice, or edit one (`PUT` replaces every field, with If-Match). */
function VoiceForm({ editing, pillars, onDone }: VoiceFormProps) {
  const client = useQueryClient();
  const formRef = useRef<HTMLFormElement>(null);
  const [name, setName] = useState(editing?.item.name ?? '');
  const [profileUrl, setProfileUrl] = useState(editing?.item.profileUrl ?? '');
  const [pillar, setPillar] = useState(editing?.item.pillar ?? 'none');
  const [note, setNote] = useState(editing?.item.note ?? '');
  const [errors, setErrors] = useState<VoiceErrors>({});
  const add = useAddVoice();
  const update = useUpdateVoice();
  const conflict = useConflict();

  function body(): WatchIn {
    return {
      name: name.trim(),
      profileUrl: profileUrl.trim() === '' ? null : profileUrl.trim(),
      pillar: pillar === 'none' ? null : pillar,
      note: note.trim() === '' ? null : note.trim(),
      active: editing?.item.active ?? true,
    };
  }

  function showErrors(next: VoiceErrors) {
    flushSync(() => {
      setErrors(next);
    });
    focusFirstInvalid(formRef.current);
  }

  function serverErrors(error: unknown) {
    const fields = fieldErrors(error);
    if (Object.keys(fields).length > 0) showErrors({ name: fields.name, profileUrl: fields.profileUrl, note: fields.note });
  }

  function save(etag: string | null) {
    if (!editing) return;
    update.mutate(
      { params: { path: { entry_id: editing.item.id } }, body: body(), ifMatch: etag },
      {
        onSuccess: () => {
          conflict.close();
          onDone();
        },
        onError: (error) => {
          if (!conflict.show(error)) serverErrors(error);
        },
      },
    );
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const next: VoiceErrors = {};
    if (name.trim() === '') next.name = 'Add their name.';
    else if (name.trim().length > 120) next.name = 'Keep the name to 120 characters.';
    if (profileUrl.trim() !== '' && !isHttpsUrl(profileUrl.trim())) next.profileUrl = 'Use their https profile link, or leave it empty.';
    if (note.trim().length > 500) next.note = 'Keep the note to 500 characters.';
    if (Object.keys(next).length > 0) {
      showErrors(next);
      return;
    }
    setErrors({});
    if (editing) {
      save(editing.etag);
      return;
    }
    add.mutate({ body: body() }, { onSuccess: onDone, onError: serverErrors });
  }

  const pending = add.isPending || update.isPending;

  return (
    <form ref={formRef} noValidate onSubmit={submit} className="grid gap-4 rounded-lg border border-line bg-sheet p-4">
      <Field label="Name" error={errors.name} required>
        <Input
          value={name}
          autoComplete="off"
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </Field>
      <Field label="Profile link" description="Optional. Their LinkedIn profile, to open it from the session." error={errors.profileUrl}>
        <Input
          type="url"
          inputMode="url"
          autoComplete="off"
          value={profileUrl}
          onChange={(event) => {
            setProfileUrl(event.target.value);
          }}
        />
      </Field>
      <Field label="Pillar" description="Optional. Which of your pillars they write about.">
        {(control) => (
          <Select value={pillar} onValueChange={setPillar}>
            <SelectTrigger {...control}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No pillar</SelectItem>
              {pillars.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  {option.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      <Field label="Note" description="Optional. Why they are worth your time." error={errors.note}>
        <Textarea
          rows={2}
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
          }}
        />
      </Field>
      <div className="flex gap-2 *:flex-1 sm:justify-end sm:*:flex-none">
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : editing ? 'Save changes' : 'Add the voice'}
        </Button>
      </div>
      <ConflictDialog
        {...conflict}
        subject={editing?.item.name ?? 'This voice'}
        saving={update.isPending}
        onKeepMine={() => {
          const etag = conflict.conflict?.etag;
          if (etag) save(etag);
          else {
            conflict.close();
            void client.invalidateQueries({ queryKey: engageKeys.watchlist });
          }
        }}
        onTakeTheirs={() => {
          conflict.close();
          void client.invalidateQueries({ queryKey: engageKeys.watchlist });
          onDone();
        }}
      />
    </form>
  );
}

interface VoiceRowProps {
  entry: WatchEntryVersioned;
  now: number;
  pillars: readonly PillarOption[];
  onEdit: () => void;
  onRemove: () => void;
}

function VoiceRow({ entry, now, pillars, onEdit, onRemove }: VoiceRowProps) {
  const { item } = entry;
  const mark = useMarkEngaged();
  const update = useUpdateVoice();
  const engaged = timeOf(item.lastEngagedAt);
  const today = engaged !== undefined && isSameDay(engaged, now);
  const since = sinceWords(item.lastEngagedAt, now);

  function setActive(active: boolean) {
    update.mutate({
      params: { path: { entry_id: item.id } },
      body: {
        name: item.name,
        profileUrl: item.profileUrl ?? null,
        pillar: item.pillar ?? null,
        note: item.note ?? null,
        active,
      },
      ifMatch: entry.etag,
    });
  }

  return (
    <li className="grid gap-3 border-b border-line py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <p className="font-semibold text-ink">{item.name}</p>
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
            {item.pillar ? <Badge variant={pillarVariant(item.pillar)}>{pillarName(pillars, item.pillar)}</Badge> : null}
            <span>
              {!item.active ? 'Paused' : today ? 'Engaged today' : since ? `Last engaged ${since}` : 'Not engaged yet'}
            </span>
          </p>
          {item.note ? <p className="text-sm text-ink-muted">{item.note}</p> : null}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={`More for ${item.name}`}>
              <Ellipsis aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onEdit}>
              <Pencil aria-hidden="true" />
              Edit
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => {
                setActive(!item.active);
              }}
            >
              {item.active ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
              {item.active ? 'Pause' : 'Resume'}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="danger" onSelect={onRemove}>
              <Trash2 aria-hidden="true" />
              Remove
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {item.active ? (
        <div className="flex flex-wrap items-center gap-2">
          {item.profileUrl ? (
            <Button asChild variant="secondary" size="sm">
              <a href={item.profileUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink aria-hidden="true" />
                Open profile{' '}
                <span className="sr-only">of {item.name} (opens in a new tab)</span>
              </a>
            </Button>
          ) : null}
          {today ? (
            <p className="inline-flex items-center gap-1.5 text-sm font-medium text-success">
              <CircleCheck aria-hidden="true" className="size-4" />
              Done for today
            </p>
          ) : (
            <Button
              size="sm"
              disabled={mark.isPending}
              onClick={() => {
                mark.mutate({ params: { path: { entry_id: item.id } } });
              }}
            >
              Mark engaged{' '}
              <span className="sr-only">with {item.name}</span>
            </Button>
          )}
        </div>
      ) : null}
    </li>
  );
}

/** Voices not engaged today first, longest since first; then today's. */
function sessionOrder(entries: readonly WatchEntryVersioned[], now: number): WatchEntryVersioned[] {
  const rank = (entry: WatchEntryVersioned) => {
    const engaged = timeOf(entry.item.lastEngagedAt);
    if (engaged === undefined) return -Infinity;
    return isSameDay(engaged, now) ? Infinity : engaged;
  };
  return [...entries].sort((a, b) => rank(a) - rank(b));
}

/**
 * The daily 15-minute session (Today > Engage): the watchlist of niche voices outside the owner's
 * network, who was engaged today, and adding, editing, pausing or removing voices.
 */
export function WatchlistSession() {
  const watchlist = useWatchlist();
  const pillars = usePillarOptions();
  const now = useNow();
  const headingId = useId();
  const [form, setForm] = useState<WatchEntryVersioned | 'new' | null>(null);
  const [removing, setRemoving] = useState<WatchEntryVersioned | null>(null);
  const remove = useRemoveVoice();

  if (watchlist.isPending) {
    return (
      <div aria-busy="true" className="grid gap-2">
        <span className="sr-only">Loading your watchlist</span>
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    );
  }
  if (watchlist.isError) {
    return (
      <ProblemAlert
        error={watchlist.error}
        title="Couldn’t load your watchlist."
        onRetry={() => void watchlist.refetch()}
        retryLabel="Try loading the watchlist again"
      />
    );
  }

  const active = sessionOrder(
    watchlist.data.filter((entry) => entry.item.active),
    now,
  );
  const paused = watchlist.data.filter((entry) => !entry.item.active);
  const done = active.filter((entry) => {
    const engaged = timeOf(entry.item.lastEngagedAt);
    return engaged !== undefined && isSameDay(engaged, now);
  }).length;
  const showForm = form !== null || watchlist.data.length === 0;

  return (
    <div className="grid gap-6">
      <section aria-labelledby={headingId} className="grid gap-3">
        <h3 id={headingId} className="text-sm font-semibold text-ink">
          Today’s session
        </h3>
        <p className="text-sm text-ink-muted">
          About 15 minutes: add a comment of substance to two or three posts from voices outside your network. Use the
          Reply tab to draft it.
        </p>
        {active.length > 0 ? (
          <div className="grid gap-1.5">
            <Progress
              value={done}
              max={active.length}
              tone={done === active.length ? 'success' : 'ink'}
              aria-label="Voices engaged today"
              getValueLabel={(value, max) => `${String(value)} of ${String(max)} voices`}
            />
            <p className="text-sm text-ink-muted">
              {done === active.length
                ? `All ${String(active.length)} voices engaged today.`
                : `${String(done)} of ${String(active.length)} voices engaged today.`}
            </p>
          </div>
        ) : null}
      </section>

      {watchlist.data.length === 0 ? (
        <div className="grid gap-1 text-sm">
          <p className="font-semibold text-ink">No voices yet.</p>
          <p className="text-ink-muted">
            Add {SUGGESTED_VOICES} niche voices outside your network: people whose posts you want to add to, not just
            like.
          </p>
        </div>
      ) : null}

      {active.length > 0 ? (
        <ul aria-label="Voices for today" className="border-t border-line">
          {active.map((entry) => (
            <VoiceRow
              key={entry.item.id}
              entry={entry}
              now={now}
              pillars={pillars}
              onEdit={() => {
                setForm(entry);
              }}
              onRemove={() => {
                setRemoving(entry);
              }}
            />
          ))}
        </ul>
      ) : null}

      {showForm ? (
        <VoiceForm
          key={form === 'new' || form === null ? 'new' : form.item.id}
          editing={form === 'new' || form === null ? null : form}
          pillars={pillars}
          onDone={() => {
            setForm(null);
          }}
        />
      ) : (
        <Button
          variant="secondary"
          className="w-fit"
          onClick={() => {
            setForm('new');
          }}
        >
          <Plus aria-hidden="true" />
          Add a voice
        </Button>
      )}

      {paused.length > 0 ? (
        <Collapsible>
          <CollapsibleTrigger asChild>
            <Button variant="link" size="sm">
              Paused voices ({paused.length})
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <ul aria-label="Paused voices" className="mt-2 border-t border-line">
              {paused.map((entry) => (
                <VoiceRow
                  key={entry.item.id}
                  entry={entry}
                  now={now}
                  pillars={pillars}
                  onEdit={() => {
                    setForm(entry);
                  }}
                  onRemove={() => {
                    setRemoving(entry);
                  }}
                />
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      ) : null}

      <AlertDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Remove ${removing?.item.name ?? 'this voice'}?`}</AlertDialogTitle>
            <AlertDialogDescription>
              They leave your watchlist. Comments you saved stay in Posted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep them</AlertDialogCancel>
            <AlertDialogAction
              variant="danger"
              onClick={() => {
                if (!removing) return;
                remove.mutate(
                  { params: { path: { entry_id: removing.item.id } }, ifMatch: removing.etag },
                  {
                    onSuccess: () => {
                      setRemoving(null);
                    },
                  },
                );
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
