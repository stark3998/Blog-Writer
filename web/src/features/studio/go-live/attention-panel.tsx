import { ExternalLink, PenLine } from 'lucide-react';
import { useId, useState, type SubmitEvent } from 'react';
import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button, buttonVariants } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import { cn } from '@/lib/cn';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { PostStatusBadge } from '../posts/components/status-badge';
import { useNow } from '@/lib/use-now';
import { postErrorSentence } from '../posts/model/labels';
import { formatMoment, relativeTime } from '../posts/model/time';
import { postsPath, type GoLiveView } from '../posts/model/types';

import { ActionBar } from './action-bar';
import { isLinkedInPostUrl } from './kit-panel';
import { Proof } from './proof';
import { goLiveKey, kitKey, useKit } from './queries';
import { PanelHeading } from './panel-heading';

/**
 * A post that needs you. After a publish attempt whose answer never came (a timeout, a 5xx, a
 * restart: intent `unknown`), the app cannot know whether LinkedIn created the post, so it asks:
 * check LinkedIn and say what you saw (`POST /api/posts/{id}/resolve`). It never tries again by
 * itself. Other failures (the writer stopped) send you back to the composer.
 */
export function AttentionPanel({ view }: { view: GoLiveView }) {
  const post = view.post.item;
  const intent = post.intent;
  const unknownAttempt = intent?.status === 'unknown';

  if (!unknownAttempt) {
    return (
      <div className="grid gap-6">
        <header className="grid gap-2">
          <div>
            <PostStatusBadge status={post.status} />
          </div>
          <PanelHeading postId={post.id}>This post needs you</PanelHeading>
          <p className="text-lg text-ink-muted">
            {post.lastError ? postErrorSentence(post.lastError) : 'Something stopped it before it was ready.'}
          </p>
        </header>
        <div>
          <Link to={postsPath(post)} className={buttonVariants({ size: 'lg' })}>
            <PenLine aria-hidden="true" />
            Open the post
          </Link>
        </div>
      </div>
    );
  }
  return <ResolveAttempt view={view} />;
}

function ResolveAttempt({ view }: { view: GoLiveView }) {
  const post = view.post.item;
  const postId = post.id;
  const now = useNow();
  const formId = useId();
  const [answer, setAnswer] = useState<'posted' | 'not_posted' | ''>('');
  const [url, setUrl] = useState('');
  const [urlError, setUrlError] = useState<string | undefined>(undefined);
  // The server's "Open LinkedIn" address (the kit's), so the app ships no LinkedIn URL of its own.
  const kit = useKit(postId);
  const linkedInUrl = kit.data?.openLinkedInUrl;
  const resolve = useApiMutation('post', '/api/posts/{post_id}/resolve', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates: [goLiveKey(postId), kitKey(postId), queryKeys.posts.lists(), queryKeys.today],
    handles: ['not_a_publish_attempt', 'post_url_invalid', 'validation_failed', 'etag_mismatch', 'invalid_transition'],
    successToast: (data) =>
      data.item.status === 'posted'
        ? 'Marked as posted. Reply reminders are on their way.'
        : 'Marked as not posted. Publish it again when you are ready.',
  });
  const attemptAt = post.intent?.createdAt;
  const reason = post.intent?.errorCode ?? post.lastError ?? 'publish_unknown';
  const serverUrlError =
    fieldErrors(resolve.error).postUrl ??
    (isProblem(resolve.error, 'post_url_invalid') ? 'Paste the post’s linkedin.com link.' : undefined);

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (answer === '') return;
    const trimmed = url.trim();
    if (answer === 'posted' && trimmed !== '' && !isLinkedInPostUrl(trimmed)) {
      setUrlError('Paste the post’s link from LinkedIn (a linkedin.com address), or leave it empty.');
      return;
    }
    setUrlError(undefined);
    resolve.mutate({
      params: { path: { post_id: postId } },
      body: answer === 'posted' && trimmed !== '' ? { outcome: 'posted', postUrl: trimmed } : { outcome: answer },
      ifMatch: view.post.etag,
    });
  }

  return (
    <div className="grid gap-8">
      <header className="grid gap-2">
        <div>
          <PostStatusBadge status={post.status} />
        </div>
        <PanelHeading postId={postId}>Check LinkedIn: did your post go out?</PanelHeading>
        <p className="text-lg text-ink-muted">
          {`${postErrorSentence(reason)}${attemptAt ? ` You tapped Publish ${formatMoment(attemptAt)}, ${relativeTime(attemptAt, now)}.` : ''}`}
        </p>
      </header>

      <Alert variant="warn">
        <AlertTitle>The app won’t try again by itself.</AlertTitle>
        <AlertDescription>
          <p>
            Publishing twice would post twice. Look at your recent activity on LinkedIn (your profile, then Activity), then tell the app what you see.
          </p>
        </AlertDescription>
      </Alert>

      {linkedInUrl ? (
        <div>
          <a
            href={linkedInUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonVariants({ variant: 'secondary' })}
          >
            <ExternalLink aria-hidden="true" />
            Open LinkedIn
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        </div>
      ) : null}

      <form id={formId} onSubmit={submit} noValidate className="grid max-w-measure gap-4">
        <Fieldset legend="What did you see?" required>
          {(group) => (
            <RadioGroup
              {...group}
              value={answer}
              onValueChange={(value) => {
                if (value === 'posted' || value === 'not_posted') setAnswer(value);
              }}
              className="grid gap-2"
            >
              {(
                [
                  ['posted', 'It’s on LinkedIn'],
                  ['not_posted', 'It didn’t go out'],
                ] as const
              ).map(([value, label]) => (
                <div
                  key={value}
                  className={cn(
                    'relative flex min-h-11 items-center gap-3 rounded-lg border bg-sheet px-4 py-2',
                    answer === value ? 'border-ink' : 'border-line',
                  )}
                >
                  <RadioGroupItem id={`${formId}-${value}`} value={value} />
                  <label
                    htmlFor={`${formId}-${value}`}
                    className="cursor-pointer text-base text-ink after:absolute after:inset-0 after:rounded-lg"
                  >
                    {label}
                  </label>
                </div>
              ))}
            </RadioGroup>
          )}
        </Fieldset>
        {answer === 'posted' ? (
          <Field
            label="Post link"
            description="Optional, but it lets the app edit or delete the post later and match its numbers."
            error={urlError ?? serverUrlError}
          >
            <Input
              type="url"
              inputMode="url"
              autoComplete="off"
              value={url}
              placeholder="linkedin.com/feed/update/…"
              onChange={(event) => {
                setUrl(event.target.value);
              }}
            />
          </Field>
        ) : null}
        {answer === 'not_posted' ? (
          <p className="text-sm text-ink-muted">
            It goes back to Approved (or to Draft if it changed). Nothing is sent until you tap Publish again.
          </p>
        ) : null}
        {resolve.error && !serverUrlError ? (
          <Alert variant="error">
            <AlertDescription>
              <p>{problemMessage(resolve.error)}</p>
            </AlertDescription>
          </Alert>
        ) : null}
      </form>

      <Proof view={view} title="The post you tried to publish" />

      <ActionBar
        label="Resolve the publish attempt"
        primary={
          <Button
            type="submit"
            form={formId}
            size="lg"
            className="w-full md:w-auto"
            disabled={answer === '' || resolve.isPending}
          >
            {resolve.isPending ? 'Saving…' : 'Save what I saw'}
          </Button>
        }
      />
    </div>
  );
}
