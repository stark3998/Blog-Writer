import { Copy } from 'lucide-react';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { DiffView } from '@/components/ui/diff-view';
import { conflictOf, type ConflictInfo } from '@/lib/api/envelope';
import { toastSuccess } from '@/lib/api/toast';

const when = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

/** Who saved the version the owner's save collided with (`updatedBy`), as the start of a sentence. */
export function conflictAuthor(updatedBy: string | undefined): string {
  switch (updatedBy) {
    case 'owner':
      return 'You saved a newer version in another tab or on another device';
    case 'pipeline':
      return 'The pipeline saved a newer version';
    case 'system':
      return 'The app saved a newer version';
    case undefined:
    default:
      return 'A newer version was saved';
  }
}

/** "The pipeline saved a newer version at 26 Nov 2026, 15:04." */
export function describeConflict(conflict: ConflictInfo | undefined): string {
  const at = conflict?.updatedAt ? new Date(conflict.updatedAt) : undefined;
  const time = at && !Number.isNaN(at.getTime()) ? ` at ${when.format(at)}` : '';
  return `${conflictAuthor(conflict?.updatedBy)}${time}.`;
}

export interface ConflictDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** From the 412 (`conflictOf(error)`): who saved the current version, when, and its etag. */
  conflict: ConflictInfo | undefined;
  /** What changed, for the title: "This draft", "The pillars". */
  subject?: string;
  /** The owner's version and the saved one, as text (Markdown); with both, the dialog shows the difference. */
  mine?: string;
  theirs?: string;
  /** Save the owner's version over the current one, with the current etag (`conflict.etag`, or reload it first). */
  onKeepMine: () => void;
  /** Drop the owner's changes and load the current version. */
  onTakeTheirs: () => void;
  /** True while "Keep mine" saves; the buttons wait. */
  saving?: boolean;
}

/**
 * The answer to a 412 (contract 1.4): what happened, a diff of the owner's version against the
 * saved one, and three ways on: "Keep mine" (save over it), "Take theirs" (reload) and "Copy mine"
 * (to the clipboard, so nothing is lost either way). It never decides by itself, and a click
 * outside does not close it. Focus starts on "Copy mine", the one answer that loses nothing.
 */
export function ConflictDialog({
  open,
  onOpenChange,
  conflict,
  subject = 'This',
  mine,
  theirs,
  onKeepMine,
  onTakeTheirs,
  saving = false,
}: ConflictDialogProps) {
  const copyMine = useCallback(async () => {
    if (mine === undefined) return;
    try {
      await navigator.clipboard.writeText(mine);
      toastSuccess('Copied your version.');
    } catch {
      toast.error('Couldn’t copy your version.', { description: 'Select the text in the editor and copy it yourself.' });
    }
  }, [mine]);

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent
        className="max-w-2xl"
        onOpenAutoFocus={(event) => {
          // Radix would focus a Cancel button; there is none here, so pick the safe answer.
          event.preventDefault();
          const content = event.currentTarget as HTMLElement | null;
          const target = content?.querySelector<HTMLElement>('[data-conflict-action="copy"]') ?? content;
          target?.focus({ preventScroll: true });
        }}
      >
        <AlertDialogHeader>
          <AlertDialogTitle>{`${subject} changed since you opened it`}</AlertDialogTitle>
          <AlertDialogDescription>
            {`${describeConflict(conflict)} Keep mine saves your version over it. Take theirs loads the saved version and drops your changes.`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {mine !== undefined && theirs !== undefined ? (
          <DiffView
            before={theirs}
            after={mine}
            context={1}
            label="Your version compared with the saved one"
            tabIndex={0}
            className="max-h-[40dvh] overflow-y-auto rounded-md border border-line bg-sheet p-3"
          />
        ) : null}
        <AlertDialogFooter>
          {mine !== undefined ? (
            <Button variant="ghost" data-conflict-action="copy" className="sm:mr-auto" onClick={() => void copyMine()}>
              <Copy aria-hidden="true" />
              Copy mine
            </Button>
          ) : null}
          <Button variant="secondary" disabled={saving} onClick={onTakeTheirs}>
            Take theirs
          </Button>
          <Button disabled={saving} onClick={onKeepMine}>
            {saving ? 'Saving…' : 'Keep mine'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export interface ConflictState {
  /** True while the dialog should show. */
  readonly open: boolean;
  /** What the 412 said; `undefined` until one happened. */
  readonly conflict: ConflictInfo | undefined;
  /** Opens the dialog for a 412 and returns true; returns false (and does nothing) for other errors. */
  readonly show: (error: unknown) => boolean;
  readonly close: () => void;
  readonly onOpenChange: (open: boolean) => void;
}

/**
 * State for one `ConflictDialog`. Call `show(error)` from a write's `onError`: it opens the dialog
 * for a 412 and tells you whether it did.
 *
 * ```tsx
 * const conflict = useConflict();
 * save.mutate(vars, { onError: (error) => { conflict.show(error); } });
 * <ConflictDialog {...conflict} subject="This draft" onKeepMine={…} onTakeTheirs={…} />
 * ```
 */
export function useConflict(): ConflictState {
  const [state, setState] = useState<{ open: boolean; conflict: ConflictInfo | undefined }>({
    open: false,
    conflict: undefined,
  });
  const show = useCallback((error: unknown) => {
    const conflict = conflictOf(error);
    if (conflict === undefined) return false;
    setState({ open: true, conflict });
    return true;
  }, []);
  const close = useCallback(() => {
    setState((current) => ({ ...current, open: false }));
  }, []);
  const onOpenChange = useCallback((open: boolean) => {
    setState((current) => ({ ...current, open }));
  }, []);
  return { open: state.open, conflict: state.conflict, show, close, onOpenChange };
}
