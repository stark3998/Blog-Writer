import { useId, type ReactNode } from 'react';

import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { cn } from '@/lib/cn';

export interface LibrarySectionProps {
  /** The section's heading (the page's `h2`). */
  title: string;
  /** One or two sentences: what this section is for and what the system does with it. */
  standfirst: ReactNode;
  /** What the owner does here: the margin note, as on every empty state in the app. */
  yourPart: ReactNode;
  /** Actions that belong to the whole section, under the standfirst. */
  actions?: ReactNode;
  /** The heading's id, when something returns focus to it (generated otherwise). */
  headingId?: string;
  children: ReactNode;
}

/**
 * One Library section: a region named by its serif heading, the standfirst, the owner's part as
 * a margin note beside it on wide screens (after it on phones), then the section's content.
 * The heading can take focus, so focus has somewhere sensible to go when a panel closes.
 */
export function LibrarySection({ title, standfirst, yourPart, actions, headingId, children }: LibrarySectionProps) {
  const generated = useId();
  const id = headingId ?? generated;
  return (
    <section aria-labelledby={id} className="space-y-12">
      <div className="grid gap-x-12 gap-y-6 lg:grid-cols-[minmax(0,var(--container-measure))_minmax(0,17rem)]">
        <div className="max-w-measure">
          <h2 id={id} tabIndex={-1} className="font-serif text-2xl font-medium text-ink outline-none">
            {title}
          </h2>
          <div className="mt-3 space-y-3 text-ink-muted">{standfirst}</div>
          {actions ? <div className="mt-5 flex flex-wrap gap-2">{actions}</div> : null}
        </div>
        <div className="max-w-measure self-start border-t-2 border-ink pt-3 text-sm lg:mt-1.5">
          <p className="font-semibold text-ink">Your part</p>
          <div className="mt-1 text-ink-muted">{yourPart}</div>
        </div>
      </div>
      {children}
    </section>
  );
}

export interface SubsectionProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** A block inside a section, under an `h3`, on a hairline. */
export function Subsection({ title, description, actions, children, className }: SubsectionProps) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={cn('border-t border-line pt-6', className)}>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-measure">
          <h3 id={headingId} className="text-lg font-semibold text-ink">
            {title}
          </h3>
          {description ? <div className="mt-1 text-sm text-ink-muted">{description}</div> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** Skeleton rows while a section's data loads, with a word for screen readers. */
export function LoadingBlock({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div className="space-y-3">
      <p role="status" className="sr-only">
        {label}
      </p>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className={cn('h-14', index % 2 === 1 ? 'w-[min(32rem,92%)]' : 'w-[min(40rem,100%)]')} />
      ))}
    </div>
  );
}

export interface LoadErrorProps {
  error: unknown;
  /** "Couldn’t load the pillars." */
  title: string;
  /** "Try loading the pillars again" (several retries can share a page). */
  retryLabel: string;
  onRetry: () => void;
}

/** A failed read, with the request id and "Try again". */
export function LoadError({ error, title, retryLabel, onRetry }: LoadErrorProps) {
  return <ProblemAlert error={error} title={title} onRetry={onRetry} retryLabel={retryLabel} />;
}
