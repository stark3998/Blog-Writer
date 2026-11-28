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

/**
 * Asks before unsaved edits are left behind: another tab of the piece, another post, another
 * page, or closing the browser tab. Stay is the safe answer and has focus.
 */
export function LeaveGuard({ dirty }: { dirty: boolean }) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search),
  );

  useEffect(() => {
    if (!dirty) return undefined;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [dirty]);

  return (
    <AlertDialog
      open={blocker.state === 'blocked'}
      onOpenChange={(open) => {
        if (!open && blocker.state === 'blocked') blocker.reset();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Leave without saving?</AlertDialogTitle>
          <AlertDialogDescription>Your changes to this post aren’t saved yet. Leaving drops them.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Stay</AlertDialogCancel>
          <AlertDialogAction
            variant="danger"
            onClick={() => {
              if (blocker.state === 'blocked') blocker.proceed();
            }}
          >
            Leave without saving
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
