import type { ReactNode } from 'react';

import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';

export interface NumberFieldProps {
  /** Names the number, unit included for screen readers when it matters ("Shortest, in words"). */
  label: string;
  /** The value as typed (kept as text, so an empty field stays empty while editing). */
  value: string;
  onChange: (value: string) => void;
  /** Shown after the field: "words", "%", "hours". */
  unit?: string;
  description?: ReactNode;
  error?: string | undefined;
  /** Allows a decimal point (percentages). */
  decimal?: boolean;
  className?: string;
}

/** A whole number (or a percentage) with its unit, labelled and wired for errors. */
export function NumberField({ label, value, onChange, unit, description, error, decimal = false, className }: NumberFieldProps) {
  return (
    <Field label={label} description={description} error={error} className={className}>
      {(control) => (
        <div className="flex items-center gap-2">
          <Input
            {...control}
            inputMode={decimal ? 'decimal' : 'numeric'}
            autoComplete="off"
            value={value}
            className="w-24 tabular-nums"
            onChange={(event) => {
              onChange(event.target.value);
            }}
          />
          {unit ? (
            <span aria-hidden="true" className="text-sm text-ink-muted">
              {unit}
            </span>
          ) : null}
        </div>
      )}
    </Field>
  );
}
