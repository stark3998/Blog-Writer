import { CircleAlert, Plus, X } from 'lucide-react';
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/cn';

import { fold } from '../lib/rules';

export interface StringListEditorProps {
  /** Names the list ("Vocabulary"). */
  legend: string;
  description?: ReactNode;
  items: readonly string[];
  onChange: (items: string[]) => void;
  /** The add field's label ("Add a term"). */
  addLabel: string;
  placeholder?: string;
  /** Turns what was typed into the stored value (strip `#`, keep a URL's host). */
  normalize?: (input: string) => string;
  /** Why a value cannot be added; duplicates and the limits are checked already. */
  validate?: (value: string) => string | undefined;
  /** How many items the list may hold. */
  max?: number;
  /** Characters per item. */
  maxLength?: number;
  /** Splits pasted text on commas and new lines into several items. */
  splitPaste?: boolean;
  /** How an item reads on its chip ("#Agents"). */
  display?: (item: string) => string;
  /** Errors the server or the form found per item, by index. */
  itemErrors?: Readonly<Record<number, string>>;
  /** An error about the whole list. */
  error?: string | undefined;
  /** What an item is called in the remove button's name ("term"). */
  noun: string;
  className?: string;
}

/**
 * A short list of words or names (vocabulary, hashtags, sites, required parts): each item a chip
 * with its own remove button, and a field to add more. Enter adds; a pasted list adds every item.
 * After a removal, focus moves to the next chip, so a keyboard user can keep going.
 */
export function StringListEditor({
  legend,
  description,
  items,
  onChange,
  addLabel,
  placeholder,
  normalize = (input) => input.trim(),
  validate,
  max,
  maxLength,
  splitPaste = false,
  display = (item) => item,
  itemErrors = {},
  error,
  noun,
  className,
}: StringListEditorProps) {
  const [input, setInput] = useState('');
  const [inputError, setInputError] = useState<string | undefined>();
  const listRef = useRef<HTMLUListElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const errorsId = useId();
  const full = max !== undefined && items.length >= max;

  const problemFor = (value: string, existing: readonly string[]): string | undefined => {
    if (value === '') return `Type a ${noun} first.`;
    if (maxLength !== undefined && value.length > maxLength) return `At most ${String(maxLength)} characters.`;
    if (existing.some((item) => fold(item) === fold(value))) return `${display(value)} is on the list already.`;
    return validate?.(value);
  };

  const add = (raw: string) => {
    const parts = splitPaste ? raw.split(/[\n,;]+/) : [raw];
    const next = [...items];
    for (const part of parts) {
      const value = normalize(part);
      if (parts.length > 1 && value === '') continue;
      if (max !== undefined && next.length >= max) {
        setInputError(`The list holds at most ${String(max)}.`);
        onChange(next);
        return;
      }
      const problem = problemFor(value, next);
      if (problem) {
        setInputError(problem);
        if (next.length !== items.length) onChange(next);
        return;
      }
      next.push(value);
    }
    setInputError(undefined);
    setInput('');
    onChange(next);
  };

  const remove = (index: number) => {
    onChange(items.filter((_, position) => position !== index));
    // Focus the chip that takes this one's place, else the one before, else the field.
    requestAnimationFrame(() => {
      const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>('button[data-remove]');
      const target = buttons?.[Math.min(index, buttons.length - 1)] ?? inputRef.current;
      target?.focus();
    });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      add(input);
    }
  };

  const errorEntries = Object.entries(itemErrors)
    .map(([index, message]) => [Number(index), message] as const)
    .filter(([index]) => index < items.length);

  return (
    <Fieldset legend={legend} description={description} error={error} className={className}>
      {items.length > 0 ? (
        <ul ref={listRef} className="flex flex-wrap gap-2">
          {items.map((item, index) => {
            const itemError = index in itemErrors ? itemErrors[index] : undefined;
            return (
              <li
                key={`${item}-${String(index)}`}
                className={cn(
                  'inline-flex max-w-full items-center gap-1 rounded-md bg-sunken py-0.5 pl-2.5 pr-0.5 text-sm text-ink ring-1 ring-inset',
                  itemError ? 'ring-danger' : 'ring-line',
                )}
              >
                {itemError ? <CircleAlert aria-hidden="true" className="size-3.5 shrink-0 text-danger" /> : null}
                <span className="min-w-0 break-words">{display(item)}</span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  data-remove=""
                  aria-label={`Remove the ${noun} ${display(item)}`}
                  aria-describedby={itemError ? `${errorsId}-${String(index)}` : undefined}
                  className="size-7 shrink-0 pointer-coarse:size-9"
                  onClick={() => {
                    remove(index);
                  }}
                >
                  <X aria-hidden="true" className="size-3.5" />
                </Button>
              </li>
            );
          })}
        </ul>
      ) : null}
      {errorEntries.length > 0 ? (
        <ul className="space-y-1 text-sm font-medium text-danger">
          {errorEntries.map(([index, message]) => (
            <li key={index} id={`${errorsId}-${String(index)}`} className="flex items-start gap-1.5">
              <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              <span>{`${display(items[index] ?? '')}: ${message}`}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {full ? (
        <p className="text-sm text-ink-muted">{`That is the most this list can hold (${String(max)}). Remove one to add another.`}</p>
      ) : (
        <div className="flex items-start gap-2">
          <Field label={addLabel} hideLabel error={inputError} className="min-w-0 flex-1">
            <Input
              ref={inputRef}
              value={input}
              placeholder={placeholder}
              maxLength={splitPaste ? undefined : maxLength}
              autoComplete="off"
              onChange={(event) => {
                setInput(event.target.value);
                if (inputError) setInputError(undefined);
              }}
              onKeyDown={onKeyDown}
            />
          </Field>
          <Button
            variant="secondary"
            aria-label={addLabel}
            onClick={() => {
              add(input);
            }}
          >
            <Plus aria-hidden="true" />
            <span className="max-sm:sr-only">Add</span>
          </Button>
        </div>
      )}
    </Fieldset>
  );
}
