import { Command as CommandPrimitive } from 'cmdk';
import { SearchIcon } from 'lucide-react';
import { createContext, use, useRef, type ComponentProps, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

import { buttonVariants } from './button';
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from './dialog';

/** True inside a `CommandDialog`, so its input row offers a Close button. */
const InCommandDialog = createContext(false);

export function Command({ className, ...props }: ComponentProps<typeof CommandPrimitive>) {
  return (
    <CommandPrimitive
      data-slot="command"
      className={cn('flex size-full flex-col overflow-hidden bg-raised text-ink', className)}
      {...props}
    />
  );
}

interface CommandDialogProps extends ComponentProps<typeof Dialog> {
  title: string;
  /**
   * Lets the caller decide where focus goes when the dialog closes: call `preventDefault()` and
   * move it. Otherwise focus goes back to where it was when the dialog opened.
   */
  onCloseAutoFocus?: ComponentProps<typeof DialogContent>['onCloseAutoFocus'];
  description: string;
  children: ReactNode;
  /** Passed to cmdk; turn off to filter items yourself. */
  shouldFilter?: boolean;
  /** Accessible name for the command menu. */
  label?: string;
}

/**
 * A command menu in a dialog, opening near the top so results have room below.
 *
 * It is opened by state (a shortcut, a button), not by a `DialogTrigger`, and Radix returns focus
 * on close only to a trigger: without one, focus would drop to the page body. So the dialog
 * remembers what had focus when it opened and puts focus back there.
 */
export function CommandDialog({
  title,
  description,
  children,
  shouldFilter,
  label,
  onCloseAutoFocus,
  ...props
}: CommandDialogProps) {
  const opener = useRef<HTMLElement | null>(null);
  return (
    <Dialog {...props}>
      <DialogContent
        showCloseButton={false}
        onOpenAutoFocus={() => {
          // Radix asks before moving focus in, so this is still the element that opened it.
          opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }}
        onCloseAutoFocus={(event) => {
          onCloseAutoFocus?.(event);
          const target = opener.current;
          opener.current = null;
          if (event.defaultPrevented) return;
          event.preventDefault();
          if (target?.isConnected) target.focus({ preventScroll: true });
        }}
        className="top-[max(1rem,12vh)] max-w-xl translate-y-0 gap-0 overflow-hidden p-0"
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        <DialogDescription className="sr-only">{description}</DialogDescription>
        <InCommandDialog value>
          <Command label={label ?? title} shouldFilter={shouldFilter} loop>
            {children}
          </Command>
        </InCommandDialog>
      </DialogContent>
    </Dialog>
  );
}

export function CommandInput({ className, ...props }: ComponentProps<typeof CommandPrimitive.Input>) {
  const inDialog = use(InCommandDialog);
  return (
    <div
      data-slot="command-input-wrapper"
      className="flex items-center gap-3 border-b border-line px-4 focus-within:shadow-[inset_0_-2px_0_var(--color-focus)]"
    >
      <SearchIcon className="size-5 shrink-0 text-ink-subtle" aria-hidden="true" />
      <CommandPrimitive.Input
        data-slot="command-input"
        className={cn(
          'flex h-14 w-full bg-transparent text-base text-ink outline-none placeholder:text-ink-subtle',
          className,
        )}
        {...props}
      />
      {inDialog ? (
        // On phones and touch screens there is no Escape key, and a screen reader cannot reach
        // the backdrop (it is hidden from it), so the dialog needs a control to leave by.
        <DialogClose
          data-slot="command-dialog-close"
          className={cn(
            buttonVariants({ variant: 'ghost', size: 'sm' }),
            '-mr-2 hidden max-md:inline-flex pointer-coarse:inline-flex',
          )}
        >
          Close
        </DialogClose>
      ) : null}
    </div>
  );
}

export function CommandList({ className, ...props }: ComponentProps<typeof CommandPrimitive.List>) {
  return (
    <CommandPrimitive.List
      data-slot="command-list"
      className={cn('max-h-[min(60dvh,28rem)] scroll-py-2 overflow-y-auto overscroll-contain p-2', className)}
      {...props}
    />
  );
}

export function CommandEmpty({ className, ...props }: ComponentProps<typeof CommandPrimitive.Empty>) {
  return (
    <CommandPrimitive.Empty
      data-slot="command-empty"
      className={cn('px-4 py-10 text-center text-sm text-ink-muted', className)}
      {...props}
    />
  );
}

export function CommandGroup({ className, ...props }: ComponentProps<typeof CommandPrimitive.Group>) {
  return (
    <CommandPrimitive.Group
      data-slot="command-group"
      className={cn(
        'text-ink [&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:pt-3',
        '[&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-ink-subtle',
        className,
      )}
      {...props}
    />
  );
}

export function CommandItem({ className, ...props }: ComponentProps<typeof CommandPrimitive.Item>) {
  return (
    <CommandPrimitive.Item
      data-slot="command-item"
      className={cn(
        'relative flex cursor-default select-none items-center gap-3 rounded-md px-2.5 py-2.5 text-sm outline-none',
        'data-[selected=true]:bg-wash-strong data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50',
        '[&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-ink-subtle',
        className,
      )}
      {...props}
    />
  );
}

export function CommandSeparator({ className, ...props }: ComponentProps<typeof CommandPrimitive.Separator>) {
  return (
    <CommandPrimitive.Separator
      data-slot="command-separator"
      className={cn('-mx-2 my-1 h-px bg-line', className)}
      {...props}
    />
  );
}

export function CommandShortcut({ className, ...props }: ComponentProps<'span'>) {
  return <span data-slot="command-shortcut" className={cn('ml-auto text-xs text-ink-subtle', className)} {...props} />;
}
