import { useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  CircleCheck,
  CircleX,
  ClipboardCopy,
  ExternalLink,
  Hourglass,
  Info,
  RefreshCw,
  Send,
  TriangleAlert,
} from 'lucide-react';
import { useEffect, useEffectEvent, useId, useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';
import { Link } from 'react-router';
import { toast } from 'sonner';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import { Button } from '@/components/ui/button';
import { Field, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { keptWords } from '@/features/today/lib/time';
import { useNow } from '@/lib/use-now';
import { isEtagMismatch } from '@/lib/api/envelope';
import { fieldErrors, isProblem, problemCode, problemMessage } from '@/lib/api/problems';
import { toastSuccess } from '@/lib/api/toast';
import { cn } from '@/lib/cn';
import { isHttpsUrl } from '@/lib/urls';

import { StickyActions } from './components/sticky-actions';
import {
  forgetItem,
  reloadItems,
  useDismissItem,
  useFinalize,
  useLint,
  type Angle,
  type Finding,
  type ItemEntry,
  type WatchEntryVersioned,
} from './lib/queries';
import { DRAFT_MAX, groundingWords, lengthOf, REPLY_LIMIT, sliceCodePoints } from './lib/text';

/** The check runs this long after the owner stops typing. */
export const LINT_DELAY_MS = 500;

export interface AnglesState {
  readonly pending: boolean;
  /** The last request's error, when it was for this item. */
  readonly error: unknown;
  readonly run: () => void;
}

interface CheckResult {
  readonly text: string;
  readonly findings: readonly Finding[];
}

const count = new Intl.NumberFormat('en-US');

function things(n: number): string {
  return n === 1 ? '1 thing' : `${String(n)} things`;
}

/** What the angles step says when it cannot run, with the one way on. */
function AnglesProblem({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const code = problemCode(error);
  const setting = { label: 'Open Blocklist & policy', href: '/settings/policy' };
  const explained: Record<string, { title: string; body: string; link?: { label: string; href: string } }> = {
    ai_use_not_confirmed: {
      title: 'Angles need your AI-use confirmation',
      body: 'Their text and your stories go to a model. Confirm your employer’s AI-use policy first.',
      link: setting,
    },
    no_owner_material: {
      title: 'Angles need your own material',
      body: problemMessage(error),
      link: { label: 'Open Stories & POV', href: '/library/stories' },
    },
    blocklist_not_configured: {
      title: 'Save your blocklist first',
      body: 'Their text is checked against it before anything goes to a model. An empty list is fine.',
      link: setting,
    },
    confidential_text: {
      title: 'Their text matches your blocklist',
      body: 'It can’t go to a model. Write your reply yourself, or dismiss it.',
    },
  };
  const known = code === undefined ? undefined : explained[code];
  if (known) {
    return (
      <Alert variant="warn">
        <AlertTitle>{known.title}</AlertTitle>
        <AlertDescription>
          <p>{known.body}</p>
        </AlertDescription>
        {known.link ? (
          <AlertActions>
            <Button asChild size="sm" variant="secondary">
              <Link to={known.link.href}>{known.link.label}</Link>
            </Button>
          </AlertActions>
        ) : null}
      </Alert>
    );
  }
  return (
    <Alert variant="error">
      <AlertTitle>Couldn’t get angles</AlertTitle>
      <AlertDescription>
        <p>{problemMessage(error)}</p>
      </AlertDescription>
      <AlertActions>
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <RefreshCw aria-hidden="true" />
          Try again
        </Button>
      </AlertActions>
    </Alert>
  );
}

function AngleOption({ angle }: { angle: Angle }) {
  const textId = useId();
  const groundId = useId();
  const grounded = groundingWords(angle.groundedIn);
  return (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-3 rounded-lg border border-line bg-sheet p-3 transition-colors duration-150',
        'hover:bg-wash has-[[data-state=checked]]:border-ink has-[[data-state=checked]]:bg-sheet',
      )}
    >
      <RadioGroupItem value={angle.id} className="mt-0.5" aria-labelledby={textId} aria-describedby={grounded ? groundId : undefined} />
      <span className="grid min-w-0 gap-1">
        <span id={textId} className="whitespace-pre-line text-sm text-ink">
          {angle.text}
        </span>
        {grounded ? (
          <span id={groundId} className="text-xs text-ink-subtle">
            Draws on {grounded}
          </span>
        ) : null}
      </span>
    </label>
  );
}

function FindingRow({ finding, draft }: { finding: Finding; draft: string }) {
  const block = finding.severity === 'block';
  const Icon = block ? CircleX : finding.severity === 'warn' ? TriangleAlert : Info;
  const span =
    typeof finding.start === 'number' && typeof finding.end === 'number' && finding.end > finding.start
      ? sliceCodePoints(draft, finding.start, finding.end).trim()
      : '';
  return (
    <li className="flex items-start gap-2 text-sm">
      <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', block ? 'text-danger' : 'text-warn')} />
      <div className="min-w-0">
        <p className="text-ink">
          <span className="font-semibold">{block ? 'Fix: ' : 'Worth a look: '}</span>
          {finding.message}
        </p>
        {span !== '' ? <p className="truncate text-ink-subtle">“{span}”</p> : null}
      </div>
    </li>
  );
}

interface ReplyItemProps {
  entry: ItemEntry;
  /** The owner's draft (kept by the panel, so it survives going back to the list). */
  draft: string;
  onDraftChange: (text: string) => void;
  angles: AnglesState;
  /** Active watchlist voices, to link a comment to one when it is saved. */
  voices: readonly WatchEntryVersioned[];
  onBack: () => void;
  /** Saved or dismissed: the item is gone. */
  onFinished: () => void;
}

/**
 * One item: their text (kept 48 hours), angles grounded in the owner's stories, positions and
 * briefs, the owner's reply with a live check (under 1,000 characters, no links, no restating,
 * no AI tells, nothing on the blocklist), Copy, and "I posted it" to keep what went out.
 */
export function ReplyItem({ entry, draft, onDraftChange, angles, voices, onBack, onFinished }: ReplyItemProps) {
  const { item } = entry;
  const client = useQueryClient();
  const now = useNow();
  const theirsId = useId();
  const anglesId = useId();
  const draftRef = useRef<HTMLTextAreaElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [checkFailed, setCheckFailed] = useState<{ text: string; error: unknown } | null>(null);
  const [expiredOnCheck, setExpired] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [step, setStep] = useState<'write' | 'posted'>('write');
  const [finalText, setFinalText] = useState('');
  const [finalUrl, setFinalUrl] = useState('');
  const [voiceId, setVoiceId] = useState('none');
  const [finalErrors, setFinalErrors] = useState<{ text?: string; url?: string }>({});
  const [confirmDismiss, setConfirmDismiss] = useState(false);
  const lint = useLint();
  const finalize = useFinalize();
  const dismiss = useDismissItem();
  const conflict = useConflict();

  // Any request about the item can learn that its 48 hours are over (410 `expired`).
  const expired = expiredOnCheck || isProblem(angles.error, 'expired');
  const angleList = item.angles ?? [];
  const length = lengthOf(draft);
  const over = length > REPLY_LIMIT;
  const current = result !== null && result.text === draft ? result : null;
  const failed = checkFailed !== null && checkFailed.text === draft ? checkFailed : null;
  const blocks = current?.findings.filter((finding) => finding.severity === 'block') ?? [];
  const notes = current?.findings.filter((finding) => finding.severity !== 'block') ?? [];
  const ready = draft.trim() !== '' && !over && current !== null && blocks.length === 0;

  function startCheck(text: string) {
    lint.mutate(
      { params: { path: { item_id: item.id } }, body: { draftText: text } },
      {
        onSuccess: (out) => {
          setResult({ text, findings: out.findings });
        },
        onError: (error) => {
          if (isProblem(error, 'expired')) setExpired(true);
          else setCheckFailed({ text, error });
        },
      },
    );
  }
  const checkLater = useEffectEvent((text: string) => {
    startCheck(text);
  });

  // Check the draft a moment after the owner stops typing (no model call; nothing is stored).
  const needsCheck = draft.trim() !== '' && length <= DRAFT_MAX && !expired && result?.text !== draft;
  useEffect(() => {
    if (!needsCheck) return;
    const timer = window.setTimeout(() => {
      checkLater(draft);
    }, LINT_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [draft, needsCheck]);

  function choose(id: string) {
    const previous = angleList.find((angle) => angle.id === chosen);
    const next = angleList.find((angle) => angle.id === id);
    setChosen(id);
    // Start from the angle while the draft is empty or still the last angle as it was.
    if (next && (draft.trim() === '' || draft === previous?.text)) onDraftChange(next.text);
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      toastSuccess('Reply copied. Paste it on LinkedIn.');
    } catch {
      toast.error('Couldn’t copy the reply.', { description: 'Select the text and copy it yourself.' });
      draftRef.current?.select();
    }
  }

  function openPosted() {
    setFinalText(draft);
    setFinalErrors({});
    setStep('posted');
  }

  function send(etag: string | null) {
    finalize.mutate(
      {
        params: { path: { item_id: item.id } },
        body: {
          finalText: finalText.trim(),
          url: finalUrl.trim() === '' ? null : finalUrl.trim(),
          watchEntryId: voiceId === 'none' ? null : voiceId,
        },
        ifMatch: etag,
      },
      {
        onSuccess: () => {
          conflict.close();
          onFinished();
        },
        onError: (error) => {
          if (conflict.show(error)) return;
          if (isProblem(error, 'expired')) {
            setExpired(true);
            setStep('write');
            return;
          }
          const fields = fieldErrors(error);
          if (isProblem(error, 'confidential_text') || fields.finalText !== undefined || fields.url !== undefined) {
            setFinalErrors({
              text: fields.finalText ?? (isProblem(error, 'confidential_text') ? problemMessage(error) : undefined),
              url: fields.url,
            });
          }
        },
      },
    );
  }

  function submitPosted(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const errors: { text?: string; url?: string } = {};
    const posted = lengthOf(finalText.trim());
    if (posted === 0) errors.text = 'Paste what you posted.';
    else if (posted > DRAFT_MAX) errors.text = `LinkedIn comments stop at ${count.format(DRAFT_MAX)} characters.`;
    if (finalUrl.trim() !== '' && !isHttpsUrl(finalUrl.trim())) errors.url = 'Use the https link LinkedIn gives you, or leave it empty.';
    if (errors.text !== undefined || errors.url !== undefined) {
      flushSync(() => {
        setFinalErrors(errors);
      });
      focusFirstInvalid(formRef.current);
      return;
    }
    setFinalErrors({});
    send(entry.etag);
  }

  function runDismiss() {
    setConfirmDismiss(false);
    dismiss.mutate(
      { params: { path: { item_id: item.id } }, ifMatch: entry.etag },
      {
        onSuccess: onFinished,
        onError: (error) => {
          if (isProblem(error, 'expired')) {
            forgetItem(client, item.id);
            onFinished();
          } else if (isEtagMismatch(error)) {
            void reloadItems(client);
          }
        },
      },
    );
  }

  function back() {
    if (expired) forgetItem(client, item.id);
    onBack();
  }

  let status: string;
  if (expired) {
    status = ready
      ? 'Checked before it expired. Ready to copy.'
      : 'This item expired, so your draft can’t be checked or saved any more.';
  } else if (draft.trim() === '') status = 'Write your reply, or start from an angle.';
  else if (over) status = `Over ${count.format(REPLY_LIMIT)} characters. Shorten it before you copy it.`;
  else if (length > DRAFT_MAX) status = 'Too long to check.';
  else if (failed) status = 'Couldn’t check it.';
  else if (current === null) status = 'Checking…';
  else if (blocks.length > 0) status = `${things(blocks.length)} to fix before you copy it.`;
  else if (notes.length > 0) status = `Ready to copy. ${things(notes.length)} worth a look.`;
  else status = 'Ready to copy.';
  const checking = status === 'Checking…';

  const long = lengthOf(item.pastedText) > 360;

  return (
    <div className="grid gap-6">
      <div className="flex items-center justify-between gap-2">
        <Button variant="ghost" size="sm" onClick={back} className="-ml-3">
          <ArrowLeft aria-hidden="true" />
          All replies
        </Button>
        {step === 'write' && !expired ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={dismiss.isPending}
            onClick={() => {
              if (draft.trim() === '') runDismiss();
              else setConfirmDismiss(true);
            }}
          >
            Dismiss
          </Button>
        ) : null}
      </div>

      {expired ? (
        <Alert variant="warn">
          <AlertTitle>This item expired</AlertTitle>
          <AlertDescription>
            <p>
              Other members’ text is kept for 48 hours, so it has been deleted. Your draft stays below; read it over
              before you use it anywhere, because it can’t be checked any more.
            </p>
          </AlertDescription>
        </Alert>
      ) : null}

      <section aria-labelledby={theirsId} className="grid gap-2">
        <h3 id={theirsId} className="text-sm font-semibold text-ink">
          {item.itemKind === 'reply' ? 'The comment on your post' : 'The post you are commenting on'}
        </h3>
        <blockquote
          className={cn(
            'whitespace-pre-line rounded-lg border-l-2 border-line-strong bg-sunken px-4 py-3 text-ink',
            long && !expanded && 'line-clamp-6',
          )}
        >
          {item.pastedText}
        </blockquote>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
          <span className="inline-flex items-center gap-1.5">
            <Hourglass aria-hidden="true" className="size-4 text-ink-subtle" />
            {keptWords(item.expiresAt, now)}
          </span>
          {long ? (
            <Button
              variant="link"
              size="sm"
              aria-expanded={expanded}
              onClick={() => {
                setExpanded((value) => !value);
              }}
            >
              {expanded ? 'Show less' : 'Show all of it'}
            </Button>
          ) : null}
          {item.targetUrl ? (
            <a
              href={item.targetUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-1.5 text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink md:min-h-0"
            >
              Open on LinkedIn{' '}
              <ExternalLink aria-hidden="true" className="size-4" />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          ) : null}
        </div>
      </section>

      {step === 'write' ? (
        <>
          <section aria-labelledby={anglesId} aria-busy={angles.pending} className="grid gap-3">
            <div className="flex items-center justify-between gap-2">
              <h3 id={anglesId} className="text-sm font-semibold text-ink">
                Angles
              </h3>
              {angleList.length > 0 && !expired ? (
                <Button variant="ghost" size="sm" disabled={angles.pending} onClick={angles.run} className="-mr-3">
                  <RefreshCw aria-hidden="true" />
                  Get new angles
                </Button>
              ) : null}
            </div>
            {angles.pending ? (
              <div className="grid gap-2">
                <p role="status" className="text-sm text-ink-muted">
                  Finding angles in your stories, positions and briefs…
                </p>
                <Skeleton className="h-16" />
                <Skeleton className="h-16" />
              </div>
            ) : angles.error !== null && angles.error !== undefined && !isProblem(angles.error, 'expired') ? (
              <AnglesProblem error={angles.error} onRetry={angles.run} />
            ) : angleList.length > 0 ? (
              <RadioGroup aria-labelledby={anglesId} value={chosen ?? ''} onValueChange={choose} className="gap-2">
                {angleList.map((angle) => (
                  <AngleOption key={angle.id} angle={angle} />
                ))}
              </RadioGroup>
            ) : (
              <div className="grid gap-2 text-sm text-ink-muted">
                <p>
                  {item.servedBy && item.servedBy.length > 0
                    ? 'No angle fit your material this time. Write it yourself, or ask again.'
                    : 'Angles draw on your approved stories, positions and research briefs.'}
                </p>
                {!expired ? (
                  <Button variant="secondary" className="w-fit" onClick={angles.run}>
                    Get angles
                  </Button>
                ) : null}
              </div>
            )}
            {chosen !== null && draft !== '' && draft !== angleList.find((angle) => angle.id === chosen)?.text ? (
              <Button
                variant="link"
                size="sm"
                className="w-fit"
                onClick={() => {
                  const angle = angleList.find((option) => option.id === chosen);
                  if (angle) onDraftChange(angle.text);
                }}
              >
                Replace my draft with this angle
              </Button>
            ) : null}
          </section>

          <section className="grid gap-2">
            <Field
              label="Your reply"
              description="Add something only you could say: a detail from your work, a number, a decision."
            >
              <Textarea
                ref={draftRef}
                rows={6}
                value={draft}
                onChange={(event) => {
                  onDraftChange(event.target.value);
                }}
              />
            </Field>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <p className={cn('text-sm', ready ? 'text-success' : 'text-ink-muted')}>
                {ready ? <CircleCheck aria-hidden="true" className="mr-1.5 inline size-4 align-[-3px]" /> : null}
                {/* Only settled results are read out, once typing pauses; "Checking…" is for the eye. */}
                <span role="status">{checking ? '' : status}</span>
                {checking ? <span>{status}</span> : null}
              </p>
              <p className={cn('text-xs tabular-nums', over ? 'font-semibold text-danger' : 'text-ink-subtle')}>
                {count.format(length)} / {count.format(REPLY_LIMIT)}
              </p>
            </div>
            {failed ? (
              <Alert variant="error">
                <AlertDescription>
                  <p>{problemMessage(failed.error)}</p>
                </AlertDescription>
                <AlertActions>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      startCheck(draft);
                    }}
                  >
                    Check again
                  </Button>
                </AlertActions>
              </Alert>
            ) : null}
            {current !== null && current.findings.length > 0 ? (
              <ul aria-label="What the check found" className="grid gap-2">
                {current.findings.map((finding, index) => (
                  <FindingRow key={`${finding.code}-${String(index)}`} finding={finding} draft={draft} />
                ))}
              </ul>
            ) : null}
          </section>

          <StickyActions>
            {copied ? (
              <>
                <Button variant="secondary" onClick={() => void copy()} disabled={!ready}>
                  <ClipboardCopy aria-hidden="true" />
                  Copy again
                </Button>
                <Button onClick={openPosted} disabled={expired}>
                  <Send aria-hidden="true" />I posted it
                </Button>
              </>
            ) : (
              <>
                <Button variant="secondary" onClick={openPosted} disabled={expired || draft.trim() === ''}>
                  I posted it
                </Button>
                <Button onClick={() => void copy()} disabled={!ready}>
                  <ClipboardCopy aria-hidden="true" />
                  Copy reply
                </Button>
              </>
            )}
          </StickyActions>
        </>
      ) : (
        <form ref={formRef} noValidate onSubmit={submitPosted} className="grid gap-5">
          <div className="grid gap-1">
            <h3 className="text-sm font-semibold text-ink">Save what you posted</h3>
            <p className="text-sm text-ink-muted">
              Only your words and the link are kept. Their text is deleted as soon as you save.
            </p>
          </div>
          <Field
            label="What you posted"
            description="Edit it if you changed it on LinkedIn."
            error={finalErrors.text}
            required
          >
            <Textarea
              rows={6}
              value={finalText}
              onChange={(event) => {
                setFinalText(event.target.value);
              }}
            />
          </Field>
          <Field
            label="Link to your reply"
            description="Optional. Copy it from your reply’s menu on LinkedIn."
            error={finalErrors.url}
          >
            <Input
              type="url"
              inputMode="url"
              autoComplete="off"
              value={finalUrl}
              onChange={(event) => {
                setFinalUrl(event.target.value);
              }}
            />
          </Field>
          {item.itemKind === 'comment' && voices.length > 0 ? (
            <Field label="A voice from your watchlist" description="Optional. Counts it in today’s session.">
              {(control) => (
                <Select value={voiceId} onValueChange={setVoiceId}>
                  <SelectTrigger {...control}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Not from the watchlist</SelectItem>
                    {voices.map((voice) => (
                      <SelectItem key={voice.item.id} value={voice.item.id}>
                        {voice.item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
          ) : null}
          <StickyActions>
            <Button
              variant="secondary"
              onClick={() => {
                setStep('write');
              }}
            >
              Back
            </Button>
            <Button type="submit" disabled={finalize.isPending}>
              {finalize.isPending ? 'Saving…' : 'Save what I posted'}
            </Button>
          </StickyActions>
        </form>
      )}

      <AlertDialog open={confirmDismiss} onOpenChange={setConfirmDismiss}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Dismiss this one?</AlertDialogTitle>
            <AlertDialogDescription>Their text and your draft are deleted. You can’t undo this.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={runDismiss}>
              Dismiss
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ConflictDialog
        {...conflict}
        subject="This reply"
        saving={finalize.isPending}
        onKeepMine={() => {
          const etag = conflict.conflict?.etag;
          if (etag) {
            send(etag);
          } else {
            conflict.close();
            void reloadItems(client);
          }
        }}
        onTakeTheirs={() => {
          conflict.close();
          setStep('write');
          void reloadItems(client);
        }}
      />
    </div>
  );
}
