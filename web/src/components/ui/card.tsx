import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/** A bordered surface for one self-contained item. Not a layout tool: use whitespace first. */
export function Card({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div data-slot="card" className={cn('rounded-lg border border-line bg-sheet text-ink', className)} {...props} />
  );
}

export function CardHeader({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card-header" className={cn('flex flex-col gap-1 p-5', className)} {...props} />;
}

export function CardTitle({ className, ...props }: ComponentProps<'h3'>) {
  return <h3 data-slot="card-title" className={cn('text-base font-semibold', className)} {...props} />;
}

export function CardDescription({ className, ...props }: ComponentProps<'p'>) {
  return <p data-slot="card-description" className={cn('text-sm text-ink-muted', className)} {...props} />;
}

export function CardContent({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="card-content" className={cn('px-5 pb-5', className)} {...props} />;
}

export function CardFooter({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="card-footer"
      className={cn('flex items-center gap-3 border-t border-line px-5 py-4', className)}
      {...props}
    />
  );
}
