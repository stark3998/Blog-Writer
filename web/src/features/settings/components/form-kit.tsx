import { CircleCheck, CircleDot, WifiOff } from 'lucide-react';
import { useCallback, useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import type { Versioned } from '@/lib/api/envelope';
import { cn } from '@/lib/cn';

/**
 * Pieces every Settings form shares: an editable draft of a versioned document that remembers
 * the version it started from (so a save sends that etag, and a change made elsewhere in the
 * meantime is a 412 and the conflict dialog, never a silent overwrite), and the save bar.
 */

export interface VersionedDraft<T, D> {
  /** The version the draft started from: its etag goes in `If-Match` (`null`: `If-None-Match: *`). */
  readonly base: Versioned<T>;
  readonly draft: D;
  readonly setDraft: (update: D | ((current: D) => D)) => void;
  /** Starts over from `next` (after a save, or "Take theirs"). */
  readonly reset: (next: Versioned<T>) => void;
  /** True when the draft differs from what it started from. */
  readonly dirty: boolean;
}

/**
 * A draft of `source.item` made by `toDraft`. `toDraft` must be a stable function (declare it at
 * module level). The draft does not follow later reads of the document: what the owner typed
 * stays until they save, discard or take the other version.
 */
export function useVersionedDraft<T, D>(source: Versioned<T>, toDraft: (item: T) => D): VersionedDraft<T, D> {
  const [state, setState] = useState(() => ({ base: source, draft: toDraft(source.item) }));
  const setDraft = useCallback((update: D | ((current: D) => D)) => {
    setState((current) => ({
      ...current,
      draft: typeof update === 'function' ? (update as (value: D) => D)(current.draft) : update,
    }));
  }, []);
  const reset = useCallback(
    (next: Versioned<T>) => {
      setState({ base: next, draft: toDraft(next.item) });
    },
    [toDraft],
  );
  const dirty = JSON.stringify(state.draft) !== JSON.stringify(toDraft(state.base.item));
  return { base: state.base, draft: state.draft, setDraft, reset, dirty };
}

interface SaveBarProps {
  dirty: boolean;
  saving: boolean;
  /** The save waits for the connection (TanStack pauses writes while offline). */
  paused?: boolean;
  /** The submit button's words, in the action's terms ("Save the blocklist"). */
  saveLabel: string;
  onDiscard: () => void;
  /**
   * Keeps the bar in reach at the bottom of a long form while there are changes (above the phone's
   * tab bar), so the main action is always a thumb away.
   */
  sticky?: boolean;
  /** Shown when there is nothing to save ("Saved", "Not saved yet: the defaults apply"). */
  idleNote?: ReactNode;
  /** Lets the first save of a never-saved document go through without changes (saving the defaults). */
  allowClean?: boolean;
  className?: string;
}

/** The form's save and discard buttons, with a short status of what is unsaved. */
export function SaveBar({
  dirty,
  saving,
  paused = false,
  saveLabel,
  onDiscard,
  sticky = false,
  idleNote,
  allowClean = false,
  className,
}: SaveBarProps) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center justify-between gap-3',
        sticky &&
          dirty &&
          'sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 -mx-3 rounded-lg border border-line bg-raised px-3 py-3 shadow-overlay md:bottom-4',
        className,
      )}
    >
      <p className="flex items-center gap-2 text-sm text-ink-muted" aria-live="polite">
        {paused ? (
          <>
            <WifiOff aria-hidden="true" className="size-4 text-warn" />
            Offline: it saves when the connection is back
          </>
        ) : dirty ? (
          <>
            <CircleDot aria-hidden="true" className="size-4 text-warn" />
            Unsaved changes
          </>
        ) : (
          (idleNote ?? (
            <>
              <CircleCheck aria-hidden="true" className="size-4 text-success" />
              Saved
            </>
          ))
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        {dirty ? (
          <Button variant="ghost" disabled={saving} onClick={onDiscard}>
            Discard changes
          </Button>
        ) : null}
        <Button type="submit" disabled={saving || (!dirty && !allowClean)}>
          {paused ? 'Waiting for a connection…' : saving ? 'Saving…' : saveLabel}
        </Button>
      </div>
    </div>
  );
}

/** Field errors of a 422 `validation_failed`, plus the client's own checks, by request path. */
export type FieldErrorMap = Readonly<Record<string, string>>;

/** The first error at `path` or under it (`postSlots.2` also finds `postSlots.2.day`). */
export function errorAt(errors: FieldErrorMap, path: string): string | undefined {
  if (errors[path] !== undefined) return errors[path];
  const prefix = `${path}.`;
  const nested = Object.keys(errors).find((key) => key.startsWith(prefix));
  return nested === undefined ? undefined : errors[nested];
}
