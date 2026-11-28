import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export function Kbd({ className, ...props }: ComponentProps<'kbd'>) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        'inline-flex h-5 min-w-5 items-center justify-center rounded-sm border border-line bg-sunken px-1.5',
        'font-sans text-xs font-medium text-ink-muted',
        className,
      )}
      {...props}
    />
  );
}
