import type { Editor } from '@tiptap/core';
import { Unlink } from 'lucide-react';
import { useEffect, useEffectEvent, useState, type SyntheticEvent, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

import type { EditorBus } from './bus';

/**
 * Link targets the dialect allows (dialect section 3): `https:`, `mailto:`, a `#fragment` or a
 * site path (`/blog/…`, not `//host`). Returns what is wrong, or `undefined`.
 */
export function linkProblem(value: string): string | undefined {
  if (value === '') return 'Enter an address.';
  if (/\s/.test(value)) return 'Addresses can’t contain spaces.';
  if (value.startsWith('#')) return value.length > 1 ? undefined : 'Name the section after the #.';
  if (value.startsWith('/')) return value.startsWith('//') ? 'Use a site path like /blog/my-article.' : undefined;
  if (/^mailto:[^@\s]+@[^@\s]+$/i.test(value)) return undefined;
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' && url.hostname !== '') return undefined;
  } catch {
    // Not a URL: explained below.
  }
  return 'Use a secure web address (https), a mailto: address, a #section link or a site path like /blog/….';
}

export interface LinkPopoverProps {
  editor: Editor;
  bus: EditorBus;
  /** The toolbar button that opens it. */
  children: ReactNode;
}

/** Adds, changes or removes the link on the selection (Mod-K opens it too). */
export function LinkPopover({ editor, bus, children }: LinkPopoverProps) {
  const [open, setOpen] = useState(false);
  const [href, setHref] = useState('');
  const [error, setError] = useState<string>();
  const [linked, setLinked] = useState(false);

  const prepare = () => {
    const current: unknown = editor.getAttributes('link').href;
    setHref(typeof current === 'string' ? current : '');
    setLinked(editor.isActive('link'));
    setError(undefined);
  };

  const openFromShortcut = useEffectEvent(() => {
    prepare();
    setOpen(true);
  });
  useEffect(
    () =>
      bus.on('link', () => {
        openFromShortcut();
      }),
    [bus],
  );

  const apply = (event: SyntheticEvent) => {
    event.preventDefault();
    const value = href.trim();
    const problem = linkProblem(value);
    if (problem) {
      setError(problem);
      return;
    }
    if (editor.state.selection.empty && !editor.isActive('link')) {
      editor
        .chain()
        .focus()
        .insertContent({ type: 'text', text: value, marks: [{ type: 'link', attrs: { href: value } }] })
        .run();
    } else {
      editor.chain().focus().extendMarkRange('link').setLink({ href: value }).run();
    }
    setOpen(false);
  };

  const remove = () => {
    editor.chain().focus().extendMarkRange('link').unsetLink().run();
    setOpen(false);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) prepare();
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-80"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          editor.commands.focus();
        }}
      >
        <form className="grid gap-3" onSubmit={apply} noValidate>
          <Field
            label="Link address"
            description="A secure web address (https), an email (mailto:), a #section or a /site path."
            error={error}
          >
            <Input
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={href}
              onChange={(event) => {
                setHref(event.target.value);
                setError(undefined);
              }}
            />
          </Field>
          <div className="flex flex-wrap justify-end gap-2">
            {linked ? (
              <Button type="button" size="sm" variant="ghost" className="mr-auto" onClick={remove}>
                <Unlink aria-hidden="true" />
                Remove link
              </Button>
            ) : null}
            <Button type="submit" size="sm">
              {linked ? 'Update link' : 'Add link'}
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  );
}
