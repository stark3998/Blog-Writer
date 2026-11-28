import { Collapsible as CollapsiblePrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * One section that opens and closes ("Show the 12 unchanged sources"). The trigger is a button
 * with `aria-expanded`; put the trigger's words in terms of what it reveals. For a list of such
 * sections use `Accordion`.
 */
export const Collapsible = CollapsiblePrimitive.Root;
export const CollapsibleTrigger = CollapsiblePrimitive.Trigger;

/** Grows open and shrinks closed (the motion answers the click; reduced motion turns it off). */
export function CollapsibleContent({ className, ...props }: ComponentProps<typeof CollapsiblePrimitive.Content>) {
  return (
    <CollapsiblePrimitive.Content
      data-slot="collapsible-content"
      className={cn('overflow-hidden data-[state=closed]:animate-collapse data-[state=open]:animate-expand', className)}
      {...props}
    />
  );
}
