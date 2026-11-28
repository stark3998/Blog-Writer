import { CalendarClock, CircleCheck, Rocket, Undo2 } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { ApiError } from '@/lib/api/errors';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { ApprovalHash } from '../components/approval-hash';
import { CHECK_LABELS } from '../model/labels';
import { canMove, type Machines } from '../model/machines';
import { formatMoment, formatSlot } from '../model/time';
import { goLivePath, isApproved, type CheckName, type Post } from '../model/types';

export interface ApprovalPanelProps {
  post: Post;
  etag: string | null;
  machines: Machines | undefined;
  /** The planner's zone, for the slot inputs. */
  timeZone: string | undefined;
  /** Why approval cannot be asked for now (unsaved edits, blocking lint), in words. */
  blockedBy: readonly string[];
  /** Checks the last refusal named; the checks panel marks them. */
  onFailingChecks: (failing: readonly string[]) => void;
  onRunChecks: () => void;
  onRender?: () => void;
  onReload: () => void;
}

/** The codes this panel explains in place (no toast for them). */
const HANDLED = [
  'checks_failing',
  'media_missing',
  'slot_conflict',
  'slot_in_past',
  'slot_after_expiry',
  'invalid_slot',
  'payload_mismatch',
  'blocklist_not_configured',
  'invalid_transition',
  'etag_mismatch',
  'validation_failed',
] as const;

function failingOf(error: unknown): string[] {
  if (!(error instanceof ApiError)) return [];
  const failing = error.problem?.extensions?.failing;
  return Array.isArray(failing) ? failing.filter((item): item is string => typeof item === 'string') : [];
}

function checkName(name: string): string {
  return Object.hasOwn(CHECK_LABELS, name) ? CHECK_LABELS[name as CheckName] : name;
}

const SLOT_CODES = ['slot_conflict', 'slot_in_past', 'slot_after_expiry', 'invalid_slot'] as const;

/**
 * Approval (`POST /api/posts/{id}/approve`), bound to the SHA-256 of the text and media: any
 * later edit clears it. With a slot, the post is staged an hour before and waits for your tap on
 * the go-live page. Works one-handed on a phone.
 */
export function ApprovalPanel({
  post,
  etag,
  machines,
  timeZone,
  blockedBy,
  onFailingChecks,
  onRunChecks,
  onRender,
  onReload,
}: ApprovalPanelProps) {
  const postId = post.id;
  const detail = queryKeys.posts.detail(postId);
  const [slotOpen, setSlotOpen] = useState(false);
  const [localDate, setLocalDate] = useState(post.slot?.localDate ?? '');
  const [localTime, setLocalTime] = useState(post.slot?.localTime ?? '');
  // After approving or withdrawing, the button that was pressed is gone: focus goes to what
  // comes next (Open go-live, or Approve again).
  const ids = useId();
  const goLiveId = `${ids}-go-live`;
  const approveId = `${ids}-approve`;
  const focusNext = useRef<string | null>(null);
  useEffect(() => {
    const target = focusNext.current;
    if (target === null) return;
    const element = document.getElementById(target);
    if (!element) return;
    focusNext.current = null;
    element.focus();
  });

  const approve = useApiMutation('post', '/api/posts/{post_id}/approve', {
    updates: () => detail,
    invalidates: [queryKeys.posts.lists(), queryKeys.today],
    successToast: 'Post approved.',
    handles: HANDLED,
    onError: (error) => {
      onFailingChecks(failingOf(error));
      if (isProblem(error, 'etag_mismatch')) onReload();
    },
    onSuccess: () => {
      onFailingChecks([]);
      setSlotOpen(false);
      focusNext.current = goLiveId;
    },
  });
  const unapprove = useApiMutation('post', '/api/posts/{post_id}/unapprove', {
    updates: () => detail,
    invalidates: [queryKeys.posts.lists(), queryKeys.today],
    successToast: 'Approval withdrawn. The post is a draft again.',
    errorToast: 'Couldn’t withdraw the approval.',
    onSuccess: () => {
      focusNext.current = approveId;
    },
  });

  const canApprove = post.status === 'draft' && canMove(machines, 'post', post.status, 'approved');
  const approved = isApproved(post.status);
  const error = approve.error;
  const slotError = SLOT_CODES.some((code) => isProblem(error, code)) ? problemMessage(error) : undefined;
  const slotChanged =
    slotOpen && localDate !== '' && localTime !== '' && (localDate !== post.slot?.localDate || localTime !== post.slot.localTime);

  function sendApprove() {
    approve.mutate({
      params: { path: { post_id: postId } },
      body: slotChanged ? { slot: { localDate, localTime, kind: 'custom' } } : {},
      ifMatch: etag,
    });
  }

  if (approved) {
    return (
      <div className="grid gap-4">
        <p className="flex items-center gap-2 text-base text-ink">
          <CircleCheck aria-hidden="true" className="size-5 text-success" />
          {post.approval ? `Approved ${formatMoment(post.approval.approvedAt)}.` : 'Approved.'}
        </p>
        {post.approval ? <ApprovalHash sha256={post.approval.sha256} /> : null}
        <p className="text-sm text-ink-muted">
          The approval covers this exact text, title, media and alt text. Any edit clears it.
        </p>
        <p className="flex items-start gap-2 text-sm text-ink">
          <CalendarClock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
          {post.slot
            ? `Goes live ${formatSlot(post.slot)}${post.status === 'staged' ? '. Ready: media uploaded and the LinkedIn sign-in checked.' : '. It is prepared an hour before.'}`
            : 'No slot yet. Publish it from the go-live page when you are ready.'}
        </p>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <Button asChild size="lg" className="w-full sm:w-auto">
            <Link id={goLiveId} to={goLivePath(post)}>
              <Rocket aria-hidden="true" />
              Open go-live
            </Link>
          </Button>
          {canMove(machines, 'post', post.status, 'draft') ? (
            <Button
              variant="secondary"
              size="lg"
              className="w-full sm:w-auto"
              disabled={unapprove.isPending}
              onClick={() => {
                unapprove.mutate({ params: { path: { post_id: postId } }, ifMatch: etag });
              }}
            >
              <Undo2 aria-hidden="true" />
              {unapprove.isPending ? 'Withdrawing…' : 'Withdraw approval'}
            </Button>
          ) : null}
        </div>
      </div>
    );
  }

  if (post.status !== 'draft') {
    return (
      <p className="text-sm text-ink-muted">
        {post.status === 'generating'
          ? 'Approval opens once the writer has finished.'
          : 'This post is past approval. Its go-live page shows where it stands.'}{' '}
        {post.status !== 'generating' ? (
          <Link to={goLivePath(post)} className="font-medium text-ink underline decoration-line-strong underline-offset-4">
            Open go-live
          </Link>
        ) : null}
      </p>
    );
  }

  const blocked = blockedBy.length > 0;

  return (
    <div className="grid gap-4">
      <p className="text-sm text-ink-muted">
        Approval binds the exact text and media with a SHA-256. Nothing goes to LinkedIn until you tap Publish on the
        go-live page, or post it yourself from the kit.
      </p>
      <div className="grid gap-2">
        <p className="flex items-start gap-2 text-sm text-ink">
          <CalendarClock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
          {post.slot ? `Slot: ${formatSlot(post.slot)}.` : 'No slot yet: approve without one, or pick one.'}
        </p>
        {slotOpen ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Date" error={slotError}>
              <Input
                type="date"
                value={localDate}
                onChange={(event) => {
                  setLocalDate(event.target.value);
                }}
              />
            </Field>
            <Field label="Time" description={timeZone ? `In ${timeZone}.` : undefined}>
              <Input
                type="time"
                value={localTime}
                onChange={(event) => {
                  setLocalTime(event.target.value);
                }}
              />
            </Field>
          </div>
        ) : (
          <div>
            <Button
              variant="link"
              size="sm"
              onClick={() => {
                setSlotOpen(true);
              }}
            >
              {post.slot ? 'Change the slot' : 'Pick a slot'}
            </Button>
          </div>
        )}
      </div>

      {error ? <ApproveError error={error} onRunChecks={onRunChecks} onRender={onRender} slotShown={slotOpen} /> : null}

      {blocked ? (
        <ul className="grid gap-1 text-sm text-ink-muted" aria-label="Before you can approve">
          {blockedBy.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
      <div>
        <Button
          id={approveId}
          size="lg"
          className="w-full sm:w-auto"
          disabled={!canApprove || blocked || approve.isPending}
          onClick={sendApprove}
        >
          <CircleCheck aria-hidden="true" />
          {approve.isPending ? 'Approving…' : slotChanged ? 'Approve for this slot' : 'Approve'}
        </Button>
      </div>
    </div>
  );
}

function ApproveError({
  error,
  onRunChecks,
  onRender,
  slotShown,
}: {
  error: unknown;
  onRunChecks: () => void;
  onRender?: () => void;
  slotShown: boolean;
}) {
  if (isProblem(error, 'checks_failing')) {
    const failing = failingOf(error);
    return (
      <Alert variant="error">
        <AlertTitle>Some checks fail or are out of date.</AlertTitle>
        <AlertDescription>
          <p>
            {failing.length > 0
              ? `Run them on the current text: ${failing.map(checkName).join(', ')}.`
              : 'Run the checks on the current text, then approve.'}
          </p>
        </AlertDescription>
        <AlertActions>
          <Button size="sm" variant="secondary" onClick={onRunChecks}>
            Run the checks
          </Button>
        </AlertActions>
      </Alert>
    );
  }
  if (isProblem(error, 'media_missing')) {
    return (
      <Alert variant="error">
        <AlertTitle>The slides aren’t rendered.</AlertTitle>
        <AlertDescription>
          <p>Approval covers the exact files that go out. Render them, then approve.</p>
        </AlertDescription>
        {onRender ? (
          <AlertActions>
            <Button size="sm" variant="secondary" onClick={onRender}>
              Render the slides
            </Button>
          </AlertActions>
        ) : null}
      </Alert>
    );
  }
  if (isProblem(error, 'blocklist_not_configured')) {
    return (
      <Alert variant="error">
        <AlertDescription>
          <p>{problemMessage(error)}</p>
        </AlertDescription>
        <AlertActions>
          <Button asChild size="sm" variant="secondary">
            <Link to="/settings/policy">Open Blocklist & policy</Link>
          </Button>
        </AlertActions>
      </Alert>
    );
  }
  if (SLOT_CODES.some((code) => isProblem(error, code)) && slotShown) return null;
  if (isProblem(error, 'etag_mismatch')) {
    return (
      <Alert variant="error">
        <AlertDescription>
          <p>The post changed since you opened it. It has been reloaded: check it and approve again.</p>
        </AlertDescription>
      </Alert>
    );
  }
  if (isProblem(error, 'payload_mismatch')) {
    return (
      <Alert variant="error">
        <AlertDescription>
          <p>The stored text and what LinkedIn would receive differ. Save the post again, then approve.</p>
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert variant="error">
      <AlertTitle>Couldn’t approve the post.</AlertTitle>
      <AlertDescription>
        <p>{problemMessage(error)}</p>
      </AlertDescription>
    </Alert>
  );
}
