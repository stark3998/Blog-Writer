import { CircleAlert, Lock, Plus, Trash2 } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, Fieldset, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';

import { ConfirmAction } from './components/confirm-action';
import { NumberField } from './components/number-field';
import { SaveBar } from './components/save-bar';
import { LibrarySection, LoadError, LoadingBlock } from './components/section-intro';
import { StringListEditor } from './components/string-list-editor';
import { UnsavedChangesGuard } from './components/unsaved-changes-guard';
import { useSettingsDraft } from './hooks/use-settings-draft';
import { FormatRuler } from './formats/format-ruler';
import {
  describeFormats,
  formatsToBody,
  formatsToDraft,
  newPack,
  PACK_LIMITS,
  validateFormats,
  type FormatsDraft,
  type OwnerInput,
  type PackDraft,
} from './formats/model';
import { errorsUnder, hasErrors, itemErrors, mergeErrors, NO_ERRORS, serverFieldErrors, type ErrorMap } from './lib/errors';
import { formatInteger, plural } from './lib/format';
import { OWNER_INPUT_LABEL } from './lib/labels';
import { parseWhole, SLUG_HINT, slugify } from './lib/rules';

function packSummary(pack: PackDraft): string {
  const min = parseWhole(pack.minWords);
  const max = parseWhole(pack.maxWords);
  const length = min !== undefined && max !== undefined ? `${formatInteger(min)}–${formatInteger(max)} words` : 'Length not set';
  const questions =
    pack.minQuestions === pack.maxQuestions ? `${pack.minQuestions} questions` : `${pack.minQuestions}–${pack.maxQuestions} questions`;
  return `${length}. ${OWNER_INPUT_LABEL[pack.ownerInput]}, ${questions}.`;
}

interface PackFormProps {
  pack: PackDraft;
  errors: ErrorMap;
  onChange: (change: (pack: PackDraft) => PackDraft) => void;
  onRemove: () => void;
  canRemove: boolean;
}

function PackForm({ pack, errors, onChange, onRemove, canRemove }: PackFormProps) {
  const set = <K extends keyof PackDraft>(field: K, value: PackDraft[K]) => {
    onChange((current) => ({ ...current, [field]: value }));
  };
  const name = pack.name.trim() || 'this format';
  return (
    <div className="space-y-7 pt-2">
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Name" required error={errors.name}>
          <Input
            value={pack.name}
            maxLength={PACK_LIMITS.name}
            onChange={(event) => {
              const value = event.target.value;
              onChange((current) => {
                const follows = !current.saved && (current.id === '' || current.id === slugify(current.name, PACK_LIMITS.id));
                return { ...current, name: value, id: follows ? slugify(value, PACK_LIMITS.id) : current.id };
              });
            }}
          />
        </Field>
        {pack.saved ? (
          <p className="flex items-start gap-2 self-end pb-2 text-sm text-ink-muted">
            <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
            <span>
              Id <code className="font-mono text-ink">{pack.id}</code>. Articles name their format by it.
            </span>
          </p>
        ) : (
          <Field label="Id" required error={errors.id} description={SLUG_HINT}>
            <Input
              value={pack.id}
              maxLength={PACK_LIMITS.id}
              className="font-mono"
              autoCapitalize="none"
              spellCheck={false}
              onChange={(event) => {
                set('id', event.target.value.toLowerCase());
              }}
            />
          </Field>
        )}
      </div>

      <Fieldset legend="Length" description="The draft aims inside this range; the gates flag anything outside it.">
        <div className="grid gap-5 sm:grid-cols-2">
          <NumberField
            label="Shortest, in words"
            unit="words"
            value={pack.minWords}
            error={errors.minWords}
            onChange={(value) => {
              set('minWords', value);
            }}
          />
          <NumberField
            label="Longest, in words"
            unit="words"
            value={pack.maxWords}
            error={errors.maxWords}
            onChange={(value) => {
              set('maxWords', value);
            }}
          />
        </div>
      </Fieldset>

      <Fieldset legend="Your input" description="What the pipeline asks you before drafting.">
        {(group) => (
          <div className="space-y-5">
            <SegmentedControl
              {...group}
              value={pack.ownerInput}
              onValueChange={(value) => {
                set('ownerInput', value as OwnerInput);
              }}
            >
              <SegmentedControlItem value="full_interview">{OWNER_INPUT_LABEL.full_interview}</SegmentedControlItem>
              <SegmentedControlItem value="micro_interview">{OWNER_INPUT_LABEL.micro_interview}</SegmentedControlItem>
            </SegmentedControl>
            <div className="grid gap-5 sm:grid-cols-2">
              <NumberField
                label="Fewest questions"
                value={pack.minQuestions}
                error={errors.minQuestions}
                onChange={(value) => {
                  set('minQuestions', value);
                }}
              />
              <NumberField
                label="Most questions"
                value={pack.maxQuestions}
                error={errors.maxQuestions}
                onChange={(value) => {
                  set('maxQuestions', value);
                }}
              />
            </div>
          </div>
        )}
      </Fieldset>

      <StringListEditor
        legend="Must contain"
        description="Parts every article in this format has. The rubric checks for each one."
        items={pack.mustContain}
        onChange={(items) => {
          set('mustContain', items);
        }}
        addLabel={`Add a required part to ${name}`}
        noun="part"
        placeholder="decisions and trade-offs"
        max={PACK_LIMITS.items}
        maxLength={PACK_LIMITS.itemLength}
        itemErrors={itemErrors(errors, 'mustContain')}
        error={errors.mustContain}
      />

      <StringListEditor
        legend="Not allowed"
        description="What the drafts in this format must never do, such as frameworks the AI invented."
        items={pack.banned}
        onChange={(items) => {
          set('banned', items);
        }}
        addLabel={`Add something ${name} must not do`}
        noun="item"
        max={PACK_LIMITS.items}
        maxLength={PACK_LIMITS.itemLength}
        itemErrors={itemErrors(errors, 'banned')}
        error={errors.banned}
      />

      {errors[''] ? (
        <p className="flex items-start gap-1.5 text-sm font-medium text-danger">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {errors['']}
        </p>
      ) : null}

      <div className="border-t border-line pt-4">
        {canRemove ? (
          <ConfirmAction
            title={`Remove ${name}?`}
            description="The format goes when you save. Articles already in this format keep their text."
            confirmLabel="Remove format"
            cancelLabel="Keep it"
            onConfirm={onRemove}
          >
            <Button variant="ghost" className="text-danger hover:text-danger">
              <Trash2 aria-hidden="true" />
              {`Remove ${name}`}
            </Button>
          </ConfirmAction>
        ) : (
          <p className="text-sm text-ink-muted">You need at least one format.</p>
        )}
      </div>
    </div>
  );
}

/**
 * Library > Format packs (`/library/formats`): each article format's length, required parts,
 * what it must never do and how much of your input it needs. Calls `GET/PUT /api/kb/formats`
 * (contract section 10).
 */
export function FormatsSection() {
  const query = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.formats), path: '/api/kb/formats' });
  const formats = useSettingsDraft(query.data, formatsToDraft);
  const conflict = useConflict();
  const editorRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<string[]>([]);
  const [showErrors, setShowErrors] = useState(false);
  const save = useApiMutation('put', '/api/kb/formats', {
    updates: () => queryKeys.kb.detail(KB_SINGLETONS.formats),
    successToast: 'Formats saved.',
    errorToast: 'Couldn’t save the formats.',
    handles: ['etag_mismatch', 'validation_failed', 'confidential_text'],
  });

  const draft = formats.draft;
  const clientErrors = useMemo(() => (draft ? validateFormats(draft) : NO_ERRORS), [draft]);
  const errors = mergeErrors(showErrors ? clientErrors : NO_ERRORS, serverFieldErrors(save.error));
  const total = Object.keys(errors).length;
  const genericError =
    save.error && !isProblem(save.error, ['etag_mismatch', 'validation_failed', 'confidential_text']) ? save.error : undefined;

  const update = (index: number, change: (pack: PackDraft) => PackDraft) => {
    formats.update((current) => ({ packs: current.packs.map((pack, position) => (position === index ? change(pack) : pack)) }));
  };

  const send = (sent: FormatsDraft, ifMatch: string | null) => {
    save.mutate(
      { body: formatsToBody(sent), ifMatch },
      {
        onSuccess: (data) => {
          formats.saved(sent, data);
          conflict.close();
          setShowErrors(false);
        },
        onError: (error) => {
          if (conflict.show(error)) {
            void query.refetch();
            return;
          }
          openPacksWithErrors(serverFieldErrors(error));
        },
      },
    );
  };

  /** Opens every format that has an error, then focuses the first one. */
  const openPacksWithErrors = (found: ErrorMap) => {
    if (!draft) return;
    const keys = draft.packs.filter((_, index) => hasErrors(errorsUnder(found, `packs.${String(index)}`))).map((pack) => pack.key);
    setOpen((current) => [...new Set([...current, ...keys])]);
    requestAnimationFrame(() => {
      focusFirstInvalid(editorRef.current);
    });
  };

  const onSave = () => {
    if (!draft) return;
    setShowErrors(true);
    if (hasErrors(clientErrors)) {
      openPacksWithErrors(clientErrors);
      return;
    }
    send(draft, formats.baseEtag ?? null);
  };

  return (
    <LibrarySection
      title="Article formats"
      standfirst={<p>Each format sets how long an article runs, the parts it must contain and how much of your input it needs.</p>}
      yourPart="Adjust a format when it stops working for you. Frameworks must be yours: invented ones are refused."
    >

      {query.isPending ? (
        <LoadingBlock label="Loading the formats" rows={4} />
      ) : query.isError ? (
        <LoadError error={query.error} title="Couldn’t load the formats." retryLabel="Try loading the formats again" onRetry={() => void query.refetch()} />
      ) : draft ? (
        <div ref={editorRef} className="space-y-10">
          {formats.baseEtag === null ? (
            <Alert variant="info">
              <AlertTitle>These are the plan’s four formats.</AlertTitle>
              <AlertDescription>
                <p>Adjust one when it stops working for you, then save.</p>
              </AlertDescription>
            </Alert>
          ) : null}

          <FormatRuler
            entries={draft.packs.map((pack) => ({
              key: pack.key,
              name: pack.name,
              min: parseWhole(pack.minWords),
              max: parseWhole(pack.maxWords),
            }))}
          />

          {showErrors && total > 0 ? (
            <Alert variant="error">
              <AlertTitle>{`${plural(total, 'field needs', 'fields need')} a fix before saving.`}</AlertTitle>
              <AlertDescription>
                <p>The formats with a problem are open below.</p>
              </AlertDescription>
            </Alert>
          ) : null}
          {genericError ? <ProblemAlert error={genericError} title="Couldn’t save the formats." /> : null}

          <div>
            <Accordion type="multiple" value={open} onValueChange={setOpen}>
              {draft.packs.map((pack, index) => {
                const packErrors = errorsUnder(errors, `packs.${String(index)}`);
                const broken = hasErrors(packErrors);
                return (
                  <AccordionItem key={pack.key} value={pack.key}>
                    <AccordionTrigger>
                      <span className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-4">
                        <span className="flex items-center gap-2">
                          {broken ? (
                            <>
                              <CircleAlert aria-hidden="true" className="size-4 shrink-0 text-danger" />
                              <span className="sr-only">Needs a fix:</span>
                            </>
                          ) : null}
                          {pack.name.trim() || 'New format'}
                        </span>
                        <span className="text-sm font-normal text-ink-muted">{packSummary(pack)}</span>
                      </span>
                    </AccordionTrigger>
                    <AccordionContent>
                      <PackForm
                        pack={pack}
                        errors={packErrors}
                        onChange={(change) => {
                          update(index, change);
                        }}
                        onRemove={() => {
                          formats.update((current) => ({ packs: current.packs.filter((_, position) => position !== index) }));
                        }}
                        canRemove={draft.packs.length > 1}
                      />
                    </AccordionContent>
                  </AccordionItem>
                );
              })}
            </Accordion>
            {errors.packs ? <p className="mt-3 text-sm font-medium text-danger">{errors.packs}</p> : null}
            <Button
              variant="secondary"
              size="sm"
              className="mt-5"
              onClick={() => {
                const pack = newPack();
                formats.update((current) => ({ packs: [...current.packs, pack] }));
                setOpen((current) => [...current, pack.key]);
                requestAnimationFrame(() => {
                  editorRef.current?.querySelector<HTMLInputElement>(`[data-state="open"] input`)?.focus();
                });
              }}
            >
              <Plus aria-hidden="true" />
              Add a format
            </Button>
          </div>
        </div>
      ) : null}

      <SaveBar
        dirty={formats.dirty}
        saving={save.isPending}
        saveLabel="Save formats"
        onSave={onSave}
        onDiscard={() => {
          formats.discard();
          save.reset();
          setShowErrors(false);
        }}
        blockedReason={showErrors && total > 0 ? `${plural(total, 'field needs', 'fields need')} a fix.` : undefined}
      />

      <ConflictDialog
        {...conflict}
        subject="The formats"
        mine={draft ? describeFormats(draft) : undefined}
        theirs={query.data && query.data.etag !== formats.baseEtag ? describeFormats(formatsToDraft(query.data.item)) : undefined}
        saving={save.isPending}
        onKeepMine={() => {
          if (draft) send(draft, conflict.conflict?.etag ?? query.data?.etag ?? null);
        }}
        onTakeTheirs={() => {
          void query.refetch().then(() => {
            formats.takeServer();
          });
          save.reset();
          conflict.close();
        }}
      />

      <UnsavedChangesGuard when={formats.dirty} subject="the formats" />
    </LibrarySection>
  );
}
