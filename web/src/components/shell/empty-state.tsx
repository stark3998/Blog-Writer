import { useId, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

interface EmptyStateProps {
  /** A plain sentence about what is (not) here yet. */
  title: string;
  /** What will appear here, and what the system does on its own. */
  children: ReactNode;
  /** What the owner does here. Shown as a margin note beside the text on wide screens. */
  yourPart: ReactNode;
  /** `lg` is for the one empty state a page leads with. */
  size?: 'md' | 'lg';
  className?: string;
}

/**
 * An honest empty state: what will appear, and the owner's part in it.
 * The "Your part" note sits in the margin like an editor's note on a proof.
 */
export function EmptyState({ title, children, yourPart, size = 'md', className }: EmptyStateProps) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={headingId}
      className={cn(
        'grid gap-x-12 gap-y-6 lg:grid-cols-[minmax(0,var(--container-measure))_minmax(0,17rem)]',
        className,
      )}
    >
      <div className="max-w-measure">
        <h2
          id={headingId}
          className={cn('font-serif font-medium text-ink', size === 'lg' ? 'text-3xl' : 'text-2xl')}
        >
          {title}
        </h2>
        <div className="mt-3 space-y-4 text-ink-muted">{children}</div>
      </div>
      <div className="max-w-measure self-start border-t-2 border-ink pt-3 text-sm lg:mt-1.5">
        <p className="font-semibold text-ink">Your part</p>
        <div className="mt-1 text-ink-muted">{yourPart}</div>
      </div>
    </section>
  );
}
