import { useMemo, useState } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';

import { PairsEditor } from '../components/pairs-editor';
import { StringListEditor } from '../components/string-list-editor';
import { UnsavedChangesGuard } from '../components/unsaved-changes-guard';
import { sameDraft } from '../hooks/use-settings-draft';
import { hasErrors, itemErrors, mergeErrors, NO_ERRORS, serverFieldErrors } from '../lib/errors';
import { formatInteger, plural } from '../lib/format';
import type { Entry, StyleGuide } from '../lib/types';
import { GUIDE_LIMITS, GUIDE_VIEW_KEY, guideToBody, guideToDraft, validateGuide, type GuideDraft } from './model';

export interface GuideEditorProps {
  /** The version the edit starts from. */
  base: Entry<StyleGuide>;
  onCancel: () => void;
  /** The new version was saved (as `proposed`, waiting for approval). */
  onSaved: (guide: StyleGuide) => void;
}

/**
 * Edits the guide's text and rules. Saving never changes the guide in use: it creates a new
 * version that waits for the owner's approval, with its changes shown against the current one.
 */
export function GuideEditor({ base, onCancel, onSaved }: GuideEditorProps) {
  const initial = useMemo(() => guideToDraft(base.item), [base.item]);
  const [draft, setDraft] = useState<GuideDraft>(initial);
  const [showErrors, setShowErrors] = useState(false);
  const save = useApiMutation('post', '/api/voice/guide/{guide_id}/versions', {
    invalidates: [GUIDE_VIEW_KEY],
    successToast: (data) => `Saved as version ${String(data.item.version)}. Approve it to start using it.`,
    errorToast: 'Couldn’t save the new version.',
    handles: ['validation_failed', 'confidential_text', 'body_required'],
  });

  const dirty = !sameDraft(draft, initial);
  const clientErrors = useMemo(() => validateGuide(draft), [draft]);
  const errors = mergeErrors(showErrors ? clientErrors : NO_ERRORS, serverFieldErrors(save.error));
  const total = Object.keys(errors).length;
  const words = draft.body.trim() === '' ? 0 : draft.body.trim().split(/\s+/).length;
  const setRules = (part: Partial<GuideDraft['rules']>) => {
    setDraft((current) => ({ ...current, rules: { ...current.rules, ...part } }));
  };

  const submit = () => {
    setShowErrors(true);
    if (hasErrors(clientErrors)) return;
    save.mutate(
      { params: { path: { guide_id: base.item.id } }, body: guideToBody(draft) },
      {
        onSuccess: (data) => {
          onSaved(data.item);
        },
      },
    );
  };

  return (
    <div className="space-y-8 rounded-lg border border-line-strong p-4 sm:p-6">
      <div>
        <h4 className="font-serif text-xl font-medium text-ink">{`Editing version ${String(base.item.version)}`}</h4>
        <p className="mt-1 text-sm text-ink-muted">Saving creates a new version for you to approve. The guide in use stays as it is until then.</p>
      </div>
      {showErrors && total > 0 ? (
        <Alert variant="error">
          <AlertTitle>{`${plural(total, 'part needs', 'parts need')} a fix before saving.`}</AlertTitle>
          <AlertDescription>
            <p>Each one is marked where it is.</p>
          </AlertDescription>
        </Alert>
      ) : null}
      {save.error && !isProblem(save.error, ['validation_failed', 'confidential_text', 'body_required']) ? (
        <ProblemAlert error={save.error} title="Couldn’t save the new version." />
      ) : null}
      {isProblem(save.error, 'body_required') ? (
        <Alert variant="error">
          <AlertTitle>The guide cannot be empty.</AlertTitle>
        </Alert>
      ) : null}

      <Field
        label="The guide"
        description={`How you write, in your words: tone, structure, what you never do. ${formatInteger(words)} words.`}
        error={errors.body}
      >
        <Textarea
          value={draft.body}
          maxLength={GUIDE_LIMITS.body}
          rows={14}
          className="font-serif leading-prose"
          onChange={(event) => {
            const body = event.target.value;
            setDraft((current) => ({ ...current, body }));
          }}
        />
      </Field>

      <div className="grid gap-8 lg:grid-cols-2">
        <StringListEditor
          legend="Prefer"
          description="Habits drafts should follow."
          items={draft.rules.prefer}
          onChange={(prefer) => {
            setRules({ prefer });
          }}
          addLabel="Add a habit to prefer"
          noun="rule"
          max={GUIDE_LIMITS.rules}
          maxLength={GUIDE_LIMITS.rule}
          itemErrors={itemErrors(errors, 'rules.prefer')}
          error={errors['rules.prefer']}
        />
        <StringListEditor
          legend="Avoid"
          description="Habits drafts should steer clear of."
          items={draft.rules.avoid}
          onChange={(avoid) => {
            setRules({ avoid });
          }}
          addLabel="Add a habit to avoid"
          noun="rule"
          max={GUIDE_LIMITS.rules}
          maxLength={GUIDE_LIMITS.rule}
          itemErrors={itemErrors(errors, 'rules.avoid')}
          error={errors['rules.avoid']}
        />
      </div>

      <StringListEditor
        legend="Never use"
        description="Phrases a draft must not contain. Paste a list separated by commas or new lines."
        items={draft.rules.bannedPhrases}
        onChange={(bannedPhrases) => {
          setRules({ bannedPhrases });
        }}
        addLabel="Add a phrase to never use"
        noun="phrase"
        max={GUIDE_LIMITS.banned}
        maxLength={GUIDE_LIMITS.phrase}
        splitPaste
        itemErrors={itemErrors(errors, 'rules.bannedPhrases')}
        error={errors['rules.bannedPhrases']}
      />

      <PairsEditor
        legend="Vocabulary"
        description="Terms with a note on how you use them."
        rows={draft.rules.vocabulary}
        onChange={(vocabulary) => {
          setRules({ vocabulary });
        }}
        fromLabel="Term"
        toLabel="How you use it"
        addLabel="Add a term"
        noun="term"
        rowErrors={itemErrors(errors, 'rules.vocabulary')}
        error={errors['rules.vocabulary']}
        max={GUIDE_LIMITS.vocabulary}
        fromMaxLength={GUIDE_LIMITS.term}
        toMaxLength={GUIDE_LIMITS.note}
      />

      <div className="flex flex-wrap gap-2 border-t border-line pt-5">
        <Button disabled={save.isPending || !dirty} onClick={submit}>
          {save.isPending ? 'Saving…' : 'Save as a new version'}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        {!dirty ? <p className="self-center text-sm text-ink-muted">No changes yet.</p> : null}
      </div>

      <UnsavedChangesGuard when={dirty && !save.isSuccess} subject="the voice guide" />
    </div>
  );
}
