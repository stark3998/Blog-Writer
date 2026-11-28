import { Undo2, WifiOff } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useOnline } from '@/lib/use-online';
import { cn } from '@/lib/cn';

export interface SaveBarProps {
  /** True when the draft differs from the saved version. */
  dirty: boolean;
  saving: boolean;
  /** The save action's words: "Save pillars". */
  saveLabel: string;
  onSave: () => void;
  onDiscard: () => void;
  /** "2 fields need a fix." when the draft cannot be saved as it is. */
  blockedReason?: string | undefined;
  className?: string;
}

/**
 * Save and discard for a settings form. It stays in reach while the owner scrolls a long form:
 * stuck to the bottom of the screen, above the phone's tab bar. It appears once there is
 * something to save; a stable status line tells screen readers. Offline, the changes stay on
 * the page and Save waits for the connection.
 */
export function SaveBar({ dirty, saving, saveLabel, onSave, onDiscard, blockedReason, className }: SaveBarProps) {
  const online = useOnline();
  const visible = dirty || saving;
  const message = saving
    ? 'Saving…'
    : !visible
      ? ''
      : !online
        ? 'You’re offline. Your changes stay on this page; save them once you’re back online.'
        : (blockedReason ?? 'You have unsaved changes.');
  return (
    <>
      <p role="status" className="sr-only">
        {message}
      </p>
      {visible ? (
        <div
          data-slot="save-bar"
          className={cn('sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-10 animate-fade-in md:bottom-2', className)}
        >
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line-strong bg-raised px-4 py-3 shadow-overlay">
            <p
              aria-hidden="true"
              className={cn('flex min-w-0 flex-1 items-start gap-2 text-sm', blockedReason && online ? 'text-danger' : 'text-ink-muted')}
            >
              {online ? null : <WifiOff aria-hidden="true" className="mt-0.5 size-4 shrink-0" />}
              {message}
            </p>
            <div className="flex gap-2">
              <Button variant="ghost" disabled={saving} onClick={onDiscard}>
                <Undo2 aria-hidden="true" />
                Discard
              </Button>
              <Button disabled={saving || !online} onClick={onSave}>
                {saving ? 'Saving…' : saveLabel}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
