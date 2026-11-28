import { cva, type VariantProps } from 'class-variance-authority';
import { Progress as ProgressPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

const trackVariants = cva('relative w-full overflow-hidden rounded-full bg-wash-strong', {
  variants: {
    size: { sm: 'h-1', md: 'h-1.5', lg: 'h-2' },
  },
  defaultVariants: { size: 'md' },
});

const indicatorVariants = cva('h-full w-full rounded-full transition-transform duration-300 ease-standard', {
  variants: {
    /** Ink by default; a state colour only when the value means one (a quota running out). */
    tone: { ink: 'bg-ink', success: 'bg-success', warn: 'bg-warn', danger: 'bg-danger' },
  },
  defaultVariants: { tone: 'ink' },
});

export interface ProgressProps
  extends Omit<ComponentProps<typeof ProgressPrimitive.Root>, 'value' | 'max'>,
    VariantProps<typeof trackVariants>,
    VariantProps<typeof indicatorVariants> {
  /** How far along; `null` or left out while the amount is unknown (an indeterminate bar). */
  value?: number | null;
  /** Defaults to 100. */
  max?: number;
}

/**
 * A progress bar (`role="progressbar"`). Name it (`aria-label` or `aria-labelledby`), and give
 * `getValueLabel` when a percentage is the wrong reading ("3 of 12 sources").
 */
export function Progress({ className, value, max = 100, size, tone, ...props }: ProgressProps) {
  const limit = Number.isFinite(max) && max > 0 ? max : 100;
  const known = value !== null && value !== undefined && Number.isFinite(value);
  const current = known ? Math.min(Math.max(value, 0), limit) : null;
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      value={current}
      max={limit}
      className={cn(trackVariants({ size }), className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className={cn(indicatorVariants({ tone }), current === null && 'w-2/5 animate-progress')}
        style={current === null ? undefined : { transform: `translateX(-${String(100 - (current / limit) * 100)}%)` }}
      />
    </ProgressPrimitive.Root>
  );
}
