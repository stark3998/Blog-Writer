import type { VariantProps } from 'class-variance-authority';
import { AlertDialog as AlertDialogPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

import { buttonVariants } from './button';

/**
 * A question that needs an answer before anything else ("Unpublish this article?"). Unlike
 * `Dialog` a click outside does not close it, and focus starts on Cancel, the safe answer. Name
 * the actions by what they do ("Unpublish", "Keep it live"), not "OK".
 */
export const AlertDialog = AlertDialogPrimitive.Root;
export const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
export const AlertDialogPortal = AlertDialogPrimitive.Portal;

export function AlertDialogOverlay({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Overlay>) {
  return (
    <AlertDialogPrimitive.Overlay
      data-slot="alert-dialog-overlay"
      className={cn('fixed inset-0 z-50 bg-backdrop data-[state=open]:animate-fade-in', className)}
      {...props}
    />
  );
}

export function AlertDialogContent({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Content>) {
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content
        data-slot="alert-dialog-content"
        className={cn(
          'fixed left-1/2 top-1/2 z-50 grid max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2',
          'gap-4 overflow-y-auto rounded-lg border border-line bg-raised p-6 text-ink shadow-overlay outline-none',
          'data-[state=open]:animate-pop-in',
          className,
        )}
        {...props}
      />
    </AlertDialogPortal>
  );
}

export function AlertDialogHeader({ className, ...props }: ComponentProps<'div'>) {
  return <div data-slot="alert-dialog-header" className={cn('flex flex-col gap-1.5', className)} {...props} />;
}

/** Actions: side by side on wide screens, stacked full-width on phones with the main one nearest the thumb. */
export function AlertDialogFooter({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="alert-dialog-footer"
      className={cn('flex flex-col-reverse gap-2 sm:flex-row sm:justify-end', className)}
      {...props}
    />
  );
}

export function AlertDialogTitle({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Title>) {
  return (
    <AlertDialogPrimitive.Title
      data-slot="alert-dialog-title"
      className={cn('font-serif text-2xl font-medium', className)}
      {...props}
    />
  );
}

export function AlertDialogDescription({
  className,
  ...props
}: ComponentProps<typeof AlertDialogPrimitive.Description>) {
  return (
    <AlertDialogPrimitive.Description
      data-slot="alert-dialog-description"
      className={cn('text-sm text-ink-muted', className)}
      {...props}
    />
  );
}

type ButtonVariant = VariantProps<typeof buttonVariants>['variant'];

/** The answer that acts. `variant="danger"` when it destroys something. */
export function AlertDialogAction({
  className,
  variant = 'primary',
  ...props
}: ComponentProps<typeof AlertDialogPrimitive.Action> & { variant?: ButtonVariant }) {
  return (
    <AlertDialogPrimitive.Action
      data-slot="alert-dialog-action"
      className={cn(buttonVariants({ variant }), className)}
      {...props}
    />
  );
}

/** The safe answer. It has focus when the dialog opens. */
export function AlertDialogCancel({
  className,
  variant = 'secondary',
  ...props
}: ComponentProps<typeof AlertDialogPrimitive.Cancel> & { variant?: ButtonVariant }) {
  return (
    <AlertDialogPrimitive.Cancel
      data-slot="alert-dialog-cancel"
      className={cn(buttonVariants({ variant }), className)}
      {...props}
    />
  );
}
