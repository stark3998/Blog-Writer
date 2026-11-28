import { Tabs as TabsPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export function Tabs({ className, ...props }: ComponentProps<typeof TabsPrimitive.Root>) {
  return <TabsPrimitive.Root data-slot="tabs" className={cn('flex flex-col gap-8', className)} {...props} />;
}

/** Underlined tabs on a hairline. Scrolls sideways on narrow screens instead of wrapping. */
export function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        'relative flex w-full gap-6 overflow-x-auto px-0.5 shadow-[inset_0_-1px_0_var(--color-line)] [scrollbar-width:none]',
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        'inline-flex h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 border-transparent',
        'text-sm font-medium text-ink-muted transition-colors duration-150 hover:text-ink',
        'focus-visible:outline-offset-[-2px] data-[state=active]:border-ink data-[state=active]:text-ink',
        'disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4',
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content data-slot="tabs-content" className={cn('outline-offset-4', className)} {...props} />;
}
