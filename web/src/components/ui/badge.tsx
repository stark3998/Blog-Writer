import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export const badgeVariants = cva(
  'inline-flex w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded-sm px-2 py-0.5 text-xs font-medium [&_svg]:size-3',
  {
    variants: {
      variant: {
        neutral: 'bg-sunken text-ink-muted ring-1 ring-inset ring-line',
        outline: 'text-ink ring-1 ring-inset ring-line-strong',
        success: 'bg-success-soft text-success',
        warn: 'bg-warn-soft text-warn',
        danger: 'bg-danger-soft text-danger',
        /* Pillars. Always pair the colour with the pillar's name. */
        agents: 'bg-agents-soft text-agents',
        identity: 'bg-identity-soft text-identity',
        governance: 'bg-governance-soft text-governance',
      },
    },
    defaultVariants: { variant: 'neutral' },
  },
);

export interface BadgeProps extends ComponentProps<'span'>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}
