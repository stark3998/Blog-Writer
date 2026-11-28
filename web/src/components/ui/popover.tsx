import { Popover as PopoverPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/**
 * A small panel anchored to its trigger, for content and controls that belong to it (the
 * selection-AI proposal, a quick filter). It is not a menu (use `DropdownMenu`) and not a tooltip.
 * Escape and a click outside close it; focus goes back to the trigger.
 */
export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
/** Positions the panel against something other than the trigger (a text selection's box). */
export const PopoverAnchor = PopoverPrimitive.Anchor;
export const PopoverClose = PopoverPrimitive.Close;

export function PopoverContent({
  className,
  align = 'center',
  sideOffset = 6,
  collisionPadding = 12,
  ...props
}: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        data-slot="popover-content"
        align={align}
        sideOffset={sideOffset}
        collisionPadding={collisionPadding}
        className={cn(
          'z-50 w-72 max-w-[calc(100vw-1.5rem)] rounded-lg border border-line bg-raised p-4 text-ink shadow-overlay',
          'outline-none origin-(--radix-popover-content-transform-origin) data-[state=open]:animate-pop-in',
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}
