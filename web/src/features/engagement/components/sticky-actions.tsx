import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * The actions of a step inside a sheet, kept at the bottom edge while the step scrolls, so the
 * main one stays under the thumb on a phone. Buttons share the row on phones (the main one last,
 * on the right) and sit at the end on wider screens.
 */
export function StickyActions({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="sticky-actions"
      className={cn(
        'sticky bottom-0 z-10 -mx-6 flex gap-2 border-t border-line bg-raised px-6 pt-3',
        'pb-[max(0.75rem,env(safe-area-inset-bottom))]',
        '*:flex-1 sm:justify-end sm:*:flex-none',
        className,
      )}
      {...props}
    />
  );
}
