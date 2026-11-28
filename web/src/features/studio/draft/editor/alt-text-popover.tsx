import type { Editor } from '@tiptap/core';
import { Captions } from 'lucide-react';
import { Toolbar } from 'radix-ui';
import { useState, type SyntheticEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import { toggleVariants } from '@/components/ui/toggle-group';
import { cn } from '@/lib/cn';

/** The longest alt text the editor accepts (one or two plain sentences). */
export const ALT_TEXT_MAX = 300;

export function altTextProblem(value: string): string | undefined {
  if (value === '') return 'Describe the image: alt text is required.';
  if (value.length > ALT_TEXT_MAX) return `Keep it under ${ALT_TEXT_MAX} characters.`;
  return undefined;
}

/** Edits the selected image's alt text (the dialect requires one on every image). */
export function AltTextPopover({ editor }: { editor: Editor }) {
  const [open, setOpen] = useState(false);
  const [alt, setAlt] = useState('');
  const [error, setError] = useState<string>();

  const save = (event: SyntheticEvent) => {
    event.preventDefault();
    const value = alt.replace(/\s+/g, ' ').trim();
    const problem = altTextProblem(value);
    if (problem) {
      setError(problem);
      return;
    }
    editor.chain().focus().updateAttributes('image', { alt: value }).run();
    setOpen(false);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          const current: unknown = editor.getAttributes('image').alt;
          setAlt(typeof current === 'string' ? current : '');
          setError(undefined);
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <Toolbar.Button
          onMouseDown={(event) => {
            event.preventDefault();
          }}
          className={cn(toggleVariants({ size: 'sm' }), 'shrink-0')}
        >
          <Captions aria-hidden="true" />
          Alt text
        </Toolbar.Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-80"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          editor.commands.focus();
        }}
      >
        <form className="grid gap-3" onSubmit={save} noValidate>
          <Field label="Alt text" description="What the image shows, for readers who can’t see it." error={error} required>
            <Textarea
              rows={3}
              value={alt}
              onChange={(event) => {
                setAlt(event.target.value);
                setError(undefined);
              }}
            />
          </Field>
          <Button type="submit" size="sm" className="justify-self-end">
            Save alt text
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
