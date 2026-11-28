import { NotebookPen } from 'lucide-react';
import { useId, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field } from '@/components/ui/field';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isEtagMismatch } from '@/lib/api/envelope';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';

import { BlockLoading } from '../components/settings-block';
import { formatDate } from '../format';
import { settingsKeys, type Schemas } from '../queries';

type ChecklistItem = Schemas['ChecklistItem'];
const NOTE_LIMIT = 500;

function bodyItems(items: readonly ChecklistItem[], change: { id: string; done?: boolean; note?: string | null }) {
  return items.map((item) => ({
    id: item.id,
    label: item.label,
    done: item.id === change.id && change.done !== undefined ? change.done : item.done,
    note: item.id === change.id && change.note !== undefined ? change.note : (item.note ?? null),
  }));
}

function NoteEditor({
  item,
  saving,
  onSave,
  onCancel,
}: {
  item: ChecklistItem;
  saving: boolean;
  onSave: (note: string | null) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(item.note ?? '');
  const tooLong = text.length > NOTE_LIMIT;
  return (
    <div className="mt-3 grid gap-2">
      <Field
        label={`Note on “${item.label}”`}
        description={`${String(text.length)} of ${String(NOTE_LIMIT)} characters.`}
        error={tooLong ? `At most ${String(NOTE_LIMIT)} characters.` : undefined}
      >
        <Textarea
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={saving || tooLong}
          onClick={() => {
            onSave(text.trim() === '' ? null : text.trim());
          }}
        >
          {saving ? 'Saving…' : 'Save the note'}
        </Button>
        <Button size="sm" variant="ghost" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function ChecklistRow({
  item,
  saving,
  onToggle,
  onNote,
}: {
  item: ChecklistItem;
  saving: boolean;
  onToggle: (done: boolean) => void;
  onNote: (note: string | null, done: () => void) => void;
}) {
  const id = useId();
  const [editing, setEditing] = useState(false);
  return (
    <li className="border-b border-line py-4">
      <div className="flex items-start gap-3">
        <div className="flex h-5.5 shrink-0 items-center pointer-coarse:h-11">
          <Checkbox
            id={id}
            checked={item.done}
            disabled={saving}
            onCheckedChange={(checked) => {
              onToggle(checked === true);
            }}
          />
        </div>
        <div className="grid min-w-0 flex-1 gap-1">
          <div className="flex min-h-5.5 items-center pointer-coarse:min-h-11">
            <Label htmlFor={id} className={item.done ? 'text-ink-muted line-through decoration-line-strong' : undefined}>
              {item.label}
            </Label>
          </div>
          {item.done && item.doneAt ? <p className="text-xs text-ink-subtle">Done {formatDate(item.doneAt)}</p> : null}
          {item.note && !editing ? <p className="text-sm text-ink-muted">{item.note}</p> : null}
          {editing ? (
            <NoteEditor
              item={item}
              saving={saving}
              onSave={(note) => {
                onNote(note, () => {
                  setEditing(false);
                });
              }}
              onCancel={() => {
                setEditing(false);
              }}
            />
          ) : (
            <div>
              <Button
                size="sm"
                variant="link"
                aria-label={`${item.note ? 'Edit the note on' : 'Add a note to'} “${item.label}”`}
                onClick={() => {
                  setEditing(true);
                }}
              >
                <NotebookPen aria-hidden="true" />
                {item.note ? 'Edit note' : 'Add a note'}
              </Button>
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/**
 * The one-off profile alignment checklist (headline, About, Featured): ticked as the owner works
 * through LinkedIn's own profile pages. Each tick saves at once.
 */
export function ProfileChecklistPanel() {
  const checklist = useApiQuery({ queryKey: settingsKeys.profileChecklist, path: '/api/engagement/profile-checklist' });
  const save = useApiMutation('put', '/api/engagement/profile-checklist', {
    updates: () => settingsKeys.profileChecklist,
    handles: ['etag_mismatch'],
    errorToast: 'Couldn’t save the checklist.',
  });

  if (checklist.isPending) return <BlockLoading label="Loading the profile checklist" />;
  if (checklist.isError) {
    return (
      <ProblemAlert
        error={checklist.error}
        title="Couldn’t load the profile checklist."
        onRetry={() => void checklist.refetch()}
        retryLabel="Try loading the profile checklist again"
      />
    );
  }
  const { item, etag } = checklist.data;
  const items = item.items ?? [];
  const done = items.filter((entry) => entry.done).length;

  const send = (change: { id: string; done?: boolean; note?: string | null }, after?: () => void) => {
    save.mutate(
      { body: { items: bodyItems(items, change) }, ifMatch: etag },
      {
        onSuccess: () => after?.(),
        onError: (error) => {
          if (!isEtagMismatch(error)) return;
          void checklist.refetch();
          toast.error('The checklist changed in another tab.', {
            description: 'It’s reloaded with the latest version. Make your change again.',
          });
        },
      },
    );
  };

  return (
    <div className="max-w-measure">
      <div className="flex items-center gap-4">
        <Progress
          value={done}
          max={Math.max(items.length, 1)}
          tone={done === items.length && items.length > 0 ? 'success' : 'ink'}
          aria-label="Profile checklist done"
          getValueLabel={() => `${String(done)} of ${String(items.length)} done`}
          className="max-w-48"
        />
        <p className="text-sm text-ink-muted">
          {String(done)} of {String(items.length)} done
        </p>
      </div>
      <ul aria-label="Profile checklist" className="mt-4 border-t border-line">
        {items.map((entry) => (
          <ChecklistRow
            key={entry.id}
            item={entry}
            saving={save.isPending}
            onToggle={(next) => {
              send({ id: entry.id, done: next });
            }}
            onNote={(note, close) => {
              send({ id: entry.id, note }, close);
            }}
          />
        ))}
      </ul>
    </div>
  );
}
