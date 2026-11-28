import { intakeProgress, type IntakeProgress } from '@/features/intake';
import { confidentialFindings } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';

import type { InterviewDrafts } from './draft-store';

/**
 * The interview as the Studio tab works with it: types from the generated schema, the status
 * machine, which interview is current, the answers to send and how a refusal maps back to the
 * questions. Pure functions; no raw text ever reaches them except through the drafts map, and
 * only confirmed wording comes out.
 */

export type Interview = components['schemas']['Interview'];
export type InterviewEntry = components['schemas']['Versioned_Interview_'];
export type InterviewQuestion = components['schemas']['InterviewQuestion'];
export type InterviewAnswer = components['schemas']['InterviewAnswer'];
export type InterviewStatus = components['schemas']['InterviewStatus'];
export type AnswerIn = components['schemas']['AnswerIn'];

/** The longest answer `POST /api/interviews/{id}/answers` takes (`AnswerIn.text`). */
export const ANSWER_MAX_CHARS = 4000;

/**
 * The `interview` machine (contract section 2), used until `/api/meta/machines` answers and if it
 * cannot: pending -> answered, skipped, expired; expired -> answered, skipped.
 */
export const INTERVIEW_MACHINE: Readonly<Record<InterviewStatus, readonly InterviewStatus[]>> = {
  pending: ['answered', 'skipped', 'expired'],
  expired: ['answered', 'skipped'],
  answered: [],
  skipped: [],
};

/** Still takes answers or a skip: pending, or expired (late answers still count). */
export function isOpenInterview(status: InterviewStatus): boolean {
  return status === 'pending' || status === 'expired';
}

export const INTERVIEW_STATUS_LABELS: Readonly<Record<InterviewStatus, string>> = {
  pending: 'Waiting for your answers',
  expired: 'Time ran out',
  answered: 'Answered',
  skipped: 'Skipped',
};

/**
 * The interview to show and the earlier ones. The current one is the article's own
 * (`interviewId`), else the newest still open, else the newest. Entries come newest first.
 */
export function pickInterviews(
  entries: readonly InterviewEntry[],
  preferredId?: string | null,
): { current: InterviewEntry | undefined; earlier: InterviewEntry[] } {
  const current =
    (preferredId ? entries.find((entry) => entry.item.id === preferredId) : undefined) ??
    entries.find((entry) => isOpenInterview(entry.item.status)) ??
    entries[0];
  return { current, earlier: entries.filter((entry) => entry !== current) };
}

/** The newer of two versions of the same interview (`updatedAt` is fixed-width ISO: string order is time order). */
export function newerEntry(a: InterviewEntry, b: InterviewEntry | undefined): InterviewEntry {
  if (b?.item.id !== a.item.id) return a;
  return b.item.updatedAt > a.item.updatedAt ? b : a;
}

/** A question's progress in the drafts, plus the server's "needs a fix". */
export type QuestionProgress = IntakeProgress | 'fix';

export function questionProgress(
  drafts: InterviewDrafts,
  questionId: string,
  fixIds: ReadonlySet<string>,
): QuestionProgress {
  const progress = intakeProgress(drafts.get(questionId));
  return fixIds.has(questionId) && progress !== 'confirmed' ? 'fix' : progress;
}

export const QUESTION_PROGRESS_LABELS: Readonly<Record<QuestionProgress, string>> = {
  empty: 'Not started',
  draft: 'Draft',
  review: 'Wording to confirm',
  confirmed: 'Confirmed',
  fix: 'Needs a fix',
};

/** The confirmed answers, in question order: exactly what `POST /api/interviews/{id}/answers` sends. */
export function answersToSend(questions: readonly InterviewQuestion[], drafts: InterviewDrafts): AnswerIn[] {
  return questions.flatMap((question) => {
    const confirmed = drafts.get(question.id)?.confirmed;
    if (!confirmed || confirmed.text.trim() === '') return [];
    return [{ questionId: question.id, text: confirmed.text, redactionFindings: confirmed.redactionFindings }];
  });
}

/** Where to go after question `index` is confirmed: the next unconfirmed question after it, else "check and send". */
export function nextPosition(
  questions: readonly InterviewQuestion[],
  drafts: InterviewDrafts,
  index: number,
): number | 'send' {
  const next = questions.findIndex(
    (question, at) => at > index && intakeProgress(drafts.get(question.id)) !== 'confirmed',
  );
  return next === -1 ? 'send' : next;
}

/** Where to start: the first question without a confirmed answer, else "check and send". */
export function firstPosition(questions: readonly InterviewQuestion[], drafts: InterviewDrafts): number | 'send' {
  if (questions.length === 0) return 'send';
  return nextPosition(questions, drafts, -1);
}

const ANSWER_FIELD = /^answers(?:\[(\d+)\]|\.(\d+))(?:\.text)?$/;

/**
 * The questions a `confidential_text` refusal of the answers points at. The server names the
 * field of each answer it refused (`answers[1].text`), indexing the answers as they were sent.
 */
export function refusedQuestionIds(error: unknown, sent: readonly AnswerIn[]): string[] {
  const ids = new Set<string>();
  for (const finding of confidentialFindings(error)) {
    const match = ANSWER_FIELD.exec(finding.field);
    const index = Number(match?.[1] ?? match?.[2] ?? Number.NaN);
    const answer = Number.isInteger(index) ? sent[index] : undefined;
    if (answer) ids.add(answer.questionId);
  }
  return [...ids];
}

/** Answers as plain text for the conflict dialog's comparison (public-safe wording only). */
export function answersText(
  questions: readonly InterviewQuestion[],
  answers: readonly Pick<AnswerIn, 'questionId' | 'text'>[],
): string {
  const byQuestion = new Map(answers.map((answer) => [answer.questionId, answer.text]));
  return questions
    .map((question, index) => {
      const answer = byQuestion.get(question.id) ?? '(not answered)';
      return `${String(index + 1)}. ${question.text}\n\n${answer}`;
    })
    .join('\n\n');
}

const DUE = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

/** "Wed, Nov 26, 6:00 PM" in the owner's time zone, or `undefined` for a missing or unreadable time. */
export function formatMoment(at: string | null | undefined): string | undefined {
  if (!at) return undefined;
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? undefined : DUE.format(date);
}

/** The pipeline steps as the owner knows them, for the timeline after the answers are sent. */
export const PIPELINE_STEP_LABELS: Readonly<Record<string, string>> = {
  research: 'Research',
  interview_questions: 'Questions written',
  interview: 'Your answers',
  outline: 'Outline',
  draft: 'Draft',
  normalize: 'Tidy the Markdown',
  critique_revise: 'Critique and revise',
  gates: 'Quality gates',
  assets: 'Diagrams and images',
  finalize: 'Ready for review',
};
