import type { ComponentProps, ReactNode } from 'react';

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { cn } from '@/lib/cn';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';

interface ResponsiveDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * A dialog on desktop and a sheet from the bottom on phones, within thumb reach. Same content,
 * same focus trap and return; put a form with `DialogActions` inside.
 */
export function ResponsiveDialog({ open, onOpenChange, title, description, children, className }: ResponsiveDialogProps) {
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const describedBy = description === undefined ? { 'aria-describedby': undefined } : {};
  if (desktop) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className={cn('max-h-[calc(100dvh-2rem)] overflow-y-auto', className)} {...describedBy}>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description !== undefined ? <DialogDescription>{description}</DialogDescription> : null}
          </DialogHeader>
          {children}
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className={cn('overflow-y-auto', className)} {...describedBy}>
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          {description !== undefined ? <SheetDescription>{description}</SheetDescription> : null}
        </SheetHeader>
        {children}
      </SheetContent>
    </Sheet>
  );
}

/** A dialog's buttons: stacked full width on phones (main action nearest the thumb), in a row on desktop. */
export function DialogActions({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cn('flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end', className)} {...props} />;
}
