import { Plus, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { flushSync } from 'react-dom';

import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';

import { itemProblem } from './brief-model';

/** One item of an edited list; `key` is the finding id, or a local key for plain text items. */
export interface EditItem {
  readonly key: string;
  readonly text: string;
}

export interface ItemsEditorProps {
  /** The item's name in a label: "Finding", "Counterpoint". */
  noun: string;
  /** The request field, for the server's per-item messages (`findings`, `counterpoints`). */
  list: string;
  items: readonly EditItem[];
  maxLength: number;
  maxItems: number;
  /** Plain text lists can grow; findings can only be edited or removed. */
  canAdd: boolean;
  saving: boolean;
  /** Messages from a refused save, by dotted path (`counterpoints.1`). */
  problems: Readonly<Record<string, string>>;
  /** The refused save itself, explained above the buttons. */
  error?: unknown;
  onSave: (items: EditItem[]) => void;
  onCancel: () => void;
}

/**
 * Edits a list of short texts in place: one text area per item, Remove beside each, Add for lists
 * that may grow, then Save or Cancel. Empty items must be written or removed before saving.
 */
export function ItemsEditor({
  noun,
  list,
  items,
  maxLength,
  maxItems,
  canAdd,
  saving,
  problems,
  error,
  onSave,
  onCancel,
}: ItemsEditorProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<readonly EditItem[]>(items);
  const [errors, setErrors] = useState<Readonly<Record<string, string>>>({});
  const lower = noun.toLowerCase();

  const save = () => {
    const found: Record<string, string> = {};
    draft.forEach((item) => {
      const text = item.text.trim();
      if (text === '') found[item.key] = `Write the ${lower}, or remove it.`;
      else if (text.length > maxLength) found[item.key] = `Keep it to ${String(maxLength)} characters.`;
    });
    flushSync(() => {
      setErrors(found);
    });
    if (Object.keys(found).length > 0) {
      rootRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
      return;
    }
    onSave(draft.map((item) => ({ key: item.key, text: item.text.trim() })));
  };

  const add = () => {
    const key = `new-${String(Math.max(0, ...draft.map((item) => Number(item.key.replace('new-', '')) || 0)) + 1)}`;
    flushSync(() => {
      setDraft((current) => [...current, { key, text: '' }]);
    });
    const fields = rootRef.current?.querySelectorAll<HTMLTextAreaElement>('textarea');
    fields?.[fields.length - 1]?.focus();
  };

  const remove = (key: string, index: number) => {
    flushSync(() => {
      setDraft((current) => current.filter((item) => item.key !== key));
    });
    // Focus the item that took its place, else the one before, else Add.
    const fields = rootRef.current?.querySelectorAll<HTMLTextAreaElement>('textarea');
    const next = fields?.[Math.min(index, (fields.length || 1) - 1)];
    if (next) next.focus();
    else rootRef.current?.querySelector<HTMLElement>('[data-items-add]')?.focus();
  };

  return (
    <div ref={rootRef} className="grid gap-4">
      {draft.length === 0 ? <p className="text-sm text-ink-muted">{`No ${lower}s left. Save to keep it that way.`}</p> : null}
      <ol className="grid gap-4">
        {draft.map((item, index) => (
          <li key={item.key} className="flex items-start gap-2">
            <Field
              label={`${noun} ${String(index + 1)}`}
              error={errors[item.key] ?? itemProblem(problems, list, index)}
              className="flex-1"
            >
              <Textarea
                value={item.text}
                maxLength={maxLength}
                rows={2}
                className="min-h-16"
                onChange={(event) => {
                  const text = event.target.value;
                  setDraft((current) => current.map((entry) => (entry.key === item.key ? { ...entry, text } : entry)));
                }}
              />
            </Field>
            <Button
              variant="ghost"
              size="icon"
              className="mt-6.5"
              aria-label={`Remove ${lower} ${String(index + 1)}`}
              onClick={() => {
                remove(item.key, index);
              }}
            >
              <X aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ol>
      {error !== undefined ? <ProblemAlert error={error} title="Your change wasn’t saved." /> : null}
      <div className="flex flex-wrap gap-2">
        {canAdd && draft.length < maxItems ? (
          <Button variant="ghost" size="sm" data-items-add onClick={add}>
            <Plus aria-hidden="true" />
            {`Add ${/^[aeiou]/.test(lower) ? 'an' : 'a'} ${lower}`}
          </Button>
        ) : null}
        <span className="flex-1" />
        <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button size="sm" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  );
}
