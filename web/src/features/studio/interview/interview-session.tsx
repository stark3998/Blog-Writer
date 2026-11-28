import { ArrowRight, CircleCheck, Hourglass, ListOrdered, LoaderCircle, Send, WifiOff } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import { Progress } from '@/components/ui/progress';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { ActionBar, EMPTY_INTAKE, IntakeFlow, intakeProgress, reopenReview } from '@/features/intake';
import { useOnline } from '@/lib/use-online';
import { isProblem } from '@/lib/api/problems';
import { apiRequest } from '@/lib/api/request';
import { useAppData } from '@/lib/app-data';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { useInterviewDrafts } from './draft-store';
import {
  ANSWER_MAX_CHARS,
  answersText,
  answersToSend,
  firstPosition,
  formatMoment,
  nextPosition,
  questionProgress,
  QUESTION_PROGRESS_LABELS,
  refusedQuestionIds,
  type AnswerIn,
  type InterviewEntry,
} from './interview-model';
import { QuestionList, QuestionSheet, type InterviewPosition } from './question-nav';
import { useInterviewTargets } from './use-machine';

export interface InterviewSessionProps {
  /** An open interview (pending or expired) and its etag. */
  entry: InterviewEntry;
  /** The piece's pillar, so its public vocabulary is never flagged as a name. */
  pillar: string | null;
  /** The detail queries the answers change (the article: its status moves on). */
  pieceKey: readonly unknown[];
  /** The answers or the skip went through: show the stored interview. */
  onSaved: (entry: InterviewEntry, outcome: 'answered' | 'skipped') => void;
  /** Load the interview again (it changed elsewhere). */
  onReload: () => void;
}

type SessionProblem =
  | { readonly kind: 'confidential'; readonly numbers: readonly number[] }
  | { readonly kind: 'moved' }
  | { readonly kind: 'failed'; readonly action: 'answers' | 'skip'; readonly error: unknown };

function count(n: number, one: string, many: string): string {
  return `${String(n)} ${n === 1 ? one : many}`;
}

/** "Question 2", "Questions 1 and 3", "Questions 1, 2 and 4". */
function questionNumbers(numbers: readonly number[]): string {
  if (numbers.length === 1) return `Question ${String(numbers[0])}`;
  const head = numbers.slice(0, -1).join(', ');
  return `Questions ${head} and ${String(numbers.at(-1))}`;
}

/** What sending now leaves out, for the question before a partial send. */
function partialSendWarning(unconfirmed: readonly number[]): string {
  const after = 'The interview takes no more answers once these are sent.';
  if (unconfirmed.length === 0) return after;
  const one = unconfirmed.length === 1;
  const stays = one ? 'isn’t confirmed, so it stays' : 'aren’t confirmed, so they stay';
  return `${questionNumbers(unconfirmed)} ${stays} unanswered. ${after}`;
}

const ANSWER_HINT =
  'Answer in your own words; rough is fine. Dictate with your keyboard’s microphone if that’s quicker. ' +
  'You check the wording before anything is sent.';

/**
 * One open interview, one question per screen: the question, the shared intake flow for the
 * answer (raw words -> redaction -> confirmed public-safe wording), and a final "Check and send"
 * step. Drafts autosave in memory only (`draft-store`); only confirmed wording is sent, with
 * If-Match, and a stale etag opens the conflict dialog. Skipping asks first.
 */
export function InterviewSession({ entry, pillar, pieceKey, onSaved, onReload }: InterviewSessionProps) {
  const interview = entry.item;
  const questions = interview.questions;
  const total = questions.length;
  const ids = useId();
  const { api } = useAppData();
  const online = useOnline();
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const { drafts, latest, update, clear } = useInterviewDrafts(interview.id);
  const [position, setPosition] = useState<InterviewPosition>(() => firstPosition(questions, latest()));
  const [fixIds, setFixIds] = useState<ReadonlySet<string>>(() => new Set());
  const [problem, setProblem] = useState<SessionProblem | null>(null);
  const [confirmedNote, setConfirmedNote] = useState<number | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [askPartial, setAskPartial] = useState(false);
  const [askSkip, setAskSkip] = useState(false);
  const [lastSent, setLastSent] = useState<readonly AnswerIn[]>([]);
  const [retry, setRetry] = useState<'answers' | 'skip'>('answers');
  const [theirs, setTheirs] = useState<{ text: string; etag: string | null } | undefined>();
  const conflict = useConflict();
  const stepHeading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  const done = useRef(false);

  const targets = useInterviewTargets(interview.status);
  const canAnswer = targets.includes('answered');
  const canSkip = targets.includes('skipped');

  const invalidates = [queryKeys.interviews.lists(), queryKeys.today, pieceKey];
  const send = useApiMutation('post', '/api/interviews/{interview_id}/answers', {
    invalidates,
    updates: (data) => queryKeys.interviews.detail(data.item.id),
    successToast: 'Answers sent.',
    errorToast: false,
    gcTime: 0,
    onSuccess: (data) => {
      done.current = true;
      onSaved(data, 'answered');
    },
  });
  const skip = useApiMutation('post', '/api/interviews/{interview_id}/skip', {
    invalidates,
    updates: (data) => queryKeys.interviews.detail(data.item.id),
    successToast: 'Interview skipped.',
    errorToast: false,
    onSuccess: (data) => {
      done.current = true;
      onSaved(data, 'skipped');
    },
  });
  const sending = send.isPending || skip.isPending;

  // Sent or skipped: the drafts have done their job. Cleared once this screen is gone, so the
  // last frame never shows emptied answers.
  useEffect(
    () => () => {
      if (done.current) clear();
    },
    [clear],
  );

  // A new step takes focus (its heading), so it is announced and the keyboard starts there.
  useEffect(() => {
    if (!moved.current) return;
    moved.current = false;
    stepHeading.current?.focus();
  }, [position]);

  const goTo = (next: InterviewPosition, note: number | null = null) => {
    moved.current = true;
    setConfirmedNote(note);
    setPosition(next);
  };

  const progressOf = (questionId: string) => questionProgress(drafts, questionId, fixIds);
  const ready = answersToSend(questions, drafts);
  const confirmedCount = ready.length;

  const fetchTheirs = async () => {
    setTheirs(undefined);
    try {
      const current = await apiRequest(api, 'get', '/api/interviews/{interview_id}', {
        params: { path: { interview_id: interview.id } },
      });
      setTheirs({ text: answersText(current.item.questions, current.item.answers ?? []), etag: current.etag });
    } catch {
      // The dialog still works without the comparison.
    }
  };

  const failed = (action: 'answers' | 'skip', error: unknown, answers: readonly AnswerIn[] = []) => {
    if (conflict.show(error)) {
      setRetry(action);
      if (action === 'answers') void fetchTheirs();
      return;
    }
    if (action === 'answers' && isProblem(error, 'confidential_text')) {
      const refused = refusedQuestionIds(error, answers);
      for (const id of refused) update(id, reopenReview);
      setFixIds(new Set(refused));
      const numbers = questions.flatMap((question, index) => (refused.includes(question.id) ? [index + 1] : []));
      setProblem({ kind: 'confidential', numbers });
      const first = numbers[0];
      if (first !== undefined) goTo(first - 1);
      return;
    }
    if (isProblem(error, 'invalid_transition')) {
      setProblem({ kind: 'moved' });
      onReload();
      return;
    }
    setProblem({ kind: 'failed', action, error });
  };

  const sendAnswers = (etag: string | null | undefined) => {
    const answers = answersToSend(questions, latest());
    if (answers.length === 0) return;
    setLastSent(answers);
    setProblem(null);
    send.mutate(
      { params: { path: { interview_id: interview.id } }, body: { answers }, ifMatch: etag ?? undefined },
      {
        onError: (error) => {
          failed('answers', error, answers);
        },
        onSuccess: () => {
          conflict.close();
        },
      },
    );
  };

  const skipInterview = (etag: string | null | undefined) => {
    setProblem(null);
    skip.mutate(
      { params: { path: { interview_id: interview.id } }, ifMatch: etag ?? undefined },
      {
        onError: (error) => {
          failed('skip', error);
        },
        onSuccess: () => {
          conflict.close();
        },
      },
    );
  };

  const where = position === 'send' ? 'check and send' : `${String(position + 1)} of ${String(total)}`;
  const navButton = desktop ? null : (
    <Button
      variant="secondary"
      aria-haspopup="dialog"
      aria-expanded={sheetOpen}
      aria-label={`All questions, now on ${where}`}
      onClick={() => {
        setSheetOpen(true);
      }}
    >
      <ListOrdered aria-hidden="true" />
      {position === 'send' ? 'Questions' : `${String(position + 1)} of ${String(total)}`}
    </Button>
  );

  const offline = online ? null : (
    <Alert variant="warn" icon={<WifiOff aria-hidden="true" />}>
      <AlertTitle>You’re offline</AlertTitle>
      <AlertDescription>
        <p>Your answers stay in this tab. Send them once you’re back online.</p>
      </AlertDescription>
    </Alert>
  );

  const problemAlert = problem ? (
    <SessionProblemAlert
      problem={problem}
      onRetry={() => {
        if (problem.kind !== 'failed') return;
        if (problem.action === 'skip') skipInterview(entry.etag);
        else sendAnswers(entry.etag);
      }}
    />
  ) : null;

  const question = typeof position === 'number' ? questions[position] : undefined;
  const step =
    question && typeof position === 'number' ? (
      <article aria-labelledby={`${ids}question`} className="grid gap-6">
        <div className="grid max-w-measure gap-2">
          {problemAlert}
          {confirmedNote !== null ? (
            <p role="status" className="flex items-center gap-2 text-sm font-medium text-success">
              <CircleCheck aria-hidden="true" className="size-4 shrink-0" />
              {`Answer ${String(confirmedNote)} confirmed.`}
            </p>
          ) : null}
          <p className="text-sm font-medium text-ink-muted">{`Question ${String(position + 1)} of ${String(total)}`}</p>
          <h3
            id={`${ids}question`}
            ref={stepHeading}
            tabIndex={-1}
            className="font-serif text-2xl font-medium text-ink outline-none md:text-3xl"
          >
            {question.text}
          </h3>
          {question.purpose ? <p className="text-sm text-ink-muted">{`Why it’s asked: ${question.purpose}`}</p> : null}
        </div>
        <IntakeFlow
          key={question.id}
          purpose="interview_answer"
          pillar={pillar}
          state={drafts.get(question.id) ?? EMPTY_INTAKE}
          onStateChange={(action) => {
            update(question.id, action);
          }}
          label={question.text}
          hideLabel
          description={ANSWER_HINT}
          maxLength={ANSWER_MAX_CHARS}
          headingLevel={4}
          stickyActions
          disabled={sending}
          actionsStart={navButton}
          onConfirm={() => {
            setProblem((current) => (current?.kind === 'confidential' ? null : current));
            setFixIds((current) => {
              if (!current.has(question.id)) return current;
              const next = new Set(current);
              next.delete(question.id);
              return next;
            });
            goTo(nextPosition(questions, latest(), position), position + 1);
          }}
          confirmedAction={
            <Button
              onClick={() => {
                goTo(position + 1 < total ? position + 1 : 'send');
              }}
            >
              {position + 1 < total ? 'Next question' : 'Check and send'}
              <ArrowRight aria-hidden="true" />
            </Button>
          }
        />
      </article>
    ) : (
      <section aria-labelledby={`${ids}send`} className="grid gap-6">
        <div className="grid max-w-measure gap-2">
          {problemAlert}
          {confirmedNote !== null ? (
            <p role="status" className="flex items-center gap-2 text-sm font-medium text-success">
              <CircleCheck aria-hidden="true" className="size-4 shrink-0" />
              {`Answer ${String(confirmedNote)} confirmed.`}
            </p>
          ) : null}
          <h3
            id={`${ids}send`}
            ref={stepHeading}
            tabIndex={-1}
            className="font-serif text-2xl font-medium text-ink outline-none"
          >
            Check and send
          </h3>
          <p className="text-ink-muted">
            {confirmedCount === 0
              ? 'No answer is confirmed yet. Confirm at least one, or skip the interview.'
              : `${String(confirmedCount)} of ${String(total)} answers confirmed. Only the confirmed wording is sent, ` +
                'and your original words are dropped once it is.'}
          </p>
        </div>
        <ol className="grid max-w-measure border-t border-line">
          {questions.map((item, index) => {
            const confirmed = drafts.get(item.id)?.confirmed;
            const state = progressOf(item.id);
            return (
              <li key={item.id} className="grid gap-2 border-b border-line py-4">
                <p className="text-xs font-medium text-ink-subtle">{`Question ${String(index + 1)}`}</p>
                <h4 className="font-serif text-lg font-medium text-ink">{item.text}</h4>
                {confirmed && state === 'confirmed' ? (
                  <p className="whitespace-pre-wrap break-words font-serif leading-prose text-ink">{confirmed.text}</p>
                ) : (
                  <p className="text-sm text-ink-muted">
                    {state === 'empty'
                      ? 'Not answered: it stays empty if you send now.'
                      : `${QUESTION_PROGRESS_LABELS[state]}: not sent until you confirm the wording.`}
                  </p>
                )}
                <Button
                  variant="link"
                  size="sm"
                  className="justify-self-start pointer-coarse:min-h-11"
                  disabled={sending}
                  onClick={() => {
                    goTo(index);
                  }}
                >
                  {state === 'confirmed' ? 'Change the answer' : state === 'empty' ? 'Answer it' : 'Finish it'}
                  <span className="sr-only">{` (question ${String(index + 1)})`}</span>
                </Button>
              </li>
            );
          })}
        </ol>
        {offline}
        <ActionBar sticky>
          {navButton}
          <Button
            className="ml-auto"
            disabled={!canAnswer || sending || confirmedCount === 0 || !online}
            onClick={() => {
              if (confirmedCount < total) setAskPartial(true);
              else sendAnswers(entry.etag);
            }}
          >
            {send.isPending ? (
              <LoaderCircle aria-hidden="true" className="animate-pulse-soft" />
            ) : (
              <Send aria-hidden="true" />
            )}
            {send.isPending ? 'Sending…' : `Send ${count(confirmedCount, 'answer', 'answers')}`}
          </Button>
        </ActionBar>
      </section>
    );

  const unconfirmed = questions.flatMap((item, index) => (progressOf(item.id) === 'confirmed' ? [] : [index + 1]));
  const hasDrafts = questions.some((item) => intakeProgress(drafts.get(item.id)) !== 'empty');
  const due = formatMoment(interview.dueAt);
  const list = {
    questions,
    progress: progressOf,
    position,
    confirmedCount,
    onSelect: (next: InterviewPosition) => {
      goTo(next);
    },
  };

  return (
    <section aria-labelledby={`${ids}title`} className="grid gap-8">
      <header className="grid max-w-measure gap-4">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
          <h2 id={`${ids}title`} className="font-serif text-2xl font-medium text-ink">
            {interview.mode === 'micro'
              ? total === 2
                ? 'Two quick questions'
                : count(total, 'quick question', 'quick questions')
              : count(total, 'interview question', 'interview questions')}
          </h2>
          {canSkip ? (
            <Button
              variant="ghost"
              size="sm"
              className="pointer-coarse:min-h-11"
              disabled={sending}
              onClick={() => {
                setAskSkip(true);
              }}
            >
              Skip the interview
            </Button>
          ) : null}
        </div>
        <p className="text-ink-muted">
          {interview.mode === 'micro'
            ? 'Your take and one practical step, in your own words. About three minutes.'
            : 'The details only you know: the situation, your decisions, the numbers and what you learned.'}
          {due && interview.status === 'pending' ? (
            <>
              {' Due '}
              <time dateTime={interview.dueAt ?? undefined}>{due}</time>.
            </>
          ) : null}
        </p>
        {interview.status === 'expired' ? (
          <Alert variant="warn" icon={<Hourglass aria-hidden="true" />}>
            <AlertTitle>{due ? `Time ran out on ${due}` : 'Time ran out'}</AlertTitle>
            <AlertDescription>
              <p>
                Drafting went ahead with your story bank. Late answers still count: they become story material and voice
                samples.
              </p>
            </AlertDescription>
          </Alert>
        ) : null}
        <div className="grid gap-1.5">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span id={`${ids}progress`} className="font-medium text-ink">
              Answers confirmed
            </span>
            <span aria-hidden="true" className="tabular-nums text-ink-muted">
              {`${String(confirmedCount)} of ${String(total)}`}
            </span>
          </div>
          <Progress
            size="sm"
            value={confirmedCount}
            max={Math.max(total, 1)}
            aria-labelledby={`${ids}progress`}
            getValueLabel={() => `${String(confirmedCount)} of ${String(total)}`}
          />
        </div>
      </header>

      <div className="grid gap-8 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)] md:gap-10">
        {desktop ? (
          <nav aria-label="Interview questions" className="self-start md:sticky md:top-6">
            <QuestionList {...list} />
          </nav>
        ) : null}
        <div className="min-w-0">{step}</div>
      </div>

      {desktop ? null : <QuestionSheet {...list} open={sheetOpen} onOpenChange={setSheetOpen} />}

      <AlertDialog open={askPartial} onOpenChange={setAskPartial}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{`Send ${String(confirmedCount)} of ${String(total)} answers?`}</AlertDialogTitle>
            <AlertDialogDescription>{partialSendWarning(unconfirmed)}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep answering</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                sendAnswers(entry.etag);
              }}
            >
              {`Send ${count(confirmedCount, 'answer', 'answers')}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={askSkip} onOpenChange={setAskSkip}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Skip this interview?</AlertDialogTitle>
            <AlertDialogDescription>
              {hasDrafts
                ? 'Drafting goes on with your story bank and positions instead. The answers you started here are dropped.'
                : 'Drafting goes on with your story bank and positions instead.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep the interview</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                skipInterview(entry.etag);
              }}
            >
              Skip the interview
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ConflictDialog
        {...conflict}
        subject="This interview"
        mine={retry === 'answers' ? answersText(questions, lastSent) : undefined}
        theirs={retry === 'answers' ? theirs?.text : undefined}
        saving={sending}
        onKeepMine={() => {
          const etag = conflict.conflict?.etag ?? theirs?.etag ?? undefined;
          if (!etag) {
            conflict.close();
            onReload();
            return;
          }
          if (retry === 'skip') skipInterview(etag);
          else sendAnswers(etag);
        }}
        onTakeTheirs={() => {
          conflict.close();
          if (retry === 'answers') {
            clear();
            goTo(0);
          }
          onReload();
        }}
      />
    </section>
  );
}

function SessionProblemAlert({ problem, onRetry }: { problem: SessionProblem; onRetry: () => void }): ReactNode {
  switch (problem.kind) {
    case 'confidential':
      return (
        <Alert variant="error">
          <AlertTitle>Some wording can’t be sent yet</AlertTitle>
          <AlertDescription>
            <p>
              {problem.numbers.length > 0
                ? `${questionNumbers(problem.numbers)} still ${problem.numbers.length === 1 ? 'has' : 'have'} ` +
                  'details that block saving. Fix the wording and send again.'
                : 'Some answers still have details that block saving. Fix the wording and send again.'}
            </p>
          </AlertDescription>
        </Alert>
      );
    case 'moved':
      return (
        <Alert variant="warn">
          <AlertTitle>This interview changed elsewhere</AlertTitle>
          <AlertDescription>
            <p>It was answered or skipped on another device or tab. Loading the saved version.</p>
          </AlertDescription>
        </Alert>
      );
    case 'failed':
      return (
        <ProblemAlert
          error={problem.error}
          title={problem.action === 'skip' ? 'Couldn’t skip the interview.' : 'Couldn’t send the answers.'}
          onRetry={onRetry}
          retryLabel={problem.action === 'skip' ? 'Try skipping again' : 'Try sending again'}
        />
      );
  }
}
