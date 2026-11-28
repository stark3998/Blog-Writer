import { LoaderCircle, ScanText, X } from 'lucide-react';
import { useId, useRef, useState, type ChangeEvent, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button, buttonVariants } from '@/components/ui/button';
import { Field, Fieldset, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Textarea } from '@/components/ui/textarea';
import { fieldErrors, problemMessage } from '@/lib/api/problems';
import { cn } from '@/lib/cn';
import { useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { isHttpsUrl } from '@/lib/urls';

import { StickyActions } from './components/sticky-actions';
import {
  SCREENSHOT_MAX_BYTES,
  SCREENSHOT_TYPES,
  useCreateItem,
  useExtractScreenshot,
  type Finding,
  type ItemEntry,
  type ItemKind,
} from './lib/queries';
import { MAX_PASTED_CHARS } from './lib/share';
import { firstLine, lengthOf } from './lib/text';

/** How the composer starts: empty, from a reply reminder (a post) or from the share target. */
export interface ComposePrefill {
  readonly itemKind: ItemKind;
  readonly text: string;
  readonly url: string | null;
  /** The owner's post a reply belongs to (reply reminders). */
  readonly postId: string | null;
  /** Arrived through the Android share target: say that nothing is saved until Save. */
  readonly shared: boolean;
}

export const EMPTY_PREFILL: ComposePrefill = { itemKind: 'comment', text: '', url: null, postId: null, shared: false };

interface Errors {
  text?: string;
  url?: string;
  postId?: string;
  shot?: string;
}

/** "On your post: “The hook line…”" for a reply that belongs to one of the owner's posts. */
function PostContext({ postId, onClear }: { postId: string; onClear: () => void }) {
  const post = useApiQuery({
    queryKey: queryKeys.posts.detail(postId),
    path: '/api/posts/{post_id}',
    init: { params: { path: { post_id: postId } } },
  });
  const item = post.data?.item;
  const line = item ? firstLine(item.finalText ?? item.displayText) : '';
  return (
    <div className="flex items-start justify-between gap-3 rounded-md bg-sunken px-3 py-2 text-sm">
      <p className="min-w-0 text-ink-muted">
        On your post{line ? ': ' : ''}
        {line ? <span className="text-ink">“{line}”</span> : null}
      </p>
      <Button variant="ghost" size="sm" onClick={onClear} className="-my-1 shrink-0">
        <X aria-hidden="true" />
        Not this post
      </Button>
    </div>
  );
}

interface ReplyComposerProps {
  prefill: ComposePrefill;
  /** The item was saved: open it (the panel asks for angles at once). */
  onCreated: (entry: ItemEntry) => void;
  onCancel: () => void;
}

/**
 * A new item: what the owner is answering (pasted, read from their own screenshot, or shared from
 * another app), whether it is a reply on their post or a comment elsewhere, and its link. Other
 * members' text stays in this component's state until the owner taps Save; then the server keeps
 * it for 48 hours.
 */
export function ReplyComposer({ prefill, onCreated, onCancel }: ReplyComposerProps) {
  const formRef = useRef<HTMLFormElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const statusId = useId();
  const [kind, setKind] = useState<ItemKind>(prefill.itemKind);
  const [text, setText] = useState(prefill.text);
  const [url, setUrl] = useState(prefill.url ?? '');
  const [postId, setPostId] = useState(prefill.postId);
  const [errors, setErrors] = useState<Errors>({});
  const [read, setRead] = useState<readonly Finding[] | null>(null);
  const create = useCreateItem();
  const extract = useExtractScreenshot();

  const length = lengthOf(text);

  function validate(): Errors {
    const next: Errors = {};
    if (text.trim() === '') next.text = 'Paste their text, or read it from a screenshot.';
    else if (length > MAX_PASTED_CHARS) next.text = `Keep it to ${MAX_PASTED_CHARS.toLocaleString('en-US')} characters.`;
    if (url.trim() !== '' && !isHttpsUrl(url.trim())) next.url = 'Use the https link LinkedIn gives you, or leave it empty.';
    return next;
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = validate();
    if (Object.keys(next).length > 0) {
      flushSync(() => {
        setErrors(next);
      });
      focusFirstInvalid(formRef.current);
      return;
    }
    setErrors({});
    create.mutate(
      {
        body: {
          itemKind: kind,
          pastedText: text.trim(),
          postId: kind === 'reply' ? postId : null,
          targetUrl: url.trim() === '' ? null : url.trim(),
        },
      },
      {
        onSuccess: (entry) => {
          onCreated(entry);
        },
        onError: (error) => {
          const fields = fieldErrors(error);
          if (Object.keys(fields).length === 0) return;
          flushSync(() => {
            setErrors({ text: fields.pastedText, url: fields.targetUrl, postId: fields.postId });
          });
          focusFirstInvalid(formRef.current);
        },
      },
    );
  }

  function readScreenshot(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Let the same file be chosen again after a fix.
    event.target.value = '';
    if (!file) return;
    if (!SCREENSHOT_TYPES.includes(file.type)) {
      setErrors((current) => ({ ...current, shot: 'Use a PNG, JPEG or WebP screenshot.' }));
      return;
    }
    if (file.size > SCREENSHOT_MAX_BYTES) {
      setErrors((current) => ({ ...current, shot: 'Screenshots can be up to 5 MB. Crop it and try again.' }));
      return;
    }
    setErrors((current) => ({ ...current, shot: undefined }));
    setRead(null);
    extract.mutate(file, {
      onSuccess: (out) => {
        setText(out.text);
        setRead(out.findings);
        textRef.current?.focus();
      },
      onError: (error) => {
        setErrors((current) => ({ ...current, shot: problemMessage(error) }));
      },
    });
  }

  const blocked = read?.filter((finding) => finding.severity === 'block') ?? [];

  return (
    <form ref={formRef} noValidate onSubmit={submit} className="grid gap-5" aria-describedby={statusId}>
      {prefill.shared ? (
        <Alert variant="info" role={undefined}>
          <AlertTitle>Shared from another app</AlertTitle>
          <AlertDescription>
            <p>Check their text and the link. Nothing is saved until you tap Save.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <Fieldset legend="What are you answering?">
        {(group) => (
          <SegmentedControl
            {...group}
            fullWidth
            value={kind}
            onValueChange={(value) => {
              setKind(value === 'reply' ? 'reply' : 'comment');
            }}
          >
            <SegmentedControlItem value="reply">Reply to a comment</SegmentedControlItem>
            <SegmentedControlItem value="comment">Comment on a post</SegmentedControlItem>
          </SegmentedControl>
        )}
      </Fieldset>

      {kind === 'reply' && postId !== null ? (
        <div className="grid gap-1.5">
          <PostContext
            postId={postId}
            onClear={() => {
              setPostId(null);
            }}
          />
          {errors.postId ? <p className="text-sm font-medium text-danger">{errors.postId}</p> : null}
        </div>
      ) : null}

      <Field
        label="Their text"
        description="Paste the comment or post you are answering. It is kept for 48 hours to help you reply, then deleted."
        error={errors.text}
        required
      >
        <Textarea
          ref={textRef}
          value={text}
          rows={5}
          onChange={(event) => {
            setText(event.target.value);
            setRead(null);
          }}
        />
      </Field>
      <p className={cn('-mt-3 text-xs tabular-nums', length > MAX_PASTED_CHARS ? 'text-danger' : 'text-ink-subtle')}>
        {length.toLocaleString('en-US')} of {MAX_PASTED_CHARS.toLocaleString('en-US')} characters
      </p>

      <div className="grid gap-2">
        <label
          className={cn(
            buttonVariants({ variant: 'secondary' }),
            'w-fit cursor-pointer has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus',
            extract.isPending && 'pointer-events-none opacity-60',
          )}
        >
          {extract.isPending ? (
            <LoaderCircle aria-hidden="true" className="animate-spin" />
          ) : (
            <ScanText aria-hidden="true" />
          )}
          Read a screenshot
          <input
            type="file"
            accept={SCREENSHOT_TYPES.join(',')}
            className="sr-only"
            disabled={extract.isPending}
            onChange={readScreenshot}
          />
        </label>
        <p className="text-sm text-ink-muted">Your own screenshot of the post or comment. It is read once and never kept.</p>
        {errors.shot ? (
          <Alert variant="error">
            <AlertDescription>
              <p>{errors.shot}</p>
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      <div id={statusId} role="status" className="empty:hidden">
        {extract.isPending ? <p className="text-sm text-ink-muted">Reading the screenshot…</p> : null}
        {read !== null ? (
          <Alert variant={blocked.length > 0 ? 'warn' : 'info'}>
            <AlertTitle>{blocked.length > 0 ? 'Some of it matches your blocklist' : 'Text read from your screenshot'}</AlertTitle>
            <AlertDescription>
              <p>
                {blocked.length > 0
                  ? 'Remove those parts before you save. Angles can’t use text that matches the blocklist.'
                  : 'Check it against the screenshot before you save.'}
              </p>
              {blocked.length > 0 ? (
                <ul className="list-disc pl-5">
                  {blocked.map((finding, index) => (
                    <li key={`${finding.code}-${String(index)}`}>{finding.message}</li>
                  ))}
                </ul>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      <Field
        label="Link to it"
        description="Optional. The post or comment link from LinkedIn, so you can open it from here."
        error={errors.url}
      >
        <Input
          type="url"
          inputMode="url"
          autoComplete="off"
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
          }}
        />
      </Field>

      <StickyActions>
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={create.isPending || extract.isPending}>
          {create.isPending ? 'Saving…' : 'Save and get angles'}
        </Button>
      </StickyActions>
    </form>
  );
}
