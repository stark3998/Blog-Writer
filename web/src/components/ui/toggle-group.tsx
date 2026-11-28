import { cva, type VariantProps } from 'class-variance-authority';
import { ToggleGroup as ToggleGroupPrimitive } from 'radix-ui';
import { createContext, use, type ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export const toggleVariants = cva(
  [
    'inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium',
    'text-ink-muted transition-colors duration-150 hover:bg-wash hover:text-ink',
    'disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
    // On is marked by weight and a stronger wash, not colour alone.
    'data-[state=on]:bg-wash-strong data-[state=on]:font-semibold data-[state=on]:text-ink',
  ],
  {
    variants: {
      variant: {
        ghost: '',
        outline: 'border border-line-strong bg-sheet',
      },
      size: {
        sm: 'h-8 px-2.5 pointer-coarse:h-10',
        md: 'h-10 px-3 pointer-coarse:h-11',
        icon: 'size-10 pointer-coarse:size-11',
      },
    },
    defaultVariants: { variant: 'ghost', size: 'md' },
  },
);

type ToggleStyle = VariantProps<typeof toggleVariants>;

const ToggleGroupStyle = createContext<ToggleStyle>({});

/**
 * Buttons that switch options on and off, such as editor marks (`type="multiple"`) or a filter
 * (`type="single"`). Arrow keys move between them; Space or Enter toggles. For one required
 * choice that is always set, use `SegmentedControl`. Name the group (`aria-label`); icon-only
 * items need their own `aria-label`.
 */
export function ToggleGroup({
  className,
  variant,
  size,
  children,
  ...props
}: ComponentProps<typeof ToggleGroupPrimitive.Root> & ToggleStyle) {
  return (
    <ToggleGroupPrimitive.Root
      data-slot="toggle-group"
      className={cn('flex w-fit flex-wrap items-center gap-1', className)}
      {...props}
    >
      <ToggleGroupStyle value={{ variant, size }}>{children}</ToggleGroupStyle>
    </ToggleGroupPrimitive.Root>
  );
}

export function ToggleGroupItem({
  className,
  variant,
  size,
  ...props
}: ComponentProps<typeof ToggleGroupPrimitive.Item> & ToggleStyle) {
  const group = use(ToggleGroupStyle);
  return (
    <ToggleGroupPrimitive.Item
      data-slot="toggle-group-item"
      className={cn(toggleVariants({ variant: variant ?? group.variant, size: size ?? group.size }), className)}
      {...props}
    />
  );
}
