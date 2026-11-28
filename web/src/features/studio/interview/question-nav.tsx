import { Circle, CircleAlert, CircleCheck, CircleDashed, CircleDot, Send, type LucideIcon } from 'lucide-react';
import { useRef } from 'react';

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { cn } from '@/lib/cn';

import { QUESTION_PROGRESS_LABELS, type InterviewQuestion, type QuestionProgress } from './interview-model';

/** Where the owner is: a question (by index) or the final "check and send" step. */
export type InterviewPosition = number | 'send';

const PROGRESS_ICONS: Readonly<Record<QuestionProgress, { icon: LucideIcon; className: string }>> = {
  empty: { icon: Circle, className: 'text-ink-subtle' },
  draft: { icon: CircleDashed, className: 'text-ink-muted' },
  review: { icon: CircleDot, className: 'text-ink' },
  confirmed: { icon: CircleCheck, className: 'text-success' },
  fix: { icon: CircleAlert, className: 'text-danger' },
};

const rowClass = cn(
  'flex min-h-11 w-full items-start gap-3 rounded-md px-3 py-2 text-left',
  'transition-colors duration-150 hover:bg-wash aria-[current=step]:bg-wash-strong',
);

export interface QuestionListProps {
  questions: readonly InterviewQuestion[];
  progress: (questionId: string) => QuestionProgress;
  position: InterviewPosition;
  confirmedCount: number;
  onSelect: (position: InterviewPosition) => void;
  className?: string;
}

/**
 * The interview's questions in order, each a whole-row target with its state in an icon and in
 * words, and the last step, "Check and send". The current one is `aria-current="step"`.
 */
export function QuestionList({ questions, progress, position, confirmedCount, onSelect, className }: QuestionListProps) {
  return (
    <ol className={cn('grid gap-1', className)}>
      {questions.map((question, index) => {
        const state = progress(question.id);
        const { icon: Icon, className: tone } = PROGRESS_ICONS[state];
        return (
          <li key={question.id}>
            <button
              type="button"
              aria-current={position === index ? 'step' : undefined}
              className={rowClass}
              onClick={() => {
                onSelect(index);
              }}
            >
              <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
              <span className="grid min-w-0 gap-0.5">
                <span className="text-sm font-semibold text-ink">{`Question ${String(index + 1)}`}</span>
                <span className="line-clamp-2 text-sm text-ink-muted">{question.text}</span>
                <span className="text-xs text-ink-subtle">{QUESTION_PROGRESS_LABELS[state]}</span>
              </span>
            </button>
          </li>
        );
      })}
      <li>
        <button
          type="button"
          aria-current={position === 'send' ? 'step' : undefined}
          className={rowClass}
          onClick={() => {
            onSelect('send');
          }}
        >
          <Send aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-muted" />
          <span className="grid min-w-0 gap-0.5">
            <span className="text-sm font-semibold text-ink">Check and send</span>
            <span className="text-xs text-ink-subtle">
              {`${String(confirmedCount)} of ${String(questions.length)} confirmed`}
            </span>
          </span>
        </button>
      </li>
    </ol>
  );
}

export interface QuestionSheetProps extends QuestionListProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * The question list on phones: a sheet from the bottom, under the thumb. Picking a step closes
 * it and leaves focus to the step that opens (its heading), instead of the button that opened
 * the sheet.
 */
export function QuestionSheet({ open, onOpenChange, onSelect, ...list }: QuestionSheetProps) {
  const picked = useRef(false);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        onCloseAutoFocus={(event) => {
          if (picked.current) event.preventDefault();
          picked.current = false;
        }}
      >
        <SheetHeader>
          <SheetTitle>Questions</SheetTitle>
          <SheetDescription>
            {`${String(list.confirmedCount)} of ${String(list.questions.length)} answers confirmed.`}
          </SheetDescription>
        </SheetHeader>
        <nav aria-label="Interview questions" className="-mx-3 overflow-y-auto">
          <QuestionList
            {...list}
            onSelect={(position) => {
              picked.current = true;
              onSelect(position);
              onOpenChange(false);
            }}
          />
        </nav>
      </SheetContent>
    </Sheet>
  );
}
