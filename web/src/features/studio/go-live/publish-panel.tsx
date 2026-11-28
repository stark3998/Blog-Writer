import { AlarmClockPlus, CalendarArrowUp, CircleCheck, Package, Send } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router';
import { toast } from 'sonner';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Fieldset } from '@/components/ui/field';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { ApiError } from '@/lib/api/errors';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { toastSuccess } from '@/lib/api/toast';
import { cn } from '@/lib/cn';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { PostStatusBadge } from '../posts/components/status-badge';
import { useNow } from '@/lib/use-now';
import { blockerSentence, postErrorSentence } from '../posts/model/labels';
import { canMove } from '../posts/model/machines';
import { dayPhrase, formatMoment, formatSlot, formatTime, relativeTime } from '../posts/model/time';
import type { GoLiveView, Post } from '../posts/model/types';
import { useMachines } from '../posts/queries';

import { ActionBar } from './action-bar';
import { Proof } from './proof';
import { goLiveKey, kitKey } from './queries';
import { PanelHeading } from './panel-heading';

/** What the last tap ended in, said on the page. Nothing is ever sent again by itself. */
type Outcome =
  | { kind: 'rejected'; code: string }
  | { kind: 'lost'; at: number }
  | { kind: 'stale' }
  | { kind: 'refused'; error: unknown };

/** The codes the page explains itself: no toast for them. */
const PUBLISH_CODES = [
  'confirm_invalid',
  'not_approved',
  'approval_stale',
  'reconnect_required',
  'post_expired',
  'media_upload_failed',
  'media_missing',
  'media_not_uploaded',
  'document_title_missing',
  'too_many_images',
  'poll_invalid',
  'linkedin_unavailable',
  'vault_unavailable',
  'etag_mismatch',
] as const;

export const RESLOT_CODES = [
  'slot_conflict',
  'post_not_movable',
  'slot_in_past',
  'expires_first',
  'no_free_slot',
  'etag_mismatch',
  'slot_required',
  'invalid_slot',
] as const;

/** Secondary buttons share a row on a phone: their words may wrap rather than overflow. */
const rowButton = 'h-auto min-h-11 whitespace-normal px-2 leading-tight md:h-10 md:px-4';

/** "Post at 09:00 today", "Your 09:00 slot is open", "Ready to publish". */
function headline(post: Post, now: number): { title: string; lead: string } {
  const slot = post.slot;
  if (!slot) return { title: 'Ready to publish', lead: 'No slot is set: publish it when you can reply to comments.' };
  const due = Date.parse(slot.utc);
  const time = formatTime(slot.utc, slot.timezone);
  if (now < due) {
    return {
      title: `Post at ${time} ${dayPhrase(slot.utc, now, slot.timezone)}`,
      lead: `${formatSlot(slot)}, ${relativeTime(slot.utc, now)}.`,
    };
  }
  return { title: `Your ${time} slot is open`, lead: `It was due ${formatSlot(slot)}, ${relativeTime(slot.utc, now)}.` };
}

export interface PublishPanelProps {
  view: GoLiveView;
  /** Reads the go-live view again (a GET; never the publish POST). */
  onRefresh: () => void;
  /** True while that read is under way. */
  refreshing: boolean;
  /** When the view was last read (`dataUpdatedAt`): a read after a dropped tap is the server's word. */
  updatedAt: number;
}

/**
 * The one-click publish page (phone-first; the staging push opens it): the exact final render,
 * "can you reply in the next hour?", and Publish now, Snooze 30 min, Next slot, Switch to kit.
 *
 * Publishing happens only here, from a fresh tap, with the page's confirm token and the approval
 * hash it shows (contract 1.9, rule 4). One tap is one request: the button waits while it runs,
 * and whatever the answer, it is never sent again by itself.
 */
export function PublishPanel({ view, onRefresh, refreshing, updatedAt }: PublishPanelProps) {
  const post = view.post.item;
  const postId = post.id;
  const etag = view.post.etag;
  const now = useNow();
  const machines = useMachines();
  const replyId = useId();
  const [canReply, setCanReply] = useState<'yes' | 'no' | ''>('');
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  /** When the last tap went out or failed: the page offers the tap again only after a newer read. */
  const [tappedAt, setTappedAt] = useState<number | null>(null);
  const tapLock = useRef(false);
  const invalidates = [goLiveKey(postId), kitKey(postId), queryKeys.posts.lists(), queryKeys.today];

  const publish = useApiMutation('post', '/api/posts/{post_id}/publish', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates,
    handles: PUBLISH_CODES,
    errorToast: 'Couldn’t publish.',
  });
  const reslot = useApiMutation('post', '/api/posts/{post_id}/reslot', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates,
    handles: RESLOT_CODES,
    successToast: (data, variables) => {
      const slot = data.item.slot;
      if (!slot) return 'Moved.';
      return variables.body.action === 'snooze'
        ? `Snoozed to ${formatTime(slot.utc, slot.timezone)}.`
        : `Moved to ${formatSlot(slot)}.`;
    },
  });
  const mode = useApiMutation('post', '/api/posts/{post_id}/mode', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates,
    handles: ['not_approved', 'approval_stale', 'kit_in_progress', 'etag_mismatch'],
    successToast: 'Switched to the ready-to-post kit.',
  });

  const busy = publish.isPending || reslot.isPending || mode.isPending;
  const { title, lead } = headline(post, now);
  const blockers = view.reasons;
  const connected = view.connection.status === 'connected';
  const tokenDay = view.connection.tokenDay ?? 0;
  const token = view.confirmToken ?? null;
  const sha = view.approvalSha256 ?? null;
  const ready = view.canPublish && token !== null && sha !== null;
  // After a dropped connection, wait for the server's word before offering the tap again.
  const checked = outcome?.kind === 'lost' && updatedAt > outcome.at && !refreshing;
  const checking = outcome?.kind === 'lost' && !checked;
  const movable = post.status === 'approved' || canMove(machines, 'post', post.status, 'approved');
  const kitAllowed = canMove(machines, 'post', post.status, 'kit') && !blockers.includes('approval_stale');
  const waitFirst = canReply === 'no' && movable;
  // Fresh again once the server's view was read after the last tap settled (the publish answer
  // refreshes it before the mutation ends; a failure reads it again below).
  const fresh = tappedAt === null || (!publish.isPending && !refreshing && updatedAt > tappedAt);
  useEffect(() => {
    if (fresh) tapLock.current = false;
  }, [fresh]);

  function tapPublish() {
    // One tap, one request: another tap before the page is fresh again does nothing.
    if (tapLock.current || !fresh || publish.isPending || token === null || sha === null) return;
    tapLock.current = true;
    setTappedAt(Date.now());
    setOutcome(null);
    publish.mutate(
      { params: { path: { post_id: postId } }, body: { approvalSha256: sha, confirmToken: token }, ifMatch: etag },
      {
        onSuccess: (data) => {
          const status = data.item.status;
          if (status === 'posted') toastSuccess('Published on LinkedIn.');
          else if (status === 'needs_attention') toast.warning('LinkedIn didn’t answer. Check whether the post went out.');
          else setOutcome({ kind: 'rejected', code: data.item.lastError ?? 'publish_rejected' });
        },
        onError: (error) => {
          const at = Date.now();
          setTappedAt(at);
          if (error instanceof ApiError && error.kind === 'network') setOutcome({ kind: 'lost', at });
          else if (isProblem(error, 'etag_mismatch')) setOutcome({ kind: 'stale' });
          else setOutcome({ kind: 'refused', error });
          // Read the post again (a GET, never the POST): it says what happened.
          onRefresh();
        },
      },
    );
  }

  function move(action: 'snooze' | 'next_slot') {
    setOutcome(null);
    reslot.mutate({ params: { path: { post_id: postId } }, body: { action }, ifMatch: etag });
  }

  function switchToKit() {
    setOutcome(null);
    mode.mutate({ params: { path: { post_id: postId } }, body: { mode: 'kit' }, ifMatch: etag });
  }

  const publishButton = (primary: boolean) => (
    <Button
      size={primary ? 'lg' : 'md'}
      variant={primary ? 'primary' : 'secondary'}
      className={primary ? 'w-full md:w-auto' : rowButton}
      disabled={!ready || busy || checking || !fresh}
      aria-describedby={`${replyId}-note`}
      onClick={tapPublish}
    >
      <Send aria-hidden="true" />
      {publish.isPending ? 'Publishing…' : primary ? 'Publish now' : 'Publish anyway'}
    </Button>
  );
  const nextSlotButton = (primary: boolean) => (
    <Button
      size={primary ? 'lg' : 'md'}
      variant={primary ? 'primary' : 'secondary'}
      className={primary ? 'w-full md:w-auto' : rowButton}
      disabled={busy || !movable}
      onClick={() => {
        move('next_slot');
      }}
    >
      <CalendarArrowUp aria-hidden="true" />
      Next slot
    </Button>
  );

  return (
    <div className="grid gap-8">
      <header className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <PostStatusBadge status={post.status} />
          {post.status === 'staged' && post.staging?.stagedAt ? (
            <span className="text-sm text-ink-muted">{`Prepared ${formatMoment(post.staging.stagedAt)}`}</span>
          ) : null}
        </div>
        <PanelHeading postId={postId}>{title}</PanelHeading>
        <p className="text-lg text-ink-muted">{lead}</p>
      </header>

      <Outcomes
        outcome={outcome}
        checking={checking}
        stillApproved={checked}
        onCheck={onRefresh}
      />

      {post.lastError && outcome === null ? (
        <Alert variant="warn">
          <AlertTitle>The last attempt didn’t go through.</AlertTitle>
          <AlertDescription>
            <p>{postErrorSentence(post.lastError)}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      {post.goLiveMode === 'kit' && view.canPublish ? (
        <Alert>
          <AlertTitle>This post is set to go out by hand.</AlertTitle>
          <AlertDescription>
            <p>Switch it to the ready-to-post kit when you are ready, or publish it here with one tap after all.</p>
          </AlertDescription>
          {kitAllowed ? (
            <AlertActions>
              <Button size="sm" variant="secondary" disabled={busy} onClick={switchToKit}>
                <Package aria-hidden="true" />
                Switch to kit
              </Button>
            </AlertActions>
          ) : null}
        </Alert>
      ) : null}

      {!view.canPublish ? (
        <Alert variant="warn">
          <AlertTitle>Publish is off for now.</AlertTitle>
          <AlertDescription>
            <ul className="grid gap-1">
              {(blockers.length > 0 ? blockers : ['unknown']).map((code) => (
                <li key={code}>{blockerSentence(code)}</li>
              ))}
            </ul>
            {!connected ? (
              <p>
                <Link to="/settings/integrations" className="font-medium text-ink underline underline-offset-4">
                  Open Settings › Integrations
                </Link>
              </p>
            ) : null}
          </AlertDescription>
          {kitAllowed ? (
            <AlertActions>
              <Button size="sm" variant="secondary" disabled={busy} onClick={switchToKit}>
                <Package aria-hidden="true" />
                Post it yourself with the kit
              </Button>
            </AlertActions>
          ) : null}
        </Alert>
      ) : null}

      {connected && tokenDay >= 50 ? (
        <Alert>
          <AlertDescription>
            <p>
              {`Your LinkedIn sign-in is on day ${String(tokenDay)} of 60. Reconnect soon in `}
              <Link to="/settings/integrations" className="font-medium text-ink underline underline-offset-4">
                Settings › Integrations
              </Link>
              .
            </p>
          </AlertDescription>
        </Alert>
      ) : null}

      <Proof view={view} />

      <div className="grid gap-3">
        <Fieldset legend="Can you reply to comments in the next hour?">
          {(group) => (
            <RadioGroup
              {...group}
              value={canReply}
              onValueChange={(value) => {
                if (value === 'yes' || value === 'no') setCanReply(value);
              }}
              className="grid gap-2 sm:grid-cols-2"
            >
              {(
                [
                  ['yes', 'Yes, I can reply'],
                  ['no', 'Not in the next hour'],
                ] as const
              ).map(([value, label]) => (
                <div
                  key={value}
                  className={cn(
                    'relative flex min-h-11 items-center gap-3 rounded-lg border bg-sheet px-4 py-2',
                    canReply === value ? 'border-ink' : 'border-line',
                  )}
                >
                  <RadioGroupItem id={`${replyId}-${value}`} value={value} />
                  <label
                    htmlFor={`${replyId}-${value}`}
                    className="cursor-pointer text-base text-ink after:absolute after:inset-0 after:rounded-lg"
                  >
                    {label}
                  </label>
                </div>
              ))}
            </RadioGroup>
          )}
        </Fieldset>
        <p id={`${replyId}-note`} className="text-sm text-ink-muted">
          {canReply === 'no'
            ? 'Then let it wait: the first hour of replies is what the feed rewards. Take the next free slot or snooze it.'
            : canReply === 'yes'
              ? 'Good. Reply reminders come at 15 minutes, 1 hour and 3 hours after it goes out.'
              : 'Replies in the first hour carry a post. If you can’t reply, snooze it or take the next slot.'}
        </p>
      </div>

      {reslot.error || mode.error ? (
        <Alert variant="error">
          <AlertTitle>{reslot.error ? 'Couldn’t move the post.' : 'Couldn’t switch to the kit.'}</AlertTitle>
          <AlertDescription>
            <p>{problemMessage(reslot.error ?? mode.error)}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <ActionBar
        label="Go-live actions"
        primary={waitFirst ? nextSlotButton(true) : publishButton(true)}
        secondary={
          <>
            <Button
              variant="secondary"
              className={rowButton}
              disabled={busy || !movable}
              onClick={() => {
                move('snooze');
              }}
            >
              <AlarmClockPlus aria-hidden="true" />
              Snooze 30 min
            </Button>
            {waitFirst ? publishButton(false) : nextSlotButton(false)}
            <Button variant="secondary" className={rowButton} disabled={busy || !kitAllowed} onClick={switchToKit}>
              <Package aria-hidden="true" />
              Switch to kit
            </Button>
          </>
        }
        note={
          publish.isPending ? (
            <span role="status">Publishing to LinkedIn. Keep this page open; it takes a few seconds.</span>
          ) : ready ? (
            <span className="inline-flex items-center gap-1.5">
              <CircleCheck aria-hidden="true" className="size-4 text-success" />
              One tap publishes exactly this post, once.
            </span>
          ) : null
        }
      />
    </div>
  );
}

function Outcomes({
  outcome,
  checking,
  stillApproved,
  onCheck,
}: {
  outcome: Outcome | null;
  checking: boolean;
  stillApproved: boolean;
  onCheck: () => void;
}) {
  if (outcome === null) return null;
  switch (outcome.kind) {
    case 'rejected':
      return (
        <Alert variant="error">
          <AlertTitle>LinkedIn refused the post.</AlertTitle>
          <AlertDescription>
            <p>{postErrorSentence(outcome.code)}</p>
            <p>Nothing was posted. Tap Publish again once it is fixed, or use the kit.</p>
          </AlertDescription>
        </Alert>
      );
    case 'lost':
      return (
        <Alert variant="error">
          <AlertTitle>The connection dropped during the tap.</AlertTitle>
          <AlertDescription>
            <p>
              {checking
                ? 'Checking with the server whether the post went out. Don’t tap again yet.'
                : stillApproved
                  ? 'The server shows it as not published. Tap Publish again if you still want it out now.'
                  : 'The post may have gone out. Check again before you tap.'}
            </p>
          </AlertDescription>
          <AlertActions>
            <Button size="sm" variant="secondary" disabled={checking} onClick={onCheck}>
              {checking ? 'Checking…' : 'Check again'}
            </Button>
          </AlertActions>
        </Alert>
      );
    case 'stale':
      return (
        <Alert variant="error">
          <AlertTitle>Another tap got there first.</AlertTitle>
          <AlertDescription>
            <p>This post changed or is already being published from another tab or device. The page now shows where it stands.</p>
          </AlertDescription>
        </Alert>
      );
    case 'refused':
      return (
        <Alert variant="error">
          <AlertTitle>{isProblem(outcome.error, 'confirm_invalid') ? 'This page was open too long.' : 'Not published.'}</AlertTitle>
          <AlertDescription>
            <p>
              {isProblem(outcome.error, 'confirm_invalid')
                ? 'It has been refreshed. Check the post, then tap Publish again.'
                : problemMessage(outcome.error)}
            </p>
          </AlertDescription>
        </Alert>
      );
  }
}
