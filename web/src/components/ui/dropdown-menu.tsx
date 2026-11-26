import { CheckIcon } from 'lucide-react';
import { DropdownMenu as MenuPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';

export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;
export const DropdownMenuGroup = MenuPrimitive.Group;
export const DropdownMenuRadioGroup = MenuPrimitive.RadioGroup;

export function DropdownMenuContent({
  className,
  sideOffset = 6,
  ...props
}: ComponentProps<typeof MenuPrimitive.Content>) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        className={cn(
          'z-50 max-h-(--radix-dropdown-menu-content-available-height) min-w-48 overflow-y-auto',
          'rounded-lg border border-line bg-raised p-1 text-ink shadow-overlay',
          'origin-(--radix-dropdown-menu-content-transform-origin) data-[state=open]:animate-pop-in',
          className,
        )}
        {...props}
      />
    </MenuPrimitive.Portal>
  );
}

const itemClass = cn(
  'relative flex cursor-default select-none items-center gap-2.5 rounded-md px-2.5 py-2 text-sm outline-none',
  'data-[highlighted]:bg-wash-strong data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
  'pointer-coarse:py-3 [&_svg]:size-4 [&_svg]:shrink-0',
);

interface DropdownMenuItemProps extends ComponentProps<typeof MenuPrimitive.Item> {
  variant?: 'default' | 'danger';
}

export function DropdownMenuItem({ className, variant = 'default', ...props }: DropdownMenuItemProps) {
  return (
    <MenuPrimitive.Item
      data-slot="dropdown-menu-item"
      data-variant={variant}
      className={cn(itemClass, variant === 'danger' && 'text-danger', className)}
      {...props}
    />
  );
}

export function DropdownMenuRadioItem({
  className,
  children,
  ...props
}: ComponentProps<typeof MenuPrimitive.RadioItem>) {
  return (
    <MenuPrimitive.RadioItem
      data-slot="dropdown-menu-radio-item"
      className={cn(itemClass, 'pr-8', className)}
      {...props}
    >
      {children}
      <span className="absolute right-2.5 flex size-4 items-center justify-center">
        <MenuPrimitive.ItemIndicator>
          <CheckIcon aria-hidden="true" />
        </MenuPrimitive.ItemIndicator>
      </span>
    </MenuPrimitive.RadioItem>
  );
}

export function DropdownMenuLabel({ className, ...props }: ComponentProps<typeof MenuPrimitive.Label>) {
  return (
    <MenuPrimitive.Label
      data-slot="dropdown-menu-label"
      className={cn('px-2.5 pb-1 pt-2 text-xs font-semibold text-ink-subtle', className)}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({ className, ...props }: ComponentProps<typeof MenuPrimitive.Separator>) {
  return (
    <MenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn('-mx-1 my-1 h-px bg-line', className)}
      {...props}
    />
  );
}

export function DropdownMenuShortcut({ className, ...props }: ComponentProps<'span'>) {
  return <span data-slot="dropdown-menu-shortcut" className={cn('ml-auto text-xs text-ink-subtle', className)} {...props} />;
}
