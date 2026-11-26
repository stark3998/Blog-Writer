import { Tooltip as TooltipPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export function TooltipProvider({ delayDuration = 300, ...props }: ComponentProps<typeof TooltipPrimitive.Provider>) {
  return <TooltipPrimitive.Provider delayDuration={delayDuration} {...props} />;
}

export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export function TooltipContent({ className, sideOffset = 8, ...props }: ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        data-slot="tooltip-content"
        sideOffset={sideOffset}
        className={cn(
          'z-50 rounded-sm bg-ink px-2 py-1 text-xs font-medium text-ink-inverse',
          'origin-(--radix-tooltip-content-transform-origin) data-[state=delayed-open]:animate-fade-in',
          className,
        )}
        {...props}
      />
    </TooltipPrimitive.Portal>
  );
}
