import { CircleX, TriangleAlert } from 'lucide-react';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

import { findingExcerpt, findingLabel, isBlocking, type MarkedFinding } from '../findings';

export interface FindingListProps extends Omit<ComponentProps<'ol'>, 'children'> {
  /** The text the findings point into, for the quoted excerpt of each one. */
  text: string;
  marks: readonly MarkedFinding[];
}

/**
 * The findings as an ordered list that matches the numbers on the marked text: what kind of
 * detail it is, the words it points at and the server's advice ("Round it into a bucket…").
 * Details to fix and details to check differ by icon and word, not colour alone.
 */
export function FindingList({ text, marks, className, ...props }: FindingListProps) {
  if (marks.length === 0) return null;
  return (
    <ol data-slot="finding-list" className={cn('grid gap-3', className)} {...props}>
      {marks.map((mark) => {
        const blocking = isBlocking(mark.finding);
        const Icon = blocking ? CircleX : TriangleAlert;
        const excerpt = findingExcerpt(text, mark.span);
        return (
          <li key={mark.number} className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3">
            <span
              aria-hidden="true"
              className={cn(
                'mt-0.5 inline-flex size-6 items-center justify-center rounded-full text-xs font-semibold tabular-nums',
                blocking ? 'bg-danger-soft text-danger' : 'bg-warn-soft text-warn',
              )}
            >
              {mark.number}
            </span>
            <div className="min-w-0 space-y-0.5 text-sm">
              <p className="flex flex-wrap items-center gap-x-1.5 font-semibold text-ink">
                <Icon aria-hidden="true" className={cn('size-4 shrink-0', blocking ? 'text-danger' : 'text-warn')} />
                <span>{`${blocking ? 'Fix' : 'Check'}: ${findingLabel(mark.finding.code)}`}</span>
              </p>
              {excerpt ? <p className="break-words text-ink">{`“${excerpt}”`}</p> : null}
              <p className="text-ink-muted">{mark.finding.message}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
