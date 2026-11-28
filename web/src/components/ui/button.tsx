import { cva, type VariantProps } from 'class-variance-authority';
import { Slot } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export const buttonVariants = cva(
  [
    'inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium',
    'transition-colors duration-150 ease-standard',
    'disabled:pointer-events-none disabled:opacity-50',
    '[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*=size-])]:size-4',
  ],
  {
    variants: {
      variant: {
        /** The one action that matters on a screen. Solid ink, no gradients. */
        primary: 'bg-ink text-ink-inverse hover:bg-ink/85',
        secondary: 'border border-line-strong bg-sheet text-ink hover:bg-wash',
        ghost: 'text-ink-muted hover:bg-wash hover:text-ink',
        danger: 'bg-danger text-ink-inverse hover:bg-danger/90',
        link: 'text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink',
      },
      size: {
        sm: 'h-8 px-3 text-sm pointer-coarse:h-10',
        md: 'h-10 px-4 text-sm pointer-coarse:h-11',
        lg: 'h-12 px-5 text-base',
        icon: 'size-10 pointer-coarse:size-11',
        'icon-sm': 'size-8 pointer-coarse:size-10',
      },
    },
    compoundVariants: [{ variant: 'link', className: 'h-auto px-0 pointer-coarse:h-auto' }],
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

export interface ButtonProps extends ComponentProps<'button'>, VariantProps<typeof buttonVariants> {
  /** Render the child element (for example a router link) with button styling. */
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, type, ...props }: ButtonProps) {
  const Comp = asChild ? Slot.Root : 'button';
  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size }), className)}
      type={asChild ? undefined : (type ?? 'button')}
      {...props}
    />
  );
}
