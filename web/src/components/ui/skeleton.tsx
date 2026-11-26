import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/** A placeholder block while content loads. Decorative: announce loading elsewhere. */
export function Skeleton({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      aria-hidden="true"
      data-slot="skeleton"
      className={cn('animate-pulse-soft rounded-md bg-wash-strong', className)}
      {...props}
    />
  );
}
