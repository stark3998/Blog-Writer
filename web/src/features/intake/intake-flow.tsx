import { ArrowLeft, CircleCheck, LoaderCircle, LockKeyhole, PencilLine, RotateCw, Settings2, WifiOff } from 'lucide-react';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ComponentProps,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { DiffView } from '@/components/ui/diff-view';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isAbortError } from '@/lib/api/errors';
import { fieldErrors, isProblem } from '@/lib/api/problems';
import { useAppData } from '@/lib/app-data';
import { cn } from '@/lib/cn';

import { checkIntake, redactIntake } from './api';
import { ActionBar } from './components/action-bar';
import { DictationButton } from './components/dictation-button';
import { FindingList } from './components/finding-list';
import { MarkedText } from './components/marked-text';
import { countFindings, describeFindings, isBlocking, markFindings, redactionCount } from './findings';
import {
  backToWriting,
  EMPTY_INTAKE,
  hasCurrentReview,
  reopenReview,
  withConfirmed,
  withRaw,
  withRedaction,
  withWording,
  type ConfirmedWording,
  type IntakeReview,
  type IntakeState,
} from './intake-state';
import { INTAKE_MAX_CHARS, type CheckResult, type IntakePurpose, type ProposalReason } from './types';
import { useOnline } from '@/lib/use-online';

type HeadingLevel = 2 | 3 | 4;

/** A heading at a level the caller picks (the flow sits under an `h2` in Library, deeper in Studio). */
function Heading({ level, ...props }: { level: number } & ComponentProps<'h2'>) {
  switch (Math.min(5, Math.max(2, level))) {
    case 2:
      return <h2 {...props} />;
    case 3:
      return <h3 {...props} />;
    case 4:
      return <h4 {...props} />;
    default:
      return <h5 {...props} />;
  }
}

const RAW_HINT =
  'Type, or dictate with your keyboard’s microphone. Rough is fine: you check the wording before anything is saved.';

/** Where the blocklist and the AI-use confirmation are set. */
export const POLICY_SETTINGS_PATH = '/settings/policy';

/** Edits pause this long before the wording is checked (no model call: local findings only). */
export const CHECK_DELAY_MS = 450;

export interface IntakeFlowProps {
  /** What the text is for; the anonymizer adapts to it. */
  purpose: IntakePurpose;
  /** The pillar the text is about, so its public vocabulary is not flagged as a name. */
  pillar?: string | null;
  /**
   * The flow's state. It holds private raw text: keep it in component state (`useIntakeState`)
   * or another in-memory store, never in web storage, the query cache or a URL.
   */
  state: IntakeState;
  /** A `setState`: the flow passes updaters, so async answers apply to the latest state. */
  onStateChange: Dispatch<SetStateAction<IntakeState>>;
  /** Names the raw-text field: the question, "Your story", "Your take". */
  label: string;
  /** Keeps the label for screen readers only, when a heading above already shows it. */
  hideLabel?: boolean;
  /** The hint under the label; defaults to the dictation hint. */
  description?: ReactNode;
  placeholder?: string;
  /** The longest wording the save takes (interview answers: 4000). */
  maxLength?: number;
  /** The level of the step headings ("Check the wording"); their parts use the next level. */
  headingLevel?: HeadingLevel;
  /** The confirm button's words. */
  confirmLabel?: string;
  /** The wording passed the check and is confirmed: save it, or move on. */
  onConfirm?: (confirmed: ConfirmedWording) => void;
  /** Controls at the start of the action bar, such as the interview's question picker. */
  actionsStart?: ReactNode;
  /** The main action once the wording is confirmed ("Next question", "Save story"). */
  confirmedAction?: ReactNode;
  /** Keep the action bar within thumb reach on phones (see `ActionBar`). */
  stickyActions?: boolean;
  /** Holds every action, for example while the page sends what was confirmed. */
  disabled?: boolean;
  /** How long edits pause before the wording is checked; tests use 0. */
  checkDelayMs?: number;
}

interface WordingCheck {
  /** The trimmed wording that was checked; the result applies only while the wording is still this. */
  readonly text: string;
  readonly result?: CheckResult;
  readonly error?: unknown;
}

type FocusTarget = 'raw' | 'review' | 'wording' | 'confirmed';

/**
 * The shared intake flow: the owner writes or dictates (raw text, private), the server finds
 * confidential details and the anonymizer proposes public-safe wording, the owner edits and
 * confirms it, and only that wording comes out of `onConfirm`.
 *
 * The raw text goes to `POST /api/stories/intake/redact` and nowhere else; the wording goes to
 * `POST /api/stories/intake/check` (local findings, no model) as the owner edits it and again
 * before it is confirmed. Neither call goes through the query cache.
 *
 * ```tsx
 * const [intake, setIntake] = useIntakeState();
 * <IntakeFlow purpose="story" label="Your story" state={intake} onStateChange={setIntake}
 *   onConfirm={(confirmed) => save(confirmed.text)} />
 * ```
 */
export function IntakeFlow({
  purpose,
  pillar,
  state,
  onStateChange,
  label,
  hideLabel = false,
  description,
  placeholder,
  maxLength = INTAKE_MAX_CHARS,
  headingLevel = 3,
  confirmLabel = 'Use this wording',
  onConfirm,
  actionsStart,
  confirmedAction,
  stickyActions = false,
  disabled = false,
  checkDelayMs = CHECK_DELAY_MS,
}: IntakeFlowProps) {
  const { api } = useAppData();
  const online = useOnline();
  const [redacting, setRedacting] = useState(false);
  const [redactError, setRedactError] = useState<unknown>();
  const [rawError, setRawError] = useState<string | undefined>();
  const [wordingCheck, setWordingCheck] = useState<WordingCheck | undefined>();
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<unknown>();
  const [wordingError, setWordingError] = useState<string | undefined>();
  /** The wording whose new details to check appeared on the last tap: look first, then tap again. */
  const [lookFirst, setLookFirst] = useState<string | undefined>();
  const [showChanges, setShowChanges] = useState(false);

  const rawField = useRef<HTMLTextAreaElement>(null);
  const wordingField = useRef<HTMLTextAreaElement>(null);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  const confirmedHeading = useRef<HTMLHeadingElement>(null);
  /** Where focus goes once the next render is on screen (a new object for every request). */
  const [focusRequest, setFocusRequest] = useState<{ readonly target: FocusTarget } | null>(null);
  const pending = useRef<AbortController | null>(null);

  // Leaving the flow cancels what it asked for: nothing comes back to a screen that is gone.
  useEffect(
    () => () => {
      pending.current?.abort();
    },
    [],
  );

  // Moves focus after a step changes, so the new step is announced and the keyboard follows.
  useEffect(() => {
    if (focusRequest === null) return;
    const element = {
      raw: rawField.current,
      review: reviewHeading.current,
      wording: wordingField.current,
      confirmed: confirmedHeading.current,
    }[focusRequest.target];
    element?.focus();
  }, [focusRequest]);

  const focusAfter = (target: FocusTarget) => {
    setFocusRequest({ target });
  };

  const review = state.stage === 'review' ? state.review : null;
  const wording = review ? review.wording.trim() : '';

  // Checks the wording as the owner edits it (debounced; the answer applies to that text only).
  useEffect(() => {
    if (wording === '' || wordingCheck?.text === wording) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      checkIntake(api, wording, controller.signal).then(
        (result) => {
          if (!controller.signal.aborted) setWordingCheck({ text: wording, result });
        },
        (error: unknown) => {
          if (!controller.signal.aborted && !isAbortError(error)) setWordingCheck({ text: wording, error });
        },
      );
    }, checkDelayMs);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [api, checkDelayMs, wording, wordingCheck?.text]);

  const ids = useId();
  const subLevel = headingLevel + 1;
  const busy = disabled || redacting || confirming;

  const start = () => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    return controller;
  };

  const redact = async ({ again = false }: { again?: boolean } = {}) => {
    const raw = state.raw;
    if (raw.trim() === '') {
      setRawError('Write or dictate something first.');
      rawField.current?.focus();
      return;
    }
    setRawError(undefined);
    setRedactError(undefined);
    if (!again && hasCurrentReview(state)) {
      focusAfter('review');
      onStateChange(reopenReview);
      return;
    }
    const controller = start();
    setRedacting(true);
    try {
      const proposal = await redactIntake(api, { text: raw, purpose, pillar }, controller.signal);
      if (controller.signal.aborted) return;
      setRedacting(false);
      setWordingCheck(undefined);
      setWordingError(undefined);
      setConfirmError(undefined);
      focusAfter('review');
      onStateChange((current) => withRedaction(current, raw, proposal));
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) return;
      setRedacting(false);
      const tooLong = fieldErrors(error).text;
      if (tooLong !== undefined) {
        setRawError('That’s more than one check takes. Shorten it, then try again.');
        rawField.current?.focus();
        return;
      }
      setRedactError(error);
    }
  };

  const confirm = async (current: IntakeReview) => {
    const text = current.wording.trim();
    if (text === '') {
      setWordingError('Write the wording to keep, or go back to your words.');
      wordingField.current?.focus();
      return;
    }
    if (text.length > maxLength) {
      setWordingError(
        `Keep it to ${maxLength.toLocaleString()} characters. It has ${text.length.toLocaleString()}.`,
      );
      wordingField.current?.focus();
      return;
    }
    setWordingError(undefined);
    setConfirmError(undefined);
    // The owner has seen the findings of this exact text when the live check finished for it.
    const seen = wordingCheck?.text === text ? wordingCheck.result : undefined;
    let result = seen;
    if (!result) {
      const controller = start();
      setConfirming(true);
      try {
        result = await checkIntake(api, text, controller.signal);
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        setConfirming(false);
        setConfirmError(error);
        return;
      }
      if (controller.signal.aborted) return;
      setConfirming(false);
      setWordingCheck({ text, result });
    }
    if (!result.ok) {
      setWordingError('Fix the details listed under the wording first.');
      wordingField.current?.focus();
      return;
    }
    if (!seen && result.findings.length > 0) {
      // Details to check appeared with this tap: show them before anything is confirmed.
      setLookFirst(text);
      return;
    }
    setLookFirst(undefined);
    const confirmed: ConfirmedWording = {
      text,
      redactionFindings: redactionCount(current.findings),
      warnings: result.findings.filter((finding) => !isBlocking(finding)),
    };
    focusAfter('confirmed');
    const checked = result.findings;
    onStateChange((latest) => (latest.review?.raw === current.raw ? withConfirmed(latest, text, checked) : latest));
    onConfirm?.(confirmed);
  };

  const appendTranscript = (text: string) => {
    onStateChange((current) => {
      const joined = current.raw.trim() === '' ? text : `${current.raw.replace(/\s+$/, '')} ${text}`;
      return withRaw(current, joined.slice(0, INTAKE_MAX_CHARS));
    });
  };

  const offlineNote = online ? null : (
    <Alert variant="warn" icon={<WifiOff aria-hidden="true" />}>
      <AlertTitle>You’re offline</AlertTitle>
      <AlertDescription>
        <p>Your words stay in this tab. Check them once you’re back online.</p>
      </AlertDescription>
    </Alert>
  );

  if (state.stage === 'confirmed' && state.confirmed) {
    const confirmed = state.confirmed;
    return (
      <section
        aria-labelledby={`${ids}confirmed`}
        data-slot="intake-flow"
        data-stage="confirmed"
        className="grid gap-5"
      >
        <div className="grid gap-2">
          <Heading
            level={headingLevel}
            id={`${ids}confirmed`}
            ref={confirmedHeading}
            tabIndex={-1}
            className="flex items-center gap-2 text-base font-semibold text-ink outline-none"
          >
            <CircleCheck aria-hidden="true" className="size-5 text-success" />
            Wording confirmed
          </Heading>
          <p className="text-sm text-ink-muted">Only this wording is kept. Your original words are never saved.</p>
        </div>
        <blockquote
          className={cn(
            'max-w-measure whitespace-pre-wrap break-words border-l-2 border-success pl-4',
            'font-serif text-lg leading-prose text-ink',
          )}
        >
          {confirmed.text}
        </blockquote>
        <Button
          variant="secondary"
          size="sm"
          className="justify-self-start pointer-coarse:min-h-11"
          disabled={disabled}
          onClick={() => {
            focusAfter('wording');
            onStateChange(reopenReview);
          }}
        >
          <PencilLine aria-hidden="true" />
          Change the wording
        </Button>
        {actionsStart !== undefined || confirmedAction !== undefined ? (
          <ActionBar sticky={stickyActions}>
            {actionsStart}
            {confirmedAction ? <div className="ml-auto">{confirmedAction}</div> : null}
          </ActionBar>
        ) : null}
      </section>
    );
  }

  if (review) {
    const rawMarks = markFindings(review.raw, review.findings);
    const current = wordingCheck?.text === wording ? wordingCheck : undefined;
    // While an edit is rechecked, the last result stays (marked as checking) so nothing jumps.
    const basis = wording === '' ? undefined : (current ?? wordingCheck);
    const wordingMarks = basis?.result ? markFindings(basis.text, basis.result.findings) : [];
    return (
      <section aria-labelledby={`${ids}review`} data-slot="intake-flow" data-stage="review" className="grid gap-8">
        <div className="grid gap-2">
          <Heading
            level={headingLevel}
            id={`${ids}review`}
            ref={reviewHeading}
            tabIndex={-1}
            className="font-serif text-2xl font-medium text-ink outline-none"
          >
            Check the wording
          </Heading>
          <p className="text-ink-muted">
            {review.findings.length === 0
              ? 'Nothing confidential was found in your words.'
              : `In your words: ${describeFindings(review.findings)}`}
          </p>
        </div>

        <section aria-labelledby={`${ids}raw-heading`} className="grid max-w-measure gap-3">
          <Heading level={subLevel} id={`${ids}raw-heading`} className="text-sm font-semibold text-ink">
            Your words
          </Heading>
          <MarkedText
            text={review.raw}
            marks={rawMarks}
            label="Your words, with the details found marked"
            className="rounded-md bg-sunken px-4 py-3"
          />
          <FindingList text={review.raw} marks={rawMarks} aria-label="Details found in your words" />
          <p className="flex items-start gap-2 text-sm text-ink-muted">
            <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            These stay in this tab. They are never saved.
          </p>
          <Button
            variant="secondary"
            size="sm"
            className="justify-self-start pointer-coarse:min-h-11"
            disabled={busy}
            onClick={() => {
              focusAfter('raw');
              onStateChange(backToWriting);
            }}
          >
            <ArrowLeft aria-hidden="true" />
            Edit my words
          </Button>
        </section>

        <section aria-labelledby={`${ids}wording-heading`} className="grid max-w-measure gap-3">
          <Heading level={subLevel} id={`${ids}wording-heading`} className="text-sm font-semibold text-ink">
            Public-safe wording
          </Heading>
          <ProposalNotice
            reason={review.reason}
            anonymizerUsed={review.anonymizerUsed}
            hasPlaceholders={/\[[a-z ]+\]/i.test(review.wording)}
            disabled={busy || !online}
            onRetry={() => void redact({ again: true })}
          />
          <Field
            label="Wording to keep"
            description="Edit it until it says what you mean. Only this wording is saved."
            error={wordingError}
          >
            <Textarea
              ref={wordingField}
              value={review.wording}
              readOnly={confirming}
              rows={6}
              className="min-h-40"
              autoCapitalize="sentences"
              spellCheck
              onChange={(event) => {
                setWordingError(undefined);
                onStateChange((latest) => withWording(latest, event.target.value));
              }}
            />
          </Field>
          <WordingStatus
            wording={wording}
            basis={basis}
            checking={wording !== '' && current === undefined}
            maxLength={maxLength}
            lookFirst={lookFirst === wording}
            confirmLabel={confirmLabel}
          />
          {wordingMarks.length > 0 && basis ? (
            <FindingList text={basis.text} marks={wordingMarks} aria-label="Details found in the wording" />
          ) : null}
          {confirmError ? (
            <ProblemAlert
              error={confirmError}
              title="Couldn’t check the wording."
              onRetry={() => void confirm(review)}
              retryLabel="Check the wording again"
            />
          ) : null}
          <Collapsible open={showChanges} onOpenChange={setShowChanges}>
            <CollapsibleTrigger asChild>
              <Button variant="link" size="sm" className="justify-self-start pointer-coarse:min-h-11">
                {showChanges ? 'Hide what changed' : 'Show what changed from your words'}
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <DiffView
                before={review.raw}
                after={review.wording}
                label="What changed from your words"
                className="mt-3 rounded-md border border-line bg-sheet p-3"
              />
            </CollapsibleContent>
          </Collapsible>
        </section>

        {offlineNote}
        <ActionBar sticky={stickyActions}>
          {actionsStart}
          <Button className="ml-auto" disabled={busy || !online} onClick={() => void confirm(review)}>
            {confirming ? <LoaderCircle aria-hidden="true" className="animate-pulse-soft" /> : null}
            {confirming ? 'Checking…' : confirmLabel}
          </Button>
        </ActionBar>
      </section>
    );
  }

  return (
    <div data-slot="intake-flow" data-stage="write" className="grid gap-4">
      <div className="grid max-w-measure gap-2">
        <Field
          label={label}
          hideLabel={hideLabel}
          description={description ?? RAW_HINT}
          error={rawError}
        >
          <Textarea
            ref={rawField}
            value={state.raw}
            placeholder={placeholder}
            readOnly={redacting}
            maxLength={INTAKE_MAX_CHARS}
            rows={7}
            className="min-h-44"
            autoCapitalize="sentences"
            autoComplete="off"
            spellCheck
            onChange={(event) => {
              setRawError(undefined);
              onStateChange((current) => withRaw(current, event.target.value));
            }}
          />
        </Field>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <DictationButton onTranscript={appendTranscript} disabled={redacting || disabled} />
          <p className="ml-auto text-sm tabular-nums text-ink-subtle">{characters(state.raw.length)}</p>
        </div>
        <p className="flex items-start gap-2 text-sm text-ink-muted">
          <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          Only the confidentiality check sees your words, and they are never saved. Reloading the page clears them.
        </p>
      </div>
      {redactError ? <RedactProblem error={redactError} onRetry={() => void redact({ again: true })} /> : null}
      {offlineNote}
      <ActionBar sticky={stickyActions}>
        {actionsStart}
        <Button className="ml-auto" disabled={busy || !online} onClick={() => void redact()}>
          {redacting ? <LoaderCircle aria-hidden="true" className="animate-pulse-soft" /> : null}
          {redacting ? 'Checking…' : 'Make it public-safe'}
        </Button>
      </ActionBar>
    </div>
  );
}

function characters(count: number): string {
  return count === 1 ? '1 character' : `${count.toLocaleString()} characters`;
}

/**
 * The line under the wording: ready, or what still blocks it. Only finished checks are
 * announced (a small polite region); while an edit is rechecked the last answer stays, with a
 * spinner, so a screen reader is not told "checking" at every pause in typing.
 */
function WordingStatus({
  wording,
  basis,
  checking,
  maxLength,
  lookFirst,
  confirmLabel,
}: {
  wording: string;
  /** The current wording's check, else the last one while the new one runs. */
  basis: WordingCheck | undefined;
  checking: boolean;
  maxLength: number;
  lookFirst: boolean;
  confirmLabel: string;
}) {
  const findings = basis?.result?.findings ?? [];
  const blocking = countFindings(findings).blocking;
  let message = '';
  if (wording === '') message = 'Write the wording to keep.';
  else if (wording.length > maxLength) {
    message = `Over the limit by ${(wording.length - maxLength).toLocaleString()} characters.`;
  }
  else if (lookFirst && basis?.result && !checking) {
    const them = findings.length === 1 ? 'it' : 'them';
    message = `${describeFindings(findings)} Look ${them} over, then tap ${confirmLabel} again.`;
  } else if (basis?.error) message = 'Couldn’t check the wording yet. It’s checked again when you use it.';
  else if (blocking > 0) {
    message = `${blocking === 1 ? '1 detail' : `${String(blocking)} details`} to fix before you can use it.`;
  }
  else if (basis?.result && findings.length > 0) message = `Ready to use. ${describeFindings(findings)}`;
  else if (basis?.result) message = 'Ready to use. Nothing confidential found.';
  const ready = !checking && basis?.result?.ok === true && wording.length <= maxLength;
  return (
    <div className="flex min-h-5.5 items-center gap-2 text-sm">
      {checking ? (
        <LoaderCircle aria-hidden="true" className="size-4 shrink-0 animate-pulse-soft text-ink-subtle" />
      ) : null}
      {ready ? <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-success" /> : null}
      <p role="status" className={cn(ready ? 'text-success' : 'text-ink-muted')}>
        {message}
      </p>
      {checking && message === '' ? <span className="text-ink-muted">Checking the wording…</span> : null}
    </div>
  );
}

const REASONS: Record<ProposalReason, { title: string; text: string; settings: boolean; retry: boolean }> = {
  ai_use_not_confirmed: {
    title: 'No suggestion: AI use isn’t confirmed',
    text:
      'No model sees your words until you confirm your employer’s AI-use policy. ' +
      'The details found are replaced below; edit the rest yourself.',
    settings: true,
    retry: false,
  },
  strict_mode: {
    title: 'No suggestion: strict mode is on',
    text: 'No model sees private text in strict mode. The details found are replaced below; edit the rest yourself.',
    settings: false,
    retry: false,
  },
  ai_unavailable: {
    title: 'No suggestion right now',
    text:
      'The anonymizer isn’t available. The details found are replaced below; ' +
      'edit the rest yourself, or try again.',
    settings: false,
    retry: true,
  },
  refused: {
    title: 'No suggestion for this text',
    text: 'The anonymizer declined to rewrite it. The details found are replaced below; edit the rest yourself.',
    settings: false,
    retry: false,
  },
};

function ProposalNotice({
  reason,
  anonymizerUsed,
  hasPlaceholders,
  disabled,
  onRetry,
}: {
  reason: ProposalReason | null;
  anonymizerUsed: boolean;
  hasPlaceholders: boolean;
  disabled: boolean;
  onRetry: () => void;
}) {
  const placeholders = hasPlaceholders ? (
    <p>Replace the bracketed placeholders so each sentence reads naturally.</p>
  ) : null;
  if (reason === null) {
    return (
      <div className="grid gap-1 text-sm text-ink-muted">
        <p>
          {anonymizerUsed
            ? 'Suggested from your words with names, numbers and other identifying details generalised.'
            : 'Your words, ready to edit.'}
        </p>
        {placeholders}
      </div>
    );
  }
  const notice = REASONS[reason];
  return (
    <Alert variant={reason === 'ai_unavailable' ? 'warn' : 'info'}>
      <AlertTitle>{notice.title}</AlertTitle>
      <AlertDescription>
        <p>{notice.text}</p>
        {placeholders}
      </AlertDescription>
      {notice.settings || notice.retry ? (
        <AlertActions>
          {notice.settings ? (
            <Button asChild size="sm" variant="secondary" className="pointer-coarse:min-h-11">
              <Link to={POLICY_SETTINGS_PATH}>
                <Settings2 aria-hidden="true" />
                Open Blocklist & policy
              </Link>
            </Button>
          ) : null}
          {notice.retry ? (
            <Button size="sm" variant="secondary" className="pointer-coarse:min-h-11" disabled={disabled} onClick={onRetry}>
              <RotateCw aria-hidden="true" />
              Ask for a suggestion again
            </Button>
          ) : null}
        </AlertActions>
      ) : null}
    </Alert>
  );
}

function RedactProblem({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (isProblem(error, 'blocklist_not_configured')) {
    return (
      <Alert variant="warn">
        <AlertTitle>Save your blocklist first</AlertTitle>
        <AlertDescription>
          <p>
            Nothing can be checked for confidential details until the blocklist is saved once. An empty list is fine.
          </p>
        </AlertDescription>
        <AlertActions>
          <Button asChild size="sm" variant="secondary" className="pointer-coarse:min-h-11">
            <Link to={POLICY_SETTINGS_PATH}>
              <Settings2 aria-hidden="true" />
              Open Blocklist & policy
            </Link>
          </Button>
        </AlertActions>
      </Alert>
    );
  }
  return (
    <ProblemAlert
      error={error}
      title="Couldn’t check your words."
      onRetry={onRetry}
      retryLabel="Check your words again"
    />
  );
}

/**
 * Local state for one flow (`IntakeFlow`'s `state` and `onStateChange`): component state, so the
 * raw text is gone when the screen is.
 */
export function useIntakeState(initial: IntakeState = EMPTY_INTAKE) {
  return useState<IntakeState>(initial);
}
