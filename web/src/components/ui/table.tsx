import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * A plain data table on hairlines, for facts that line up in columns (jobs, snapshots, sources).
 * It scrolls sideways on narrow screens rather than squeezing. Give it a `TableCaption` (or an
 * `aria-label`) so it is named; numbers go in `numeric` cells so their digits line up.
 */
export function Table({ className, ...props }: ComponentProps<'table'>) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table
        data-slot="table"
        className={cn('w-full caption-bottom border-collapse text-left text-sm', className)}
        {...props}
      />
    </div>
  );
}

export function TableHeader({ className, ...props }: ComponentProps<'thead'>) {
  return <thead data-slot="table-header" className={cn('border-b border-line-strong', className)} {...props} />;
}

export function TableBody({ className, ...props }: ComponentProps<'tbody'>) {
  return <tbody data-slot="table-body" className={className} {...props} />;
}

export function TableFooter({ className, ...props }: ComponentProps<'tfoot'>) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn('border-t border-line-strong font-semibold text-ink', className)}
      {...props}
    />
  );
}

/** `data-state="selected"` marks a chosen row. */
export function TableRow({ className, ...props }: ComponentProps<'tr'>) {
  return (
    <tr
      data-slot="table-row"
      className={cn('border-b border-line last:border-b-0 data-[state=selected]:bg-wash-strong', className)}
      {...props}
    />
  );
}

interface CellOptions {
  /** Right-aligned with equal-width digits, so a column of numbers lines up. */
  numeric?: boolean;
}

export function TableHead({ className, numeric = false, scope = 'col', ...props }: ComponentProps<'th'> & CellOptions) {
  return (
    <th
      data-slot="table-head"
      scope={scope}
      className={cn(
        'h-10 whitespace-nowrap px-3 align-bottom font-semibold text-ink-subtle first:pl-0 last:pr-0',
        numeric && 'text-right',
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, numeric = false, ...props }: ComponentProps<'td'> & CellOptions) {
  return (
    <td
      data-slot="table-cell"
      className={cn('px-3 py-3 align-top text-ink first:pl-0 last:pr-0', numeric && 'text-right tabular-nums', className)}
      {...props}
    />
  );
}

export function TableCaption({ className, ...props }: ComponentProps<'caption'>) {
  return <caption data-slot="table-caption" className={cn('mt-3 text-left text-sm text-ink-muted', className)} {...props} />;
}
