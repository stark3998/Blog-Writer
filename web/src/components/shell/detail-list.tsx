import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/cn';

export interface DetailItem {
  id: string;
  term: ReactNode;
  detail: ReactNode;
  /** A short fact aligned beside the term, such as a duration or a length. */
  meta?: ReactNode;
  icon?: LucideIcon;
  /** A pillar thread class (for example `bg-agents`) drawn before the term. */
  thread?: string;
}

/*
 * Rows sit on hairlines. On phones each row stacks; from `sm` up the rows share
 * columns through CSS subgrid, so the term column is exactly as wide as its
 * longest term (up to a cap) and every row lines up.
 */
const listColumns = {
  plain: 'sm:grid sm:grid-cols-[fit-content(15rem)_minmax(0,1fr)] sm:gap-x-10',
  meta: 'sm:grid sm:grid-cols-[fit-content(15rem)_fit-content(9rem)_minmax(0,1fr)] sm:gap-x-10',
};
const rowClass =
  'grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 border-b border-line py-4 sm:col-span-full sm:grid-cols-subgrid';

function Term({ term, icon: Icon, thread }: Pick<DetailItem, 'term' | 'icon' | 'thread'>) {
  return (
    <>
      {thread ? <span aria-hidden="true" className={cn('mt-1 h-4 w-1 shrink-0 rounded-full', thread)} /> : null}
      {Icon ? <Icon className="mt-1 size-4 shrink-0 text-ink-subtle" aria-hidden="true" /> : null}
      <span>{term}</span>
    </>
  );
}

/** Term and description rows, for facts that belong together (formats, modes, slots). */
export function DetailList({ items, className }: { items: readonly DetailItem[]; className?: string }) {
  const hasMeta = items.some((item) => item.meta !== undefined);
  return (
    <dl className={cn('border-t border-line', hasMeta ? listColumns.meta : listColumns.plain, className)}>
      {items.map(({ id, term, detail, meta, icon, thread }) => (
        <div key={id} className={rowClass}>
          <dt className="flex items-start gap-2.5 font-semibold text-ink">
            <Term term={term} icon={icon} thread={thread} />
          </dt>
          {meta !== undefined ? <dd className="text-right tabular-nums text-ink sm:text-left">{meta}</dd> : null}
          <dd className="col-span-2 text-ink-muted sm:col-span-1">{detail}</dd>
        </div>
      ))}
    </dl>
  );
}

export interface TimelineStep {
  when: string;
  what: ReactNode;
}

/** Steps that happen in order. An ordered list, because the order is the information. */
export function Timeline({ steps, className }: { steps: readonly TimelineStep[]; className?: string }) {
  return (
    <ol className={cn('border-t border-line', listColumns.plain, className)}>
      {steps.map(({ when, what }) => (
        <li key={when} className={rowClass}>
          <span className="col-span-2 font-semibold text-ink sm:col-span-1">{when}</span>
          <span className="col-span-2 text-ink-muted sm:col-span-1">{what}</span>
        </li>
      ))}
    </ol>
  );
}
