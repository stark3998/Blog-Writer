import { useEffect } from 'react';
import { useBlocker } from 'react-router';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

export interface UnsavedChangesGuardProps {
  /** True while there are changes that a navigation would lose. */
  when: boolean;
  /** What would be lost, for the question: "your pillars". */
  subject: string;
}

/**
 * Asks before a navigation (another Library tab, another area) or closing the tab drops unsaved
 * settings. One per section: React Router allows one active blocker at a time.
 */
export function UnsavedChangesGuard({ when, subject }: UnsavedChangesGuardProps) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => when && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!when) return undefined;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [when]);

  const blocked = blocker.state === 'blocked';
  return (
    <AlertDialog
      open={blocked}
      onOpenChange={(open) => {
        if (!open && blocker.state === 'blocked') blocker.reset();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Leave without saving?</AlertDialogTitle>
          <AlertDialogDescription>{`Your changes to ${subject} aren’t saved yet. Leaving discards them.`}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel
            onClick={() => {
              if (blocker.state === 'blocked') blocker.reset();
            }}
          >
            Keep editing
          </AlertDialogCancel>
          <AlertDialogAction
            variant="danger"
            onClick={() => {
              if (blocker.state === 'blocked') blocker.proceed();
            }}
          >
            Discard changes
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
