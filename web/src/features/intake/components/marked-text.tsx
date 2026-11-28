import { Fragment, useMemo, type ComponentProps } from 'react';

import { cn } from '@/lib/cn';

import { isBlocking, segmentText, type MarkedFinding } from '../findings';

export interface MarkedTextProps extends Omit<ComponentProps<'div'>, 'children'> {
  /** The text the marks point into. */
  text: string;
  /** From `markFindings(text, findings)`: the same numbers the finding list shows. */
  marks: readonly MarkedFinding[];
  /** Names the block for screen readers ("Your words, with the details marked"). */
  label: string;
}

/**
 * Text marked up like a proof: each finding's words on a tint with an underline (wavy for
 * details to fix, dotted for details to check) and its number beside it, matching the list.
 * Never colour alone: the line style, the number and a visually hidden "fix 1" / "check 2"
 * carry it too.
 */
export function MarkedText({ text, marks, label, className, ...props }: MarkedTextProps) {
  const segments = useMemo(() => segmentText(text, marks), [text, marks]);
  return (
    <div
      role="group"
      aria-label={label}
      data-slot="marked-text"
      className={cn('whitespace-pre-wrap break-words font-serif text-lg leading-prose text-ink', className)}
      {...props}
    >
      {segments.map((segment, index) =>
        segment.mark ? (
          <Mark key={index} text={segment.text} mark={segment.mark} />
        ) : (
          <Fragment key={index}>{segment.text}</Fragment>
        ),
      )}
    </div>
  );
}

function Mark({ text, mark }: { text: string; mark: MarkedFinding }) {
  const blocking = isBlocking(mark.finding);
  return (
    <mark
      data-severity={mark.finding.severity}
      className={cn(
        'rounded-sm px-0.5 text-ink underline underline-offset-4 [text-decoration-skip-ink:none]',
        blocking
          ? 'bg-danger-soft decoration-danger decoration-wavy'
          : 'bg-warn-soft decoration-warn decoration-dotted decoration-2',
      )}
    >
      {text}
      <sup
        aria-hidden="true"
        className={cn(
          'ml-0.5 inline-block font-sans text-xs font-semibold tabular-nums',
          blocking ? 'text-danger' : 'text-warn',
        )}
      >
        {mark.number}
      </sup>
      <span className="sr-only">{` (${blocking ? 'fix' : 'check'} ${String(mark.number)})`}</span>
    </mark>
  );
}
