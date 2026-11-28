import { ArrowRight, CircleAlert, Plus, X } from 'lucide-react';
import { useId, useRef, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/cn';

export interface PairRow {
  readonly from: string;
  readonly to: string;
}

export interface PairsEditorProps {
  legend: string;
  description?: ReactNode;
  rows: readonly PairRow[];
  onChange: (rows: PairRow[]) => void;
  /** Column names: "Old name", "Use instead". */
  fromLabel: string;
  toLabel: string;
  addLabel: string;
  /** What one row is ("rename"), for the remove buttons' names. */
  noun: string;
  rowErrors?: Readonly<Record<number, string>>;
  error?: string | undefined;
  max?: number;
  fromMaxLength?: number;
  toMaxLength?: number;
  className?: string;
}

/**
 * Pairs of words: a deprecated name and the one to use instead, a term and its note. Each row is
 * two labelled fields and a remove button; empty rows are fine while typing and dropped on save.
 */
export function PairsEditor({
  legend,
  description,
  rows,
  onChange,
  fromLabel,
  toLabel,
  addLabel,
  noun,
  rowErrors = {},
  error,
  max,
  fromMaxLength,
  toMaxLength,
  className,
}: PairsEditorProps) {
  const baseId = useId();
  const listRef = useRef<HTMLOListElement>(null);
  const full = max !== undefined && rows.length >= max;

  const change = (index: number, part: Partial<PairRow>) => {
    onChange(rows.map((row, position) => (position === index ? { ...row, ...part } : row)));
  };

  const add = () => {
    onChange([...rows, { from: '', to: '' }]);
    requestAnimationFrame(() => {
      listRef.current?.querySelector<HTMLInputElement>('li:last-child input')?.focus();
    });
  };

  const remove = (index: number) => {
    onChange(rows.filter((_, position) => position !== index));
    requestAnimationFrame(() => {
      const inputs = listRef.current?.querySelectorAll<HTMLInputElement>('li input:first-of-type');
      const next = inputs?.[Math.min(index, inputs.length - 1)];
      (next ?? document.getElementById(`${baseId}-add`))?.focus();
    });
  };

  return (
    <Fieldset legend={legend} description={description} error={error} className={className}>
      {rows.length > 0 ? (
        <div>
          <div aria-hidden="true" className="mb-1.5 hidden grid-cols-[minmax(0,1fr)_1rem_minmax(0,1fr)_2.5rem] gap-2 text-xs font-medium text-ink-subtle sm:grid">
            <span>{fromLabel}</span>
            <span />
            <span>{toLabel}</span>
            <span />
          </div>
          <ol ref={listRef} className="space-y-2">
            {rows.map((row, index) => {
              const rowError = index in rowErrors ? rowErrors[index] : undefined;
              const errorId = `${baseId}-error-${String(index)}`;
              const number = String(index + 1);
              return (
                <li key={index}>
                  <div className="grid grid-cols-[minmax(0,1fr)_2.5rem] gap-2 sm:grid-cols-[minmax(0,1fr)_1rem_minmax(0,1fr)_2.5rem] sm:items-center">
                    <Input
                      aria-label={`${fromLabel} ${number}`}
                      aria-invalid={rowError ? true : undefined}
                      aria-describedby={rowError ? errorId : undefined}
                      value={row.from}
                      maxLength={fromMaxLength}
                      onChange={(event) => {
                        change(index, { from: event.target.value });
                      }}
                    />
                    <ArrowRight aria-hidden="true" className="hidden size-4 text-ink-subtle sm:block" />
                    <Input
                      aria-label={`${toLabel} ${number}`}
                      aria-invalid={rowError ? true : undefined}
                      aria-describedby={rowError ? errorId : undefined}
                      value={row.to}
                      maxLength={toMaxLength}
                      className="col-start-1 sm:col-start-auto"
                      onChange={(event) => {
                        change(index, { to: event.target.value });
                      }}
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove ${noun} ${number}`}
                      className="col-start-2 row-span-2 row-start-1 self-center sm:col-start-auto sm:row-span-1 sm:row-start-auto"
                      onClick={() => {
                        remove(index);
                      }}
                    >
                      <X aria-hidden="true" />
                    </Button>
                  </div>
                  {rowError ? (
                    <p id={errorId} className={cn('mt-1 flex items-start gap-1.5 text-sm font-medium text-danger')}>
                      <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                      <span>{rowError}</span>
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
      <div>
        <Button id={`${baseId}-add`} variant="secondary" size="sm" disabled={full} onClick={add}>
          <Plus aria-hidden="true" />
          {addLabel}
        </Button>
        {full ? <p className="mt-1 text-sm text-ink-muted">{`At most ${String(max)}.`}</p> : null}
      </div>
    </Fieldset>
  );
}
