import { RadioGroup as RadioGroupPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * One choice from two to five short options, always set: a week's mode (Normal, Light, Away), a
 * preview width (Mobile, Desktop). It is a radio group, so arrow keys move the choice and it can
 * never be cleared. The chosen segment looks like a sheet of paper on the desk, like the current
 * page in the sidebar.
 *
 * Name it (`aria-label`, or `aria-labelledby` from a `Fieldset`). `fullWidth` spreads the
 * segments across the row, which suits phones.
 */
export function SegmentedControl({
  className,
  fullWidth = false,
  ...props
}: ComponentProps<typeof RadioGroupPrimitive.Root> & { fullWidth?: boolean }) {
  return (
    <RadioGroupPrimitive.Root
      data-slot="segmented-control"
      orientation="horizontal"
      className={cn(
        'items-center gap-1 rounded-md bg-sunken p-1 shadow-[inset_0_0_0_1px_var(--color-line)]',
        fullWidth ? 'flex w-full' : 'inline-flex w-fit',
        className,
      )}
      {...props}
    />
  );
}

export function SegmentedControlItem({ className, ...props }: ComponentProps<typeof RadioGroupPrimitive.Item>) {
  return (
    <RadioGroupPrimitive.Item
      data-slot="segmented-control-item"
      className={cn(
        'inline-flex h-8 min-w-0 flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-sm px-3',
        'text-sm font-medium text-ink-muted transition-colors duration-150 hover:text-ink',
        'focus-visible:outline-offset-0 disabled:pointer-events-none disabled:opacity-50',
        'data-[state=checked]:bg-sheet data-[state=checked]:font-semibold data-[state=checked]:text-ink',
        'data-[state=checked]:shadow-[0_0_0_1px_var(--color-line-strong)]',
        'pointer-coarse:h-11 [&_svg]:size-4 [&_svg]:shrink-0',
        className,
      )}
      {...props}
    />
  );
}
