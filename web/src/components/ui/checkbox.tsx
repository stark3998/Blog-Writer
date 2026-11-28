import { Check, Minus } from 'lucide-react';
import { Checkbox as CheckboxPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * A checkbox: `checked` is `true`, `false` or `'indeterminate'`. Name it with a `Label` (or use it
 * in a `Field`). On touch screens the box grows and its hit area reaches 44 px.
 */
export function Checkbox({ className, ...props }: ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        'peer group relative inline-flex size-4.5 shrink-0 items-center justify-center rounded-[0.25rem]',
        'border border-control bg-sheet text-ink-inverse transition-colors duration-150',
        'data-[state=checked]:border-ink data-[state=checked]:bg-ink',
        'data-[state=indeterminate]:border-ink data-[state=indeterminate]:bg-ink',
        'aria-invalid:border-danger disabled:cursor-not-allowed disabled:opacity-50',
        'pointer-coarse:size-5 pointer-coarse:after:absolute pointer-coarse:after:-inset-3',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator data-slot="checkbox-indicator" className="flex items-center justify-center">
        <Check aria-hidden="true" strokeWidth={3} className="size-3 group-data-[state=indeterminate]:hidden" />
        <Minus aria-hidden="true" strokeWidth={3} className="hidden size-3 group-data-[state=indeterminate]:block" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
