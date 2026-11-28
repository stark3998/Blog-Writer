import { WifiOff } from 'lucide-react';
import { useId, type ReactNode } from 'react';

import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/cn';

import { useOnline } from '@/lib/use-online';

interface SettingsBlockProps {
  /** The block's `h2`, in the serif: what this part of the page is about. */
  title: string;
  /** One or two sentences under the title: what it does and what the owner decides here. */
  description?: ReactNode;
  /** Actions for the whole block ("Add a source", "Crawl now"), beside the title on wide screens. */
  actions?: ReactNode;
  /** An anchor for links into the block (`/settings/integrations#push`). */
  id?: string;
  className?: string;
  children: ReactNode;
}

/**
 * One block of a Settings section: a serif `h2`, a standfirst and the content. Sections stack
 * blocks with generous space between them, like the parts of a long proof.
 */
export function SettingsBlock({ title, description, actions, id, className, children }: SettingsBlockProps) {
  const headingId = useId();
  return (
    <section id={id} aria-labelledby={headingId} className={cn('scroll-mt-24', className)}>
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="max-w-measure">
          <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
            {title}
          </h2>
          {description ? <div className="mt-2 space-y-2 text-ink-muted">{description}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className="mt-6">{children}</div>
    </section>
  );
}

/** A smaller heading inside a block (`h3`), for a group of facts or fields. */
export function SubHeading({ children, className, id }: { children: ReactNode; className?: string; id?: string }) {
  return (
    <h3 id={id} className={cn('font-semibold text-ink', className)}>
      {children}
    </h3>
  );
}

/**
 * What a block shows while its data loads: a few lines of skeleton and a status for screen readers.
 * Offline, reads wait for the connection, so it says that instead of loading forever.
 */
export function BlockLoading({ label, lines = 3, className }: { label: string; lines?: number; className?: string }) {
  const online = useOnline();
  if (!online) {
    return (
      <p role="status" className={cn('flex max-w-measure items-start gap-2 text-ink-muted', className)}>
        <WifiOff aria-hidden="true" className="mt-1 size-4 shrink-0 text-ink-subtle" />
        You’re offline. This loads when the connection is back.
      </p>
    );
  }
  return (
    <div className={cn('max-w-measure', className)}>
      <p role="status" className="sr-only">
        {label}
      </p>
      <Skeleton className="h-5 w-2/5" />
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} className={cn('mt-3 h-4', index % 2 === 0 ? 'w-full' : 'w-4/5')} />
      ))}
    </div>
  );
}

/** Space between the blocks of a section. */
export function SectionStack({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('space-y-16', className)}>{children}</div>;
}
