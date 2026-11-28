import { CalendarClock, Check, CircleCheck, Download, ExternalLink, FileText, Undo2 } from 'lucide-react';
import { useId, useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button, buttonVariants } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import { cn } from '@/lib/cn';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { CopyButton } from '../posts/components/copy-button';
import { KitStateBadge } from '../posts/components/status-badge';
import { KIT_STATE_LABELS, KIT_STATES } from '../posts/model/labels';
import { canMove } from '../posts/model/machines';
import { formatBytes, mediaKindLabel } from '../posts/model/media';
import { deviceTimeZone, formatInstant, fromLocalInput, toLocalInput } from '../posts/model/time';
import type { GoLiveView, KitIn, KitState, KitView } from '../posts/model/types';
import { useMachines } from '../posts/queries';

import { ActionBar } from './action-bar';
import { goLiveKey, kitKey, useKit } from './queries';
import { PanelHeading } from './panel-heading';

const KIT_CODES = [
  'invalid_transition',
  'not_in_kit',
  'post_url_invalid',
  'validation_failed',
  'etag_mismatch',
  'invalid_kit_state',
  'kit_in_progress',
  'approval_stale',
  'not_approved',
] as const;

/** A LinkedIn post link, as the server accepts it (`is_linkedin_url`). */
export function isLinkedInPostUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return url.protocol === 'https:' && (url.hostname === 'www.linkedin.com' || url.hostname === 'linkedin.com');
  } catch {
    return false;
  }
}

function KitProgress({ state }: { state: KitState }) {
  const current = KIT_STATES.indexOf(state);
  return (
    <ol aria-label="Kit progress" className="grid gap-2 sm:grid-cols-4">
      {KIT_STATES.map((step, index) => {
        const done = index < current;
        const here = index === current;
        return (
          <li
            key={step}
            aria-current={here ? 'step' : undefined}
            className={cn(
              'flex items-center gap-2 rounded-md border px-3 py-2 text-sm',
              here ? 'border-ink font-semibold text-ink' : 'border-line text-ink-muted',
            )}
          >
            {done ? (
              <Check aria-hidden="true" className="size-4 shrink-0 text-success" />
            ) : (
              <span aria-hidden="true" className="w-4 shrink-0 text-center tabular-nums">
                {index + 1}
              </span>
            )}
            {KIT_STATE_LABELS[step]}
            {done ? <span className="sr-only"> (done)</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The ready-to-post kit (the post goes out by hand): the display text, document title and alt
 * texts to copy exactly, the files to download, the four steps, "Open LinkedIn", and the kit's
 * states: draft saved in LinkedIn, scheduled in LinkedIn at a time you enter, posted with its
 * link and the final text (prefilled, so you correct only what changed). The app keeps the record.
 */
export function KitPanel({ view }: { view: GoLiveView }) {
  const post = view.post.item;
  const postId = post.id;
  const etag = view.post.etag;
  const kit = useKit(postId);
  const machines = useMachines();
  const headingId = useId();
  const state: KitState = kit.data?.state ?? post.kit?.state ?? 'prepared';
  const invalidates = [goLiveKey(postId), kitKey(postId), queryKeys.posts.lists(), queryKeys.today];

  const move = useApiMutation('post', '/api/posts/{post_id}/kit', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates,
    handles: KIT_CODES,
    successToast: (_data, variables) => {
      switch (variables.body.to) {
        case 'native_draft_saved':
          return 'Marked as saved in LinkedIn.';
        case 'natively_scheduled':
          return `Scheduled for ${formatInstant(variables.body.scheduledAt ?? null)}.`;
        case 'posted':
          return 'Marked as posted. Reply reminders are on their way.';
      }
    },
  });
  const oneClick = useApiMutation('post', '/api/posts/{post_id}/mode', {
    updates: () => queryKeys.posts.detail(postId),
    invalidates,
    handles: KIT_CODES,
    successToast: 'Back to one-click publishing.',
  });

  if (kit.isPending) {
    return (
      <div className="grid gap-4">
        <p role="status" className="sr-only">
          Loading the kit
        </p>
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-40 w-full max-w-measure" />
      </div>
    );
  }
  if (kit.isError) {
    return (
      <ProblemAlert
        error={kit.error}
        title="Couldn’t load the kit."
        onRetry={() => void kit.refetch()}
        retryLabel="Try loading the kit again"
      />
    );
  }

  const data: KitView = kit.data;
  const can = (to: KitState) => canMove(machines, 'kit', state, to);
  const busy = move.isPending || oneClick.isPending;
  const send = (body: KitIn) => {
    move.mutate({ params: { path: { post_id: postId } }, body, ifMatch: etag });
  };

  return (
    <div className="grid gap-8">
      <header className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <KitStateBadge state={state} />
        </div>
        <PanelHeading postId={postId}>Post it yourself</PanelHeading>
        <p className="text-lg text-ink-muted">
          {state === 'natively_scheduled' && data.scheduledAt
            ? `Scheduled in LinkedIn for ${formatInstant(data.scheduledAt)}. Mark it posted once it is out.`
            : 'Copy the text, add the files, post or schedule it in LinkedIn, then tell the app what you did.'}
        </p>
      </header>

      <KitProgress state={state} />

      {move.error || oneClick.error ? (
        <Alert variant="error">
          <AlertTitle>Couldn’t save that.</AlertTitle>
          <AlertDescription>
            <p>{problemMessage(move.error ?? oneClick.error)}</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <section aria-labelledby={`${headingId}-copy`} className="grid gap-4">
        <h3 id={`${headingId}-copy`} className="font-serif text-xl font-medium text-ink">
          Copy and download
        </h3>
        <div className="grid gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium text-ink">Post text</p>
            <CopyButton text={data.displayText} label="Copy the post text" copiedMessage="Post text copied.">
              Copy text
            </CopyButton>
          </div>
          <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-line bg-sunken p-3 font-sans text-sm text-ink">
            {data.displayText}
          </pre>
        </div>
        {data.documentTitle ? (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
            <div className="grid min-w-0 gap-0.5">
              <p className="font-medium text-ink">Document title</p>
              <p className="text-sm text-ink-muted">{data.documentTitle}</p>
            </div>
            <CopyButton
              text={data.documentTitle}
              size="sm"
              label="Copy the document title"
              copiedMessage="Document title copied."
            >
              Copy title
            </CopyButton>
          </div>
        ) : null}
        {data.altTexts.map((alt, index) =>
          alt ? (
            <div
              key={`${String(index)}-${alt}`}
              className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3"
            >
              <div className="grid min-w-0 gap-0.5">
                <p className="font-medium text-ink">
                  {data.altTexts.length > 1 ? `Alt text, image ${String(index + 1)}` : 'Alt text'}
                </p>
                <p className="text-sm text-ink-muted">{alt}</p>
              </div>
              <CopyButton
                text={alt}
                size="sm"
                label={data.altTexts.length > 1 ? `Copy the alt text of image ${String(index + 1)}` : 'Copy the alt text'}
                copiedMessage="Alt text copied."
              >
                Copy alt text
              </CopyButton>
            </div>
          ) : null,
        )}
        {data.downloads.length > 0 ? (
          <ul className="grid gap-2 border-t border-line pt-3" aria-label="Files to download">
            {data.downloads.map((download) => (
              <li key={download.url} className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2 text-sm text-ink">
                  <FileText aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
                  <span className="truncate">{download.filename}</span>
                  <span className="shrink-0 text-ink-muted">
                    {`${mediaKindLabel(download.media.contentType)}, ${formatBytes(download.media.sizeBytes)}`}
                  </span>
                </span>
                <a
                  href={download.url}
                  download={download.filename}
                  className={buttonVariants({ variant: 'secondary', size: 'sm' })}
                >
                  <Download aria-hidden="true" />
                  {`Download ${download.media.contentType === 'application/pdf' ? 'the PDF' : 'the image'}`}
                </a>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section aria-labelledby={`${headingId}-steps`} className="grid gap-4">
        <h3 id={`${headingId}-steps`} className="font-serif text-xl font-medium text-ink">
          Post it in LinkedIn
        </h3>
        <Checklist steps={data.checklist} />
        <div>
          <a
            href={data.openLinkedInUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonVariants({ variant: 'secondary' })}
          >
            <ExternalLink aria-hidden="true" />
            Open LinkedIn
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        </div>
      </section>

      <section aria-labelledby={`${headingId}-tell`} className="grid gap-4">
        <h3 id={`${headingId}-tell`} className="font-serif text-xl font-medium text-ink">
          Tell the app what you did
        </h3>
        <div className="grid gap-3 sm:flex sm:flex-wrap">
          {can('native_draft_saved') ? (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                send({ to: 'native_draft_saved' });
              }}
            >
              <FileText aria-hidden="true" />
              {state === 'natively_scheduled' ? 'I unscheduled it: it’s a draft in LinkedIn' : 'I saved it as a draft in LinkedIn'}
            </Button>
          ) : null}
          {state === 'prepared' ? (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                oneClick.mutate({ params: { path: { post_id: postId } }, body: { mode: 'one_click' }, ifMatch: etag });
              }}
            >
              <Undo2 aria-hidden="true" />
              Back to one-click publishing
            </Button>
          ) : null}
        </div>
        {can('natively_scheduled') || state === 'natively_scheduled' ? (
          <ScheduleForm
            key={`schedule-${state}-${data.scheduledAt ?? ''}`}
            scheduledAt={data.scheduledAt ?? null}
            rescheduling={state === 'natively_scheduled'}
            busy={busy}
            error={move.error}
            onSubmit={(scheduledAt) => {
              send({ to: 'natively_scheduled', scheduledAt });
            }}
          />
        ) : null}
        {can('posted') ? (
          <PostedForm
            key={`posted-${state}`}
            displayText={data.displayText}
            busy={busy}
            error={move.error}
            onSubmit={(postUrl, finalText) => {
              send({ to: 'posted', postUrl, finalText });
            }}
          />
        ) : null}
      </section>

      <ActionBar
        label="Kit actions"
        primary={
          <CopyButton
            text={data.displayText}
            variant="primary"
            size="lg"
            className="w-full md:w-auto"
            copiedMessage="Post text copied."
          >
            Copy the text
          </CopyButton>
        }
        secondary={
          <a
            href={data.openLinkedInUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(buttonVariants({ variant: 'secondary' }), 'min-h-11')}
          >
            <ExternalLink aria-hidden="true" />
            Open LinkedIn
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        }
      />
    </div>
  );
}

/** The four steps, ticked off as you go (on this screen only; nothing is saved). */
function Checklist({ steps }: { steps: readonly string[] }) {
  const prefix = useId();
  const [done, setDone] = useState<readonly number[]>([]);
  return (
    <ol className="grid gap-2">
      {steps.map((step, index) => {
        const id = `${prefix}-${String(index)}`;
        const checked = done.includes(index);
        return (
          <li key={step} className="flex items-start gap-3 rounded-md border border-line px-3 py-2.5">
            <Checkbox
              id={id}
              checked={checked}
              className="mt-0.5"
              onCheckedChange={(value) => {
                setDone((current) =>
                  value === true ? [...current, index] : current.filter((item) => item !== index),
                );
              }}
            />
            <label htmlFor={id} className={cn('text-sm', checked ? 'text-ink-muted line-through' : 'text-ink')}>
              <span className="font-semibold">{`${String(index + 1)}. `}</span>
              {step}
            </label>
          </li>
        );
      })}
    </ol>
  );
}

function ScheduleForm({
  scheduledAt,
  rescheduling,
  busy,
  error,
  onSubmit,
}: {
  scheduledAt: string | null;
  rescheduling: boolean;
  busy: boolean;
  error: unknown;
  onSubmit: (scheduledAt: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(toLocalInput(scheduledAt));
  const [invalid, setInvalid] = useState<string | undefined>(undefined);
  const serverError = fieldErrors(error).scheduledAt;

  if (!open) {
    return (
      <div>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => {
            setOpen(true);
          }}
        >
          <CalendarClock aria-hidden="true" />
          {rescheduling ? 'Change the scheduled time' : 'I scheduled it in LinkedIn'}
        </Button>
      </div>
    );
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const iso = fromLocalInput(value);
    if (!iso) {
      setInvalid('Enter the date and time you picked in LinkedIn.');
      return;
    }
    setInvalid(undefined);
    onSubmit(iso);
  }

  return (
    <form onSubmit={submit} className="grid max-w-measure gap-3 rounded-lg border border-line p-4">
      <Field
        label="When LinkedIn will post it"
        description={`In this device’s time (${deviceTimeZone()}). Reminders and spacing follow this time.`}
        error={invalid ?? serverError}
        required
      >
        <Input
          type="datetime-local"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          {rescheduling ? 'Save the new time' : 'Mark as scheduled'}
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setOpen(false);
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}

function PostedForm({
  displayText,
  busy,
  error,
  onSubmit,
}: {
  displayText: string;
  busy: boolean;
  error: unknown;
  onSubmit: (postUrl: string, finalText: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [finalText, setFinalText] = useState(displayText);
  const [errors, setErrors] = useState<{ url?: string; text?: string }>({});
  const server = fieldErrors(error);
  const urlError =
    errors.url ??
    server.postUrl ??
    (isProblem(error, 'post_url_invalid') ? 'Paste the post’s linkedin.com link.' : undefined);

  if (!open) {
    return (
      <div>
        <Button
          disabled={busy}
          onClick={() => {
            setOpen(true);
          }}
        >
          <CircleCheck aria-hidden="true" />
          It’s posted
        </Button>
      </div>
    );
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const next: { url?: string; text?: string } = {};
    if (!isLinkedInPostUrl(url)) next.url = 'Paste the post’s link from LinkedIn: a linkedin.com address.';
    if (finalText.trim() === '') next.text = 'Paste the text as it went out.';
    setErrors(next);
    if (next.url !== undefined || next.text !== undefined) return;
    onSubmit(url.trim(), finalText);
  }

  return (
    <form onSubmit={submit} noValidate className="grid max-w-measure gap-3 rounded-lg border border-line p-4">
      <Field
        label="Post link"
        description="From the post’s menu in LinkedIn: Copy link to post."
        error={urlError}
        required
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
      <Field
        label="Final text"
        description="Prefilled with the kit text. Correct it if you changed anything in LinkedIn: insights use what actually went out."
        error={errors.text ?? server.finalText}
        required
      >
        <Textarea
          value={finalText}
          onChange={(event) => {
            setFinalText(event.target.value);
          }}
          className="min-h-40"
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>
          Mark as posted
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setOpen(false);
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
