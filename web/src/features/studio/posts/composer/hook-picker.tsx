import { Quote, Sparkles } from 'lucide-react';
import { useId } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Fieldset } from '@/components/ui/field';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { cn } from '@/lib/cn';

import { HOOK_TYPE_LABELS } from '../model/labels';
import { firstParagraph } from '../model/text';
import type { HookVariant } from '../model/types';

export interface HookPickerProps {
  variants: readonly HookVariant[];
  /** The variant the post uses now. */
  selectedId: string | null;
  /** The post text, to tell whether its hook is still one of the variants. */
  text: string;
  onPick: (variant: HookVariant) => void;
  /** Asks the writer for three new hooks (`regenerate` with `what: hooks`). */
  onRewrite?: () => void;
  disabled: boolean;
}

/**
 * The three hook variants (plan: "LinkedIn Studio / Hooks"), each with its type from the
 * taxonomy and the concrete detail it carries. Picking one replaces the post's first paragraph.
 */
export function HookPicker({ variants, selectedId, text, onPick, onRewrite, disabled }: HookPickerProps) {
  const idPrefix = useId();
  const opening = firstParagraph(text);
  const matching = variants.find((variant) => variant.text.trim() === opening);
  const edited = opening !== '' && matching === undefined;
  // The checked hook is the one the text opens with; after an edit none is, so any variant (the
  // last picked one too) can be picked again.
  const value = matching?.id ?? (edited ? '' : (selectedId ?? ''));

  return (
    <Fieldset
      legend="Hook"
      description={
        edited
          ? 'You edited the opening, so it matches none of these. Picking one replaces your first paragraph.'
          : 'The opening line, before the fold. Picking one replaces the first paragraph.'
      }
    >
      {(group) => (
        <div className="grid gap-3">
          <RadioGroup
            {...group}
            value={value}
            disabled={disabled}
            onValueChange={(id) => {
              const variant = variants.find((candidate) => candidate.id === id);
              if (variant) onPick(variant);
            }}
            className="gap-2"
          >
            {variants.map((variant, index) => {
              const itemId = `${idPrefix}-${variant.id}`;
              const checked = variant.id === value;
              return (
                <div
                  key={variant.id}
                  data-checked={checked || undefined}
                  className={cn(
                    'relative flex items-start gap-3 rounded-lg border px-4 py-3 transition-colors duration-150',
                    checked ? 'border-ink bg-sheet' : 'border-line bg-sheet hover:bg-wash',
                    disabled && 'opacity-60',
                  )}
                >
                  <RadioGroupItem id={itemId} value={variant.id} className="mt-1" aria-describedby={`${itemId}-meta`} />
                  <div className="grid min-w-0 flex-1 gap-1.5">
                    {/* The label's ::after covers the card, so the whole card picks the hook. */}
                    <label
                      htmlFor={itemId}
                      className={cn(
                        'text-base text-ink after:absolute after:inset-0 after:rounded-lg',
                        disabled ? 'cursor-not-allowed' : 'cursor-pointer',
                      )}
                    >
                      <span className="sr-only">{`Hook ${String(index + 1)}: `}</span>
                      {variant.text}
                    </label>
                    <p id={`${itemId}-meta`} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
                      <Badge variant="neutral">{HOOK_TYPE_LABELS[variant.hookType]}</Badge>
                      <span>{`Detail: ${variant.concreteDetail}`}</span>
                      {variant.usesOwnerWording ? (
                        <span className="inline-flex items-center gap-1">
                          <Quote aria-hidden="true" className="size-3.5" />
                          Your own words
                        </span>
                      ) : null}
                    </p>
                  </div>
                </div>
              );
            })}
          </RadioGroup>
          {onRewrite ? (
            <div>
              <Button variant="ghost" size="sm" disabled={disabled} onClick={onRewrite}>
                <Sparkles aria-hidden="true" />
                Write new hooks
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </Fieldset>
  );
}
