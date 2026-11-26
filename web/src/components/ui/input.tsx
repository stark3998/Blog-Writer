import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/** 16px text, so iOS never zooms the page when the field takes focus. */
export const fieldClass = cn(
  'w-full min-w-0 rounded-md border border-control bg-sheet px-3 text-base text-ink',
  'placeholder:text-ink-subtle transition-colors duration-150',
  'focus-visible:outline-2 focus-visible:outline-offset-1',
  'disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger',
);

export function Input({ className, type = 'text', ...props }: ComponentProps<'input'>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(fieldClass, 'flex h-10 pointer-coarse:h-11', className)}
      {...props}
    />
  );
}
