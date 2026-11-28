import { ArrowRight, CircleCheck, Clock, Hourglass, SkipForward, type LucideIcon } from 'lucide-react';
import { useId } from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { JobTimeline } from '@/features/jobs/job-timeline';

import {
  formatMoment,
  INTERVIEW_STATUS_LABELS,
  PIPELINE_STEP_LABELS,
  type Interview,
  type InterviewEntry,
  type InterviewStatus,
} from './interview-model';

const STATUS_STYLE: Readonly<Record<InterviewStatus, { variant: BadgeProps['variant']; icon: LucideIcon }>> = {
  pending: { variant: 'warn', icon: Clock },
  expired: { variant: 'warn', icon: Hourglass },
  answered: { variant: 'success', icon: CircleCheck },
  skipped: { variant: 'neutral', icon: SkipForward },
};

/** An interview's status as a badge: an icon and the words, never colour alone. */
export function InterviewStatusBadge({ status }: { status: InterviewStatus }) {
  const { variant, icon: Icon } = STATUS_STYLE[status];
  return (
    <Badge variant={variant}>
      <Icon aria-hidden="true" />
      {INTERVIEW_STATUS_LABELS[status]}
    </Badge>
  );
}

/** The questions and the confirmed answers, read-only. */
function AnswerList({ interview, level }: { interview: Interview; level: 3 | 4 }) {
  const answers = new Map((interview.answers ?? []).map((answer) => [answer.questionId, answer]));
  const questionClass = 'font-serif text-lg font-medium text-ink';
  return (
    <ol className="grid max-w-measure border-t border-line">
      {interview.questions.map((question, index) => {
        const answer = answers.get(question.id);
        return (
          <li key={question.id} className="grid gap-2 border-b border-line py-4">
            <p className="text-xs font-medium text-ink-subtle">{`Question ${String(index + 1)}`}</p>
            {level === 3 ? (
              <h3 className={questionClass}>{question.text}</h3>
            ) : (
              <h4 className={questionClass}>{question.text}</h4>
            )}
            {answer ? (
              <p className="whitespace-pre-wrap break-words font-serif leading-prose text-ink">{answer.text}</p>
            ) : (
              <p className="text-sm text-ink-muted">Not answered.</p>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function statusLine(interview: Interview): string | undefined {
  const at = formatMoment(interview.statusChangedAt);
  if (!at) return undefined;
  switch (interview.status) {
    case 'answered':
      return `Answered ${at}.`;
    case 'skipped':
      return `Skipped ${at}.`;
    case 'expired':
      return `Time ran out ${at}.`;
    case 'pending':
      return undefined;
  }
}

export interface InterviewRecordProps {
  entry: InterviewEntry;
  /** The article this interview belongs to, for "Open the draft" (posts have no Draft tab). */
  articleId?: string;
  /** Just sent or skipped in this tab: say so, and show what the pipeline does next. */
  justSaved?: 'answered' | 'skipped' | null;
}

/** A closed interview (answered or skipped), read-only. */
export function InterviewRecord({ entry, articleId, justSaved = null }: InterviewRecordProps) {
  const interview = entry.item;
  const ids = useId();
  const line = statusLine(interview);
  return (
    <section aria-labelledby={`${ids}title`} className="grid gap-6">
      {justSaved === 'answered' ? (
        <Alert variant="success" role="status">
          <AlertTitle>Answers sent</AlertTitle>
          <AlertDescription>
            <p>Drafting picks up from here. Each answer also joins your voice samples.</p>
          </AlertDescription>
          {articleId ? (
            <AlertActions>
              <Button asChild size="sm" variant="secondary" className="pointer-coarse:min-h-11">
                <Link to={`/studio/${encodeURIComponent(articleId)}/draft`}>
                  Open the draft
                  <ArrowRight aria-hidden="true" />
                </Link>
              </Button>
            </AlertActions>
          ) : null}
        </Alert>
      ) : null}
      {justSaved === 'skipped' ? (
        <Alert variant="info" role="status">
          <AlertTitle>Interview skipped</AlertTitle>
          <AlertDescription>
            <p>Drafting goes on with your story bank and positions.</p>
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="grid max-w-measure gap-2">
        <h2 id={`${ids}title`} className="font-serif text-2xl font-medium text-ink">
          Your interview
        </h2>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-muted">
          <InterviewStatusBadge status={interview.status} />
          {line ? <span>{line}</span> : null}
        </p>
      </div>
      {interview.status === 'skipped' ? (
        <p className="max-w-measure text-ink-muted">
          You skipped it, so drafting went ahead with your story bank and positions.
        </p>
      ) : (
        <AnswerList interview={interview} level={3} />
      )}
      {justSaved && interview.jobId ? (
        <JobTimeline jobId={interview.jobId} title="What happens next" labels={PIPELINE_STEP_LABELS} />
      ) : null}
    </section>
  );
}

/** Interviews from earlier pipeline runs of this piece, folded away. */
export function EarlierInterviews({ entries }: { entries: readonly InterviewEntry[] }) {
  const ids = useId();
  if (entries.length === 0) return null;
  return (
    <section aria-labelledby={`${ids}title`} className="grid gap-3">
      <h2 id={`${ids}title`} className="text-base font-semibold text-ink">
        Earlier interviews
      </h2>
      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="link" size="sm" className="justify-self-start pointer-coarse:min-h-11">
            {entries.length === 1
              ? 'Show the earlier interview'
              : `Show the ${String(entries.length)} earlier interviews`}
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-4 grid gap-8">
            {entries.map((entry) => {
              const created = formatMoment(entry.item.createdAt);
              return (
                <article key={entry.item.id} aria-labelledby={`${ids}${entry.item.id}`} className="grid gap-3">
                  <h3 id={`${ids}${entry.item.id}`} className="font-serif text-lg font-medium text-ink">
                    {created ? `Interview of ${created}` : 'Earlier interview'}
                  </h3>
                  <p className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
                    <InterviewStatusBadge status={entry.item.status} />
                    {entry.item.status === 'pending' || entry.item.status === 'expired' ? (
                      <span>A newer interview replaced it.</span>
                    ) : null}
                  </p>
                  {entry.item.status === 'answered' ? <AnswerList interview={entry.item} level={4} /> : null}
                </article>
              );
            })}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
