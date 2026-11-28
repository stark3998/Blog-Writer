import { ChevronDown, CircleCheck, ExternalLink, PenLine, Trash2 } from 'lucide-react';
import { useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button, buttonVariants } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ApiError } from '@/lib/api/errors';
import { confidentialFindings, isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { PostStatusBadge } from '../posts/components/status-badge';
import { useNow } from '@/lib/use-now';
import { estimatePayloadUnits, formatCount, MAX_PAYLOAD_UNITS } from '../posts/model/text';
import { formatMoment, relativeTime } from '../posts/model/time';
import type { Finding, GoLiveView } from '../posts/model/types';

import { Proof } from './proof';
import { goLiveKey, kitKey } from './queries';
import { PanelHeading } from './panel-heading';

const LIVE_CODES = [
  'confirm_invalid',
  'not_live',
  'no_linkedin_urn',
  'lint_failed',
  'linkedin_rejected',
  'linkedin_unavailable',
  'reconnect_required',
  'vault_unavailable',
  'confidential_text',
  'blocklist_not_configured',
  'etag_mismatch',
] as const;

function lintFindings(error: unknown): Finding[] {
  if (!(error instanceof ApiError) || !isProblem(error, 'lint_failed')) return [];
  const findings = error.problem?.extensions?.findings;
  return Array.isArray(findings) ? (findings as Finding[]) : [];
}

/**
 * A post that is live: when it went out, the link, what went out, and the two things the app can
 * still do on LinkedIn for you: edit the text (lint must pass; one update) or delete it. Both are
 * fresh owner taps with the page's confirm token.
 */
export function LivePanel({ view, onRefresh }: { view: GoLiveView; onRefresh: () => void }) {
  const post = view.post.item;
  const postId = post.id;
  const now = useNow();
  const viaApi = Boolean(post.linkedinUrn);
  const text = post.finalText ?? post.displayText;
  const invalidates = [goLiveKey(postId), kitKey(postId), queryKeys.posts.lists(), queryKeys.today];
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const conflict = useConflict();

  const edit = useApiMutation('patch', '/api/posts/{post_id}/linkedin', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates,
    handles: LIVE_CODES,
    successToast: 'The live post is updated on LinkedIn.',
  });
  const remove = useApiMutation('post', '/api/posts/{post_id}/linkedin/delete', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates,
    handles: LIVE_CODES,
    successToast: 'Deleted from LinkedIn.',
  });

  const token = view.confirmToken ?? null;
  const findings = lintFindings(edit.error);
  const confidential = confidentialFindings(edit.error).length > 0;
  const units = estimatePayloadUnits(draft, post.hashtags ?? []);

  function sendEdit(etag: string | null) {
    if (token === null) return;
    edit.mutate(
      { params: { path: { post_id: postId } }, body: { displayText: draft, confirmToken: token }, ifMatch: etag },
      {
        onSuccess: () => {
          setEditing(false);
          conflict.close();
        },
        onError: (error) => {
          if (conflict.show(error)) onRefresh();
          else if (isProblem(error, 'confirm_invalid')) onRefresh();
        },
      },
    );
  }

  function submitEdit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    sendEdit(view.post.etag);
  }

  return (
    <div className="grid gap-8">
      <header className="grid gap-2">
        <div>
          <PostStatusBadge status={post.status} />
        </div>
        <PanelHeading postId={postId}>Posted on LinkedIn</PanelHeading>
        <p className="text-lg text-ink-muted">
          {post.postedAt ? `Went live ${formatMoment(post.postedAt)}, ${relativeTime(post.postedAt, now)}.` : 'It is live.'}
        </p>
        {post.postUrl ? (
          <div className="pt-2">
            <a
              href={post.postUrl}
              target="_blank"
              rel="noopener noreferrer"
              className={buttonVariants({ size: 'lg' })}
            >
              <ExternalLink aria-hidden="true" />
              Open on LinkedIn
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </div>
        ) : null}
      </header>

      <Alert variant="success">
        <AlertTitle>Stay close for the first hour.</AlertTitle>
        <AlertDescription>
          <p>Reply reminders come at 15 minutes, 1 hour and 3 hours; the numbers form at 48 hours and 7 days.</p>
        </AlertDescription>
      </Alert>

      <Proof view={view} text={text} title="What went out" />

      {viaApi ? (
        <section className="grid gap-4" aria-label="Change the live post">
          <Collapsible
            open={editing}
            onOpenChange={(open) => {
              setEditing(open);
              if (open) setDraft(text);
            }}
          >
            <CollapsibleTrigger asChild>
              <Button variant="secondary">
                <PenLine aria-hidden="true" />
                Edit the live post
                <ChevronDown aria-hidden="true" className={editing ? 'rotate-180' : undefined} />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <form onSubmit={submitEdit} className="mt-4 grid max-w-measure gap-3">
                <Field
                  label="Live text"
                  description="Changes the text on LinkedIn once. The lint must pass; links and hashtags follow the same rules."
                  error={
                    confidential
                      ? 'Part of this text matches the blocklist. Rephrase it.'
                      : findings.length > 0
                        ? 'Fix the problems below first.'
                        : undefined
                  }
                >
                  <Textarea
                    value={draft}
                    onChange={(event) => {
                      setDraft(event.target.value);
                    }}
                    className="min-h-48"
                  />
                </Field>
                <p className={units > MAX_PAYLOAD_UNITS ? 'text-sm font-medium text-danger' : 'text-sm text-ink-muted'}>
                  {`About ${formatCount(units)} of ${formatCount(MAX_PAYLOAD_UNITS)} characters.`}
                </p>
                {findings.length > 0 ? (
                  <ul className="grid gap-1 text-sm" aria-label="Lint findings">
                    {findings.map((finding, index) => (
                      <li
                        key={`${finding.code}-${String(index)}`}
                        className={finding.severity === 'block' ? 'font-medium text-danger' : 'text-warn'}
                      >
                        {finding.message}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {edit.error && findings.length === 0 && !confidential && !isProblem(edit.error, 'etag_mismatch') ? (
                  <Alert variant="error">
                    <AlertDescription>
                      <p>{problemMessage(edit.error)}</p>
                    </AlertDescription>
                  </Alert>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button type="submit" disabled={edit.isPending || token === null || draft.trim() === '' || draft === text}>
                    {edit.isPending ? 'Updating on LinkedIn…' : 'Update on LinkedIn'}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setEditing(false);
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            </CollapsibleContent>
          </Collapsible>

          <div>
            <Button
              variant="ghost"
              className="text-danger hover:text-danger"
              disabled={token === null || remove.isPending}
              onClick={() => {
                setDeleteOpen(true);
              }}
            >
              <Trash2 aria-hidden="true" />
              Delete from LinkedIn…
            </Button>
          </div>
          {remove.error ? (
            <Alert variant="error">
              <AlertTitle>Couldn’t delete the post.</AlertTitle>
              <AlertDescription>
                <p>{problemMessage(remove.error)}</p>
              </AlertDescription>
            </Alert>
          ) : null}
        </section>
      ) : (
        <p className="text-sm text-ink-muted">
          <CircleCheck aria-hidden="true" className="mr-1.5 inline size-4 align-[-0.2em] text-ink-subtle" />
          You posted this one by hand, so edit or delete it in LinkedIn itself.
        </p>
      )}

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this post from LinkedIn?</AlertDialogTitle>
            <AlertDialogDescription>
              It disappears from LinkedIn with its reactions and comments. This can’t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <Button
              variant="danger"
              disabled={remove.isPending || token === null}
              onClick={() => {
                if (token === null) return;
                remove.mutate(
                  { params: { path: { post_id: postId } }, body: { confirmToken: token }, ifMatch: view.post.etag },
                  {
                    onSettled: () => {
                      setDeleteOpen(false);
                    },
                    onError: () => {
                      onRefresh();
                    },
                  },
                );
              }}
            >
              {remove.isPending ? 'Deleting…' : 'Delete from LinkedIn'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ConflictDialog
        {...conflict}
        subject="The live post"
        mine={draft}
        theirs={text}
        saving={edit.isPending}
        onKeepMine={() => {
          const current = conflict.conflict;
          if (current?.etag) sendEdit(current.etag);
          else onRefresh();
        }}
        onTakeTheirs={() => {
          setDraft(text);
          conflict.close();
          onRefresh();
        }}
      />
    </div>
  );
}
