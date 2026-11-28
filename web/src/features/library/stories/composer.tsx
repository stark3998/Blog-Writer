import { X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { focusFirstInvalid } from '@/components/ui/field';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { hasUnsavedText, IntakeFlow, useIntakeState } from '@/features/intake';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { UnsavedChangesGuard } from '../components/unsaved-changes-guard';
import { usePillarDirectory } from '../hooks/use-pillars';
import { sameDraft } from '../hooks/use-settings-draft';
import { hasErrors, mergeErrors, NO_ERRORS, serverFieldErrors, type ErrorMap } from '../lib/errors';
import type { Entry, PovEntry, StoryEntry } from '../lib/types';
import { PovFields, PovPillarField, StoryFields } from './forms';
import {
  describePov,
  describeStory,
  EMPTY_STORY,
  formToPovIn,
  formToStoryIn,
  OWNER_INPUTS_KEY,
  povToForm,
  storyToForm,
  validatePov,
  validateStory,
  type PovForm,
  type StoryForm,
} from './model';

export type ComposerMode =
  | { readonly kind: 'new-story' }
  | { readonly kind: 'new-pov' }
  | { readonly kind: 'edit-story'; readonly entry: Entry<StoryEntry> }
  | { readonly kind: 'edit-pov'; readonly entry: Entry<PovEntry> };

const HANDLED = ['validation_failed', 'confidential_text', 'unknown_pillar', 'blocklist_not_configured', 'etag_mismatch'];
const linkClass = 'text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink';

const invalidations = [queryKeys.stories.lists(), queryKeys.povs.lists(), OWNER_INPUTS_KEY];

/** Field errors of a refused save; `unknown_pillar` names the pillar control. */
function saveErrors(error: unknown, pillarField: 'pillars' | 'pillar'): ErrorMap {
  const errors: Record<string, string> = { ...serverFieldErrors(error) };
  if (isProblem(error, 'unknown_pillar')) errors[pillarField] = problemMessage(error);
  return errors;
}

/** Problems a save shows above the form rather than at a field. */
function SaveProblem({ error }: { error: unknown }) {
  if (!error) return null;
  if (isProblem(error, 'blocklist_not_configured')) {
    return (
      <Alert variant="error">
        <AlertTitle>Save your blocklist first.</AlertTitle>
        <AlertDescription>
          <p>
            Entries are checked against it before they are stored; an empty list is fine. Open{' '}
            <Link to="/settings/policy" className={linkClass}>
              Blocklist & policy
            </Link>
            .
          </p>
        </AlertDescription>
      </Alert>
    );
  }
  if (isProblem(error, 'confidential_text')) {
    return (
      <Alert variant="error">
        <AlertTitle>Some details can’t be stored.</AlertTitle>
        <AlertDescription>
          <p>{problemMessage(error)} Each one is marked at its field.</p>
        </AlertDescription>
      </Alert>
    );
  }
  if (isProblem(error, HANDLED)) return null;
  return <ProblemAlert error={error} title="Couldn’t save it." />;
}

interface PanelProps {
  title: string;
  /** True while closing would drop something the owner wrote. */
  dirty: boolean;
  onClose: () => void;
  /** The panel's content; `close` asks first when there is something to lose. */
  children: (close: () => void) => ReactNode;
}

function Panel({ title, dirty, onClose, children }: PanelProps) {
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [asking, setAsking] = useState(false);
  useEffect(() => {
    headingRef.current?.focus();
  }, []);
  const close = () => {
    if (dirty) setAsking(true);
    else onClose();
  };
  return (
    <section aria-labelledby={headingId} className="relative space-y-6 rounded-lg border border-line-strong bg-sheet p-4 sm:p-6">
      <div className="flex items-start justify-between gap-3 pr-10">
        <h3 id={headingId} ref={headingRef} tabIndex={-1} className="font-serif text-2xl font-medium text-ink outline-none">
          {title}
        </h3>
      </div>
      <Button variant="ghost" size="icon" className="absolute right-3 top-3" aria-label={`Close: ${title}`} onClick={close}>
        <X aria-hidden="true" />
      </Button>
      {children(close)}
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard what you wrote?</AlertDialogTitle>
            <AlertDialogDescription>Nothing here is saved yet. Your words are not kept anywhere once you close this.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep writing</AlertDialogCancel>
            <AlertDialogAction variant="danger" onClick={onClose}>
              Discard
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function NewStory({ onClose }: { onClose: () => void }) {
  const { pillars } = usePillarDirectory();
  const formId = useId();
  const formRef = useRef<HTMLDivElement>(null);
  const [intake, setIntake] = useIntakeState();
  const [form, setForm] = useState<StoryForm | undefined>();
  const [showErrors, setShowErrors] = useState(false);
  const create = useApiMutation('post', '/api/stories', {
    invalidates: invalidations,
    successToast: 'Story saved and approved.',
    errorToast: 'Couldn’t save the story.',
    handles: HANDLED,
  });
  const clientErrors = useMemo(() => (form ? validateStory(form) : NO_ERRORS), [form]);
  const errors = mergeErrors(showErrors ? clientErrors : NO_ERRORS, saveErrors(create.error, 'pillars'));

  const save = () => {
    if (!form) return;
    setShowErrors(true);
    if (hasErrors(clientErrors)) {
      requestAnimationFrame(() => {
        focusFirstInvalid(formRef.current);
      });
      return;
    }
    create.mutate(
      { body: formToStoryIn(form) },
      {
        onSuccess: onClose,
        onError: () => {
          requestAnimationFrame(() => {
            focusFirstInvalid(formRef.current);
          });
        },
      },
    );
  };

  return (
    <Panel title="New story" dirty={hasUnsavedText(intake) || form !== undefined} onClose={onClose}>
      {(close) => (
        <>
          <p className="max-w-measure text-ink-muted">
            Tell it roughly first. Confidential details are found and a public-safe wording is proposed; only the wording you confirm is
            kept. Then shape it into its parts.
          </p>
          <IntakeFlow
            purpose="story"
            label="The story, in your own words"
            headingLevel={4}
            state={intake}
            onStateChange={setIntake}
            confirmLabel="Use this wording"
            maxLength={2000}
            onConfirm={(confirmed) => {
              setForm((current) => (current ? { ...current, problem: confirmed.text } : { ...EMPTY_STORY, problem: confirmed.text }));
            }}
          />
          {form ? (
            <div ref={formRef} id={formId} className="space-y-6 border-t border-line pt-6">
              <div>
                <h4 className="text-lg font-semibold text-ink">Shape the story</h4>
                <p className="mt-1 max-w-measure text-sm text-ink-muted">
                  Your confirmed wording is under Problem. Move what you did, decided and learned into their own parts.
                </p>
              </div>
              <SaveProblem error={create.error} />
              <StoryFields
                value={form}
                onChange={(change) => {
                  setForm((current) => (current ? change(current) : current));
                }}
                errors={errors}
                pillars={pillars}
                idPrefix={formId}
              />
              <div className="flex flex-wrap gap-2 border-t border-line pt-5">
                <Button disabled={create.isPending} onClick={save}>
                  {create.isPending ? 'Saving…' : 'Save story'}
                </Button>
                <Button variant="ghost" onClick={close}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}
          <UnsavedChangesGuard when={hasUnsavedText(intake) || form !== undefined} subject="this story" />
        </>
      )}
    </Panel>
  );
}

function NewPov({ onClose }: { onClose: () => void }) {
  const { pillars } = usePillarDirectory();
  const formId = useId();
  const formRef = useRef<HTMLDivElement>(null);
  const [intake, setIntake] = useIntakeState();
  const [form, setForm] = useState<PovForm>({ pillar: '', statement: '', context: '', confidentiality: 'anonymized' });
  const [confirmed, setConfirmed] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const create = useApiMutation('post', '/api/povs', {
    invalidates: invalidations,
    successToast: 'Position saved and approved.',
    errorToast: 'Couldn’t save the position.',
    handles: HANDLED,
  });
  const clientErrors = useMemo(() => validatePov(form), [form]);
  const errors = mergeErrors(showErrors ? clientErrors : NO_ERRORS, saveErrors(create.error, 'pillar'));

  const save = () => {
    setShowErrors(true);
    if (hasErrors(clientErrors)) {
      requestAnimationFrame(() => {
        focusFirstInvalid(formRef.current);
      });
      return;
    }
    create.mutate({ body: formToPovIn(form) }, { onSuccess: onClose });
  };

  return (
    <Panel title="New position" dirty={hasUnsavedText(intake) || confirmed} onClose={onClose}>
      {(close) => (
        <>
          <div ref={formRef} className="space-y-6">
            <p className="max-w-measure text-ink-muted">
              Your view on a pillar topic, in your words. Say it roughly; the wording you confirm is what drafts quote.
            </p>
            <PovPillarField value={form} onChange={setForm} errors={errors} pillars={pillars} />
            <IntakeFlow
              purpose="pov"
              pillar={form.pillar === '' ? null : form.pillar}
              label="Your position, in your own words"
              headingLevel={4}
              state={intake}
              onStateChange={setIntake}
              maxLength={1000}
              onConfirm={(wording) => {
                setForm((current) => ({ ...current, statement: wording.text }));
                setConfirmed(true);
              }}
            />
            {confirmed ? (
              <div id={formId} className="space-y-6 border-t border-line pt-6">
                <SaveProblem error={create.error} />
                {errors.statement ? <p className="text-sm font-medium text-danger">{errors.statement}</p> : null}
                <PovFields value={form} onChange={setForm} errors={errors} pillars={pillars} idPrefix={formId} showStatement={false} />
                <div className="flex flex-wrap gap-2 border-t border-line pt-5">
                  <Button disabled={create.isPending} onClick={save}>
                    {create.isPending ? 'Saving…' : 'Save position'}
                  </Button>
                  <Button variant="ghost" onClick={close}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
          <UnsavedChangesGuard when={hasUnsavedText(intake) || confirmed} subject="this position" />
        </>
      )}
    </Panel>
  );
}

function EditStory({ entry, onClose }: { entry: Entry<StoryEntry>; onClose: () => void }) {
  const { pillars } = usePillarDirectory();
  const formId = useId();
  const formRef = useRef<HTMLDivElement>(null);
  // The version the edit started from; "Take theirs" moves it to the latest one.
  const [base, setBase] = useState(entry);
  const initial = useMemo(() => storyToForm(base.item), [base.item]);
  const [form, setForm] = useState<StoryForm>(initial);
  const [showErrors, setShowErrors] = useState(false);
  const conflict = useConflict();
  const latest = useApiQuery(
    {
      queryKey: queryKeys.stories.detail(entry.item.id),
      path: '/api/stories/{story_id}',
      init: { params: { path: { story_id: entry.item.id } } },
    },
    { enabled: conflict.open },
  );
  const save = useApiMutation('put', '/api/stories/{story_id}', {
    invalidates: invalidations,
    updates: () => queryKeys.stories.detail(entry.item.id),
    successToast: 'Story saved.',
    errorToast: 'Couldn’t save the story.',
    handles: HANDLED,
  });
  const clientErrors = useMemo(() => validateStory(form), [form]);
  const errors = mergeErrors(showErrors ? clientErrors : NO_ERRORS, saveErrors(save.error, 'pillars'));
  const dirty = !sameDraft(form, initial);
  const theirs = conflict.open && latest.data && latest.data.etag !== base.etag ? latest.data : undefined;

  const send = (etag: string | null) => {
    setShowErrors(true);
    if (hasErrors(clientErrors)) {
      requestAnimationFrame(() => {
        focusFirstInvalid(formRef.current);
      });
      return;
    }
    save.mutate(
      { params: { path: { story_id: entry.item.id } }, body: formToStoryIn(form), ifMatch: etag },
      {
        onSuccess: () => {
          conflict.close();
          onClose();
        },
        onError: (error) => {
          if (conflict.show(error)) void latest.refetch();
        },
      },
    );
  };

  return (
    <Panel title="Edit story" dirty={dirty} onClose={onClose}>
      {(close) => (
        <>
          <div ref={formRef} className="space-y-6">
            <p className="max-w-measure text-sm text-ink-muted">Saving checks it for confidential details again and asks the classifier for a new verdict.</p>
            <SaveProblem error={save.error} />
            <StoryFields value={form} onChange={setForm} errors={errors} pillars={pillars} idPrefix={formId} />
            <div className="flex flex-wrap gap-2 border-t border-line pt-5">
              <Button
                disabled={save.isPending || !dirty}
                onClick={() => {
                  send(base.etag);
                }}
              >
                {save.isPending ? 'Saving…' : 'Save story'}
              </Button>
              <Button variant="ghost" onClick={close}>
                Cancel
              </Button>
            </div>
          </div>
          <ConflictDialog
            {...conflict}
            subject="This story"
            mine={describeStory(form)}
            theirs={theirs ? describeStory(storyToForm(theirs.item)) : undefined}
            saving={save.isPending}
            onKeepMine={() => {
              send(conflict.conflict?.etag ?? latest.data?.etag ?? base.etag);
            }}
            onTakeTheirs={() => {
              if (latest.data) {
                setBase(latest.data);
                setForm(storyToForm(latest.data.item));
              }
              save.reset();
              conflict.close();
            }}
          />
          <UnsavedChangesGuard when={dirty} subject="this story" />
        </>
      )}
    </Panel>
  );
}

function EditPov({ entry, onClose }: { entry: Entry<PovEntry>; onClose: () => void }) {
  const { pillars } = usePillarDirectory();
  const formId = useId();
  const formRef = useRef<HTMLDivElement>(null);
  const [base, setBase] = useState(entry);
  const initial = useMemo(() => povToForm(base.item), [base.item]);
  const [form, setForm] = useState<PovForm>(initial);
  const [showErrors, setShowErrors] = useState(false);
  const conflict = useConflict();
  const latest = useApiQuery(
    { queryKey: queryKeys.povs.detail(entry.item.id), path: '/api/povs/{pov_id}', init: { params: { path: { pov_id: entry.item.id } } } },
    { enabled: conflict.open },
  );
  const save = useApiMutation('put', '/api/povs/{pov_id}', {
    invalidates: invalidations,
    updates: () => queryKeys.povs.detail(entry.item.id),
    successToast: 'Position saved.',
    errorToast: 'Couldn’t save the position.',
    handles: HANDLED,
  });
  const clientErrors = useMemo(() => validatePov(form), [form]);
  const errors = mergeErrors(showErrors ? clientErrors : NO_ERRORS, saveErrors(save.error, 'pillar'));
  const dirty = !sameDraft(form, initial);
  const theirs = conflict.open && latest.data && latest.data.etag !== base.etag ? latest.data : undefined;

  const send = (etag: string | null) => {
    setShowErrors(true);
    if (hasErrors(clientErrors)) {
      requestAnimationFrame(() => {
        focusFirstInvalid(formRef.current);
      });
      return;
    }
    save.mutate(
      { params: { path: { pov_id: entry.item.id } }, body: formToPovIn(form, base.item), ifMatch: etag },
      {
        onSuccess: () => {
          conflict.close();
          onClose();
        },
        onError: (error) => {
          if (conflict.show(error)) void latest.refetch();
        },
      },
    );
  };

  return (
    <Panel title="Edit position" dirty={dirty} onClose={onClose}>
      {(close) => (
        <>
          <div ref={formRef} className="space-y-6">
            <SaveProblem error={save.error} />
            <PovFields value={form} onChange={setForm} errors={errors} pillars={pillars} idPrefix={formId} />
            <div className="flex flex-wrap gap-2 border-t border-line pt-5">
              <Button
                disabled={save.isPending || !dirty}
                onClick={() => {
                  send(base.etag);
                }}
              >
                {save.isPending ? 'Saving…' : 'Save position'}
              </Button>
              <Button variant="ghost" onClick={close}>
                Cancel
              </Button>
            </div>
          </div>
          <ConflictDialog
            {...conflict}
            subject="This position"
            mine={describePov(form)}
            theirs={theirs ? describePov(povToForm(theirs.item)) : undefined}
            saving={save.isPending}
            onKeepMine={() => {
              send(conflict.conflict?.etag ?? latest.data?.etag ?? base.etag);
            }}
            onTakeTheirs={() => {
              if (latest.data) {
                setBase(latest.data);
                setForm(povToForm(latest.data.item));
              }
              save.reset();
              conflict.close();
            }}
          />
          <UnsavedChangesGuard when={dirty} subject="this position" />
        </>
      )}
    </Panel>
  );
}

/** Writing a new story or position (through the intake flow), or editing one. */
export function Composer({ mode, onClose }: { mode: ComposerMode; onClose: () => void }) {
  switch (mode.kind) {
    case 'new-story':
      return <NewStory onClose={onClose} />;
    case 'new-pov':
      return <NewPov onClose={onClose} />;
    case 'edit-story':
      return <EditStory key={mode.entry.item.id} entry={mode.entry} onClose={onClose} />;
    case 'edit-pov':
      return <EditPov key={mode.entry.item.id} entry={mode.entry} onClose={onClose} />;
  }
}
