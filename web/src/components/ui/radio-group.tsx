import { RadioGroup as RadioGroupPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * One choice from a short list, all visible. Arrow keys move the choice. Name the group (a
 * `Fieldset` legend, or `aria-label`) and each item (a `Label` with `htmlFor`).
 */
export function RadioGroup({ className, ...props }: ComponentProps<typeof RadioGroupPrimitive.Root>) {
  return <RadioGroupPrimitive.Root data-slot="radio-group" className={cn('grid gap-3', className)} {...props} />;
}

export function RadioGroupItem({ className, ...props }: ComponentProps<typeof RadioGroupPrimitive.Item>) {
  return (
    <RadioGroupPrimitive.Item
      data-slot="radio-group-item"
      className={cn(
        'peer relative inline-flex size-4.5 shrink-0 items-center justify-center rounded-full',
        'border border-control bg-sheet transition-colors duration-150 data-[state=checked]:border-ink',
        'aria-invalid:border-danger disabled:cursor-not-allowed disabled:opacity-50',
        'pointer-coarse:size-5 pointer-coarse:after:absolute pointer-coarse:after:-inset-3',
        className,
      )}
      {...props}
    >
      <RadioGroupPrimitive.Indicator data-slot="radio-group-indicator" className="size-2.5 rounded-full bg-ink" />
    </RadioGroupPrimitive.Item>
  );
}
