import { cva, type VariantProps } from 'class-variance-authority';
import { XIcon } from 'lucide-react';
import { Dialog as SheetPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export const Sheet = SheetPrimitive.Root;
export const SheetTrigger = SheetPrimitive.Trigger;
export const SheetClose = SheetPrimitive.Close;

const sheetVariants = cva(
  'fixed z-50 flex flex-col gap-4 bg-raised p-6 text-ink shadow-overlay outline-none',
  {
    variants: {
      side: {
        left: 'inset-y-0 left-0 h-full w-[min(24rem,88vw)] border-r border-line data-[state=open]:animate-slide-in-left',
        right:
          'inset-y-0 right-0 h-full w-[min(24rem,88vw)] border-l border-line data-[state=open]:animate-slide-in-right',
        top: 'inset-x-0 top-0 max-h-[85dvh] border-b border-line data-[state=open]:animate-slide-in-top',
        bottom: [
          'inset-x-0 bottom-0 max-h-[85dvh] rounded-t-xl border-t border-line',
          'pb-[calc(env(safe-area-inset-bottom)+1.5rem)] data-[state=open]:animate-slide-in-bottom',
        ],
      },
    },
    defaultVariants: { side: 'right' },
  },
);

interface SheetContentProps
  extends ComponentProps<typeof SheetPrimitive.Content>,
    VariantProps<typeof sheetVariants> {}

/** A panel that slides in from an edge. On phones, prefer side="bottom" so it sits under the thumb. */
export function SheetContent({ side, className, children, ...props }: SheetContentProps) {
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay
        data-slot="sheet-overlay"
        className="fixed inset-0 z-50 bg-backdrop data-[state=open]:animate-fade-in"
      />
      <SheetPrimitive.Content data-slot="sheet-content" className={cn(sheetVariants({ side }), className)} {...props}>
        {children}
        <SheetPrimitive.Close
          className={cn(
            'absolute right-3 top-3 inline-flex size-9 items-center justify-center rounded-md text-ink-muted',
            'hover:bg-wash hover:text-ink pointer-coarse:size-11',
          )}
        >
          <XIcon className="size-4" aria-hidden="true" />
          <span className="sr-only">Close</span>
        </SheetPrimitive.Close>
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}

export function SheetHeader({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="sheet-header" className={cn('flex flex-col gap-1.5 pr-8', className)} {...props} />;
}

export function SheetFooter({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="sheet-footer" className={cn('mt-auto flex flex-col gap-2', className)} {...props} />;
}

export function SheetTitle({ className, ...props }: ComponentProps<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title data-slot="sheet-title" className={cn('font-serif text-2xl font-medium', className)} {...props} />
  );
}

export function SheetDescription({ className, ...props }: ComponentProps<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      data-slot="sheet-description"
      className={cn('text-sm text-ink-muted', className)}
      {...props}
    />
  );
}
