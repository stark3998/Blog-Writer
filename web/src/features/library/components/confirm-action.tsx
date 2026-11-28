import type { ReactElement, ReactNode } from 'react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';

export interface ConfirmActionProps {
  /** The button that asks (`<Button>Delete</Button>`). */
  children: ReactElement;
  /** The question: "Delete this sample?" */
  title: string;
  description: ReactNode;
  /** The answer that acts, in its own words: "Delete sample". */
  confirmLabel: string;
  /** The safe answer, in its own words: "Keep it". */
  cancelLabel?: string;
  onConfirm: () => void;
  destructive?: boolean;
}

/** A question before an action that cannot be taken back. Focus starts on the safe answer. */
export function ConfirmAction({
  children,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  destructive = true,
}: ConfirmActionProps) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>{children}</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction variant={destructive ? 'danger' : 'primary'} onClick={onConfirm}>
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
