import { useState, type ReactNode, type RefObject } from 'react';

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';

import { ActionProblem } from './action-problem';
import { useReturnFocus } from './return-focus';

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  /** The action's name, on its button ("Unpublish"). */
  confirmLabel: string;
  /** While it runs ("Unpublishing…"). */
  pendingLabel: string;
  /** The safe answer ("Keep it live"). */
  cancelLabel?: string;
  variant?: 'primary' | 'danger';
  /** Extra fields (a correction note). */
  children?: ReactNode;
  /** Disables the action until the fields are complete. */
  ready?: boolean;
  /** Runs the action; a rejection is shown in the dialog, which stays open. */
  onConfirm: () => Promise<unknown>;
  /** Where focus goes when the button that opened it is gone. */
  returnFocus?: RefObject<HTMLElement | null>;
}

/**
 * A question before an action that changes where the article stands. Focus starts on the safe
 * answer; a refusal is explained in the dialog instead of a toast, so the owner can act on it.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  pendingLabel,
  cancelLabel = 'Cancel',
  variant = 'primary',
  children,
  ready = true,
  onConfirm,
  returnFocus,
}: ConfirmDialogProps) {
  const focus = useReturnFocus(returnFocus);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();

  const confirm = async () => {
    setPending(true);
    setError(undefined);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (problem) {
      setError(problem);
    } finally {
      setPending(false);
    }
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setError(undefined);
        if (!pending) onOpenChange(next);
      }}
    >
      <AlertDialogContent {...focus}>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">{description}</div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        {error ? <ActionProblem error={error} title={`Couldn’t ${confirmLabel.toLowerCase()}.`} /> : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{cancelLabel}</AlertDialogCancel>
          <Button variant={variant} disabled={pending || !ready} onClick={() => void confirm()}>
            {pending ? pendingLabel : confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
