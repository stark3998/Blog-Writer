import { Plus, Search, Trash2 } from 'lucide-react';
import { useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import type { Versioned } from '@/lib/api/envelope';
import { fieldErrors } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';

import { SaveBar, useVersionedDraft } from '../components/form-kit';
import { settingsKeys, type Schemas } from '../queries';

type Blocklist = Schemas['Blocklist'];
type Category = Schemas['BlocklistTerm']['category'];
type Match = Schemas['BlocklistTerm']['match'];

const CATEGORIES: readonly { value: Category; label: string }[] = [
  { value: 'client', label: 'Client' },
  { value: 'project', label: 'Project' },
  { value: 'person', label: 'Person' },
  { value: 'internal', label: 'Internal name' },
  { value: 'vendor', label: 'Vendor' },
  { value: 'other', label: 'Other' },
];
const MATCHES: readonly { value: Match; label: string }[] = [
  { value: 'word', label: 'Whole word' },
  { value: 'substring', label: 'Anywhere in a word' },
];
const MIN_TERM = 2;
const MAX_TERM = 100;
const MAX_TERMS = 2000;
const PAGE = 50;

interface TermDraft {
  key: string;
  id: string | null;
  term: string;
  category: Category;
  match: Match;
}

function toDraft(item: Blocklist): TermDraft[] {
  return (item.terms ?? []).map((term) => ({
    key: term.id,
    id: term.id,
    term: term.term,
    category: term.category,
    match: term.match,
  }));
}

function categoryLabel(value: Category): string {
  return CATEGORIES.find((item) => item.value === value)?.label ?? value;
}

function matchLabel(value: Match): string {
  return MATCHES.find((item) => item.value === value)?.label ?? value;
}

function describe(terms: readonly Pick<TermDraft, 'term' | 'category' | 'match'>[]): string {
  return terms.map((term) => `${term.term} (${categoryLabel(term.category)}, ${matchLabel(term.match)})`).join('\n');
}

let newKeys = 0;

/** What adding `text` (one term per line) would do: the terms to add and why others were skipped. */
export function termsToAdd(
  text: string,
  existing: readonly Pick<TermDraft, 'term'>[],
): { terms: string[]; skipped: string[] } {
  const known = new Set(existing.map((term) => term.term.normalize('NFC').toLocaleLowerCase()));
  const terms: string[] = [];
  const skipped: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const term = line.trim();
    if (term === '') continue;
    const folded = term.normalize('NFC').toLocaleLowerCase();
    if (term.length < MIN_TERM) skipped.push(`“${term}” is shorter than ${String(MIN_TERM)} characters`);
    else if (term.length > MAX_TERM) skipped.push(`a term is longer than ${String(MAX_TERM)} characters`);
    else if (known.has(folded)) skipped.push(`“${term}” is already on the list`);
    else {
      known.add(folded);
      terms.push(term);
    }
  }
  return { terms, skipped };
}

function TermRow({
  term,
  index,
  error,
  onChange,
  onRemove,
}: {
  term: TermDraft;
  index: number;
  error: string | undefined;
  onChange: (term: TermDraft) => void;
  onRemove: () => void;
}) {
  return (
    <li className="grid gap-2 border-b border-line py-3 sm:grid-cols-[minmax(0,1fr)_10rem_12rem_auto] sm:items-center">
      <div className="min-w-0">
        <p className="break-words font-medium text-ink">{term.term}</p>
        {error ? <p className="text-sm font-medium text-danger">{error}</p> : null}
      </div>
      <Field label={`Category of term ${String(index + 1)}`} hideLabel>
        {(control) => (
          <Select
            value={term.category}
            onValueChange={(value) => {
              const category = CATEGORIES.find((item) => item.value === value)?.value;
              if (category) onChange({ ...term, category });
            }}
          >
            <SelectTrigger {...control} className="h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CATEGORIES.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      <Field label={`Matching of term ${String(index + 1)}`} hideLabel>
        {(control) => (
          <Select
            value={term.match}
            onValueChange={(value) => {
              const match = MATCHES.find((item) => item.value === value)?.value;
              if (match) onChange({ ...term, match });
            }}
          >
            <SelectTrigger {...control} className="h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MATCHES.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      <Button variant="ghost" size="icon" aria-label={`Remove term ${String(index + 1)}`} onClick={onRemove}>
        <Trash2 aria-hidden="true" />
      </Button>
    </li>
  );
}

export interface BlocklistEditorProps {
  source: Versioned<Blocklist>;
  latest: Versioned<Blocklist>;
  onReload: () => Promise<Versioned<Blocklist> | undefined>;
}

/**
 * The blocklist: client and internal project names that must never reach a model, a search, a
 * post or a log. Terms stay on this screen and in the request that saves them; checks point at
 * them by id.
 */
export function BlocklistEditor({ source, latest, onReload }: BlocklistEditorProps) {
  const form = useVersionedDraft(source, toDraft);
  const terms = form.draft;
  const conflict = useConflict();
  const [entry, setEntry] = useState('');
  const [category, setCategory] = useState<Category>('client');
  const [match, setMatch] = useState<Match>('word');
  const [addNote, setAddNote] = useState<string | undefined>();
  const [filter, setFilter] = useState('');
  const [showAll, setShowAll] = useState(false);
  const entryRef = useRef<HTMLTextAreaElement>(null);
  const save = useApiMutation('put', '/api/kb/blocklist', {
    updates: () => settingsKeys.blocklist,
    invalidates: [settingsKeys.ownerInputs],
    successToast: (data) =>
      `Blocklist saved: ${String(data.item.terms?.length ?? 0)} ${data.item.terms?.length === 1 ? 'term' : 'terms'}.`,
    errorToast: 'Couldn’t save the blocklist.',
    handles: ['etag_mismatch', 'validation_failed'],
  });
  const serverErrors = fieldErrors(save.error);
  const configured = form.base.item.configured;

  const add = () => {
    const { terms: added, skipped } = termsToAdd(entry, terms);
    if (added.length === 0 && skipped.length === 0) return;
    if (terms.length + added.length > MAX_TERMS) {
      setAddNote(`The list holds at most ${String(MAX_TERMS)} terms.`);
      return;
    }
    if (save.isError) save.reset();
    flushSync(() => {
      form.setDraft((current) => [
        ...current,
        ...added.map((term) => {
          newKeys += 1;
          return { key: `new-${String(newKeys)}`, id: null, term, category, match };
        }),
      ]);
    });
    setEntry('');
    const addedText = added.length === 1 ? 'Added 1 term.' : `Added ${String(added.length)} terms.`;
    setAddNote(
      skipped.length > 0
        ? `${added.length > 0 ? `${addedText} ` : ''}Skipped: ${skipped.join('; ')}.`
        : `${addedText} Save the blocklist to apply ${added.length === 1 ? 'it' : 'them'}.`,
    );
    entryRef.current?.focus();
  };

  const send = (etag: string | null) => {
    save.mutate(
      {
        body: {
          terms: terms.map((term) => ({ id: term.id, term: term.term, category: term.category, match: term.match })),
        },
        ifMatch: etag,
      },
      {
        onSuccess: (data) => {
          form.reset(data);
          conflict.close();
          setAddNote(undefined);
        },
        onError: (error) => {
          if (conflict.show(error)) void onReload();
        },
      },
    );
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    send(form.base.etag);
  };

  const needle = filter.trim().toLocaleLowerCase();
  const indexed = terms.map((term, index) => ({ term, index }));
  const matching = needle === '' ? indexed : indexed.filter(({ term }) => term.term.toLocaleLowerCase().includes(needle));
  const visible = showAll || needle !== '' ? matching : matching.slice(0, PAGE);

  return (
    <form noValidate onSubmit={submit} aria-label="Blocklist" className="grid max-w-3xl gap-6">
      {configured ? null : (
        <Alert variant="warn">
          <AlertTitle>The blocklist isn’t saved yet.</AlertTitle>
          <AlertDescription>
            <p>
              Until you save it once, every check that needs the blocklist fails closed: intake, research, revisions and
              approvals wait. An empty list is a valid answer.
            </p>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-3 rounded-lg border border-line bg-sunken p-4">
        <Field
          label="Add terms"
          description="One term per line: client names, internal project and product names, people. Paste a list to add it all."
        >
          {(control) => (
            <Textarea
              {...control}
              ref={entryRef}
              rows={2}
              value={entry}
              className="min-h-16 bg-sheet"
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => {
                setEntry(event.target.value);
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  add();
                }
              }}
            />
          )}
        </Field>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Category">
            {(control) => (
              <Select
                value={category}
                onValueChange={(value) => {
                  const next = CATEGORIES.find((item) => item.value === value)?.value;
                  if (next) setCategory(next);
                }}
              >
                <SelectTrigger {...control} className="min-w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>
          <Field label="Matching">
            {(control) => (
              <Select
                value={match}
                onValueChange={(value) => {
                  const next = MATCHES.find((item) => item.value === value)?.value;
                  if (next) setMatch(next);
                }}
              >
                <SelectTrigger {...control} className="min-w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MATCHES.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>
          <Button variant="secondary" disabled={entry.trim() === ''} onClick={add}>
            <Plus aria-hidden="true" />
            Add to the list
          </Button>
        </div>
        {addNote ? (
          <p role="status" className="text-sm text-ink-muted">
            {addNote}
          </p>
        ) : null}
      </div>

      <div className="grid gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="font-semibold text-ink">
            {terms.length === 0
              ? 'No terms on the list'
              : `${String(terms.length)} ${terms.length === 1 ? 'term' : 'terms'} on the list`}
          </p>
          {terms.length > 12 ? (
            <Field label="Find a term" hideLabel>
              {(control) => (
                <div className="relative">
                  <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-subtle" />
                  <Input
                    {...control}
                    type="search"
                    placeholder="Find a term"
                    value={filter}
                    className="w-64 pl-9"
                    onChange={(event) => {
                      setFilter(event.target.value);
                    }}
                  />
                </div>
              )}
            </Field>
          ) : null}
        </div>
        {terms.length > 0 ? (
          <ul aria-label="Blocklist terms" className="border-t border-line">
            {visible.map(({ term, index }) => (
              <TermRow
                key={term.key}
                term={term}
                index={index}
                error={serverErrors[`terms.${String(index)}.term`] ?? serverErrors[`terms.${String(index)}`]}
                onChange={(next) => {
                  form.setDraft((current) => current.map((item) => (item.key === term.key ? next : item)));
                }}
                onRemove={() => {
                  form.setDraft((current) => current.filter((item) => item.key !== term.key));
                }}
              />
            ))}
          </ul>
        ) : (
          <p className="text-ink-muted">
            Add the names you never want to appear. If there are none, save the empty list so the checks can run.
          </p>
        )}
        {needle !== '' && matching.length === 0 ? <p className="text-ink-muted">No term matches “{filter}”.</p> : null}
        {!showAll && needle === '' && matching.length > PAGE ? (
          <Button
            variant="secondary"
            className="justify-self-start"
            onClick={() => {
              setShowAll(true);
            }}
          >
            {`Show all ${String(matching.length)} terms`}
          </Button>
        ) : null}
      </div>

      <SaveBar
        sticky
        dirty={form.dirty}
        saving={save.isPending}
        paused={save.isPaused}
        saveLabel={configured || terms.length > 0 ? 'Save the blocklist' : 'Save an empty blocklist'}
        allowClean={!configured}
        idleNote={configured ? undefined : 'Not saved yet.'}
        onDiscard={() => {
          save.reset();
          form.reset(form.base);
          setAddNote(undefined);
        }}
      />
      <ConflictDialog
        {...conflict}
        subject="The blocklist"
        mine={describe(terms)}
        theirs={latest.etag === form.base.etag ? undefined : describe(toDraft(latest.item))}
        saving={save.isPending}
        onKeepMine={() => {
          const etag = conflict.conflict?.etag;
          if (etag) {
            send(etag);
          } else {
            void onReload().then(() => {
              conflict.close();
            });
          }
        }}
        onTakeTheirs={() => {
          void onReload().then((current) => {
            if (current) form.reset(current);
            conflict.close();
          });
        }}
      />
    </form>
  );
}
