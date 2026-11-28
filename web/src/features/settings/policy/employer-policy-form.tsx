import { Plus, X } from 'lucide-react';
import { useEffect, useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, Fieldset, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import type { Versioned } from '@/lib/api/envelope';
import { confidentialFindings, fieldErrors, isProblem } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';

import { SaveBar, useVersionedDraft } from '../components/form-kit';
import { formatDateTime } from '../format';
import { settingsKeys, type Schemas } from '../queries';

type EmployerPolicy = Schemas['EmployerPolicy'];

const SHORT_LIMIT = 40;
const LONG_LIMIT = 300;
const TOPIC_LIMIT = 100;
const MAX_TOPICS = 100;
const CLEARANCE_MIN = 10;
const CLEARANCE_LIMIT = 500;

interface PolicyDraft {
  disclaimerShort: string;
  disclaimerLong: string;
  forbiddenTopics: string[];
  noEmployerBranding: boolean;
  aiUseConfirmed: boolean;
  clearanceStatement: string;
}

function toDraft(item: EmployerPolicy): PolicyDraft {
  return {
    disclaimerShort: item.disclaimerShort ?? '',
    disclaimerLong: item.disclaimerLong ?? '',
    forbiddenTopics: [...(item.forbiddenTopics ?? [])],
    noEmployerBranding: item.noEmployerBranding,
    aiUseConfirmed: item.aiUseConfirmed,
    clearanceStatement: item.clearanceStatement,
  };
}

function problemsOf(draft: PolicyDraft): Record<string, string> {
  const problems: Record<string, string> = {};
  if (draft.disclaimerShort.trim().length > SHORT_LIMIT) problems.disclaimerShort = `At most ${String(SHORT_LIMIT)} characters.`;
  if (draft.disclaimerLong.trim().length > LONG_LIMIT) problems.disclaimerLong = `At most ${String(LONG_LIMIT)} characters.`;
  const clearance = draft.clearanceStatement.trim().length;
  if (clearance < CLEARANCE_MIN) problems.clearanceStatement = `Write at least ${String(CLEARANCE_MIN)} characters.`;
  else if (clearance > CLEARANCE_LIMIT) problems.clearanceStatement = `At most ${String(CLEARANCE_LIMIT)} characters.`;
  if (draft.forbiddenTopics.length > MAX_TOPICS) problems.forbiddenTopics = `At most ${String(MAX_TOPICS)} topics.`;
  return problems;
}

function describe(draft: PolicyDraft): string {
  return [
    `Short disclaimer: ${draft.disclaimerShort || 'none'}`,
    `Article disclaimer: ${draft.disclaimerLong || 'none'}`,
    `Forbidden topics: ${draft.forbiddenTopics.join('; ') || 'none'}`,
    `No employer branding: ${draft.noEmployerBranding ? 'yes' : 'no'}`,
    `AI use confirmed: ${draft.aiUseConfirmed ? 'yes' : 'no'}`,
    `Clearance statement: ${draft.clearanceStatement}`,
  ].join('\n');
}

function Counter({ length, limit }: { length: number; limit: number }) {
  return <span className={length > limit ? 'text-danger' : undefined}>{`${String(length)} of ${String(limit)} characters.`}</span>;
}

export interface EmployerPolicyFormProps {
  source: Versioned<EmployerPolicy>;
  latest: Versioned<EmployerPolicy>;
  onReload: () => Promise<Versioned<EmployerPolicy> | undefined>;
}

/** The employer's social-media and AI-use policy: disclaimers, forbidden topics, branding, the AI-use confirmation and the clearance statement. */
export function EmployerPolicyForm({ source, latest, onReload }: EmployerPolicyFormProps) {
  const form = useVersionedDraft(source, toDraft);
  const { draft } = form;
  const conflict = useConflict();
  const [attempted, setAttempted] = useState(false);
  const [topic, setTopic] = useState('');
  const [topicNote, setTopicNote] = useState<string | undefined>();
  const formRef = useRef<HTMLFormElement>(null);
  const topicRef = useRef<HTMLInputElement>(null);
  const save = useApiMutation('put', '/api/kb/policy', {
    updates: () => settingsKeys.policy,
    invalidates: [settingsKeys.ownerInputs],
    successToast: 'Employer policy saved.',
    errorToast: 'Couldn’t save the employer policy.',
    handles: ['etag_mismatch', 'validation_failed', 'confidential_text'],
  });

  const clientErrors = attempted ? problemsOf(draft) : {};
  const serverErrors = fieldErrors(save.error);
  const findings = confidentialFindings(save.error);
  const flagged = (field: string) =>
    findings.some((finding) => finding.field === field || finding.field.startsWith(`${field}.`))
      ? 'This names something on your blocklist. Rephrase it.'
      : undefined;
  const error = (field: string) => clientErrors[field] ?? serverErrors[field] ?? flagged(field);
  const topicError = (index: number) =>
    serverErrors[`forbiddenTopics.${String(index)}`] ??
    (findings.some((finding) => finding.field === `forbiddenTopics.${String(index)}`)
      ? 'This names something on your blocklist.'
      : undefined);
  const refused = isProblem(save.error, ['validation_failed', 'confidential_text']);

  useEffect(() => {
    if (refused) focusFirstInvalid(formRef.current);
  }, [refused, save.error]);

  const update = (change: Partial<PolicyDraft>) => {
    if (save.isError) save.reset();
    form.setDraft((current) => ({ ...current, ...change }));
  };

  const addTopic = () => {
    const text = topic.trim();
    if (text === '') return;
    if (text.length > TOPIC_LIMIT) {
      setTopicNote(`A topic has at most ${String(TOPIC_LIMIT)} characters.`);
      return;
    }
    if (draft.forbiddenTopics.some((item) => item.toLocaleLowerCase() === text.toLocaleLowerCase())) {
      setTopicNote('That topic is already on the list.');
      return;
    }
    update({ forbiddenTopics: [...draft.forbiddenTopics, text] });
    setTopic('');
    setTopicNote(undefined);
    topicRef.current?.focus();
  };

  const send = (etag: string | null) => {
    save.mutate(
      {
        body: {
          disclaimerShort: draft.disclaimerShort.trim() === '' ? null : draft.disclaimerShort.trim(),
          disclaimerLong: draft.disclaimerLong.trim() === '' ? null : draft.disclaimerLong.trim(),
          forbiddenTopics: draft.forbiddenTopics,
          noEmployerBranding: draft.noEmployerBranding,
          aiUseConfirmed: draft.aiUseConfirmed,
          clearanceStatement: draft.clearanceStatement.trim(),
        },
        ifMatch: etag,
      },
      {
        onSuccess: (data) => {
          form.reset(data);
          setAttempted(false);
          conflict.close();
        },
        onError: (failure) => {
          if (conflict.show(failure)) void onReload();
        },
      },
    );
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (Object.keys(problemsOf(draft)).length > 0) {
      flushSync(() => {
        setAttempted(true);
      });
      focusFirstInvalid(formRef.current);
      return;
    }
    send(form.base.etag);
  };

  const confirmedAt = form.base.item.aiUseConfirmed ? form.base.item.aiUseConfirmedAt : null;

  return (
    <form ref={formRef} noValidate onSubmit={submit} aria-label="Employer policy" className="grid max-w-measure gap-8">
      {form.base.item.configured ? null : (
        <Alert variant="info">
          <AlertTitle>Not saved yet.</AlertTitle>
          <AlertDescription>
            <p>Until then, carousels and image cards render without a footer and the publishing gate warns.</p>
          </AlertDescription>
        </Alert>
      )}
      {isProblem(save.error, 'confidential_text') ? (
        <Alert variant="error">
          <AlertTitle>Some of this names something on your blocklist.</AlertTitle>
          <AlertDescription>
            <p>Rephrase the marked fields: policy text appears in posts and articles.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <Fieldset legend="Use of AI" description="Required before any anonymized story, answer or draft goes to a model.">
        <Field
          orientation="horizontal"
          label="My employer’s AI-use policy covers sending anonymized work details to Azure OpenAI, Claude (Anthropic is the processor) and Web IQ (no data processing agreement)."
          description={
            confirmedAt ? `Confirmed ${formatDateTime(confirmedAt)}.` : 'Until you confirm, research, triage and quick search still run.'
          }
          error={error('aiUseConfirmed')}
        >
          <Checkbox
            checked={draft.aiUseConfirmed}
            onCheckedChange={(checked) => {
              update({ aiUseConfirmed: checked === true });
            }}
          />
        </Field>
      </Fieldset>

      <Field
        label="Short disclaimer"
        description={
          <>
            On every carousel slide and image card, such as “Views are my own.” <Counter length={draft.disclaimerShort.trim().length} limit={SHORT_LIMIT} />
          </>
        }
        error={error('disclaimerShort')}
      >
        <Input
          value={draft.disclaimerShort}
          onChange={(event) => {
            update({ disclaimerShort: event.target.value });
          }}
        />
      </Field>
      <Field
        label="Article disclaimer"
        description={
          <>
            At the end of each published article. <Counter length={draft.disclaimerLong.trim().length} limit={LONG_LIMIT} />
          </>
        }
        error={error('disclaimerLong')}
      >
        <Textarea
          value={draft.disclaimerLong}
          onChange={(event) => {
            update({ disclaimerLong: event.target.value });
          }}
        />
      </Field>

      <Fieldset
        legend="Forbidden topics"
        description="Subjects the employer policy rules out. Drafts and posts are checked for them."
        error={error('forbiddenTopics')}
      >
        <div className="grid gap-3">
          {draft.forbiddenTopics.length > 0 ? (
            <ul aria-label="Forbidden topics" className="grid gap-2">
              {draft.forbiddenTopics.map((item, index) => {
                const problem = topicError(index);
                return (
                  <li key={item} className="grid gap-1">
                    <div className="flex items-center justify-between gap-3 rounded-md border border-line bg-sheet py-1 pl-3 pr-1">
                      <span className="min-w-0 break-words text-ink">{item}</span>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`Remove the topic “${item}”`}
                        onClick={() => {
                          update({ forbiddenTopics: draft.forbiddenTopics.filter((_, position) => position !== index) });
                        }}
                      >
                        <X aria-hidden="true" />
                      </Button>
                    </div>
                    {problem ? <p className="text-sm font-medium text-danger">{problem}</p> : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-ink-muted">None listed.</p>
          )}
          <div className="flex flex-wrap items-start gap-2">
            <Field label="Add a forbidden topic" hideLabel error={topicNote} className="min-w-0 flex-1">
              <Input
                ref={topicRef}
                placeholder="Add a topic"
                value={topic}
                onChange={(event) => {
                  setTopic(event.target.value);
                  setTopicNote(undefined);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    addTopic();
                  }
                }}
              />
            </Field>
            <Button variant="secondary" disabled={topic.trim() === ''} onClick={addTopic}>
              <Plus aria-hidden="true" />
              Add
            </Button>
          </div>
        </div>
      </Fieldset>

      <Field
        orientation="horizontal"
        label="Keep employer names and logos out"
        description="No employer branding in posts, carousels or articles."
        error={error('noEmployerBranding')}
      >
        <Switch
          checked={draft.noEmployerBranding}
          onCheckedChange={(checked) => {
            update({ noEmployerBranding: checked });
          }}
        />
      </Field>

      <Field
        label="Clearance statement"
        description={
          <>
            What you attest before an article publishes. <Counter length={draft.clearanceStatement.trim().length} limit={CLEARANCE_LIMIT} />
          </>
        }
        error={error('clearanceStatement')}
        required
      >
        <Textarea
          value={draft.clearanceStatement}
          onChange={(event) => {
            update({ clearanceStatement: event.target.value });
          }}
        />
      </Field>

      <SaveBar
        dirty={form.dirty}
        saving={save.isPending}
        paused={save.isPaused}
        saveLabel="Save the policy"
        allowClean={!form.base.item.configured}
        idleNote={form.base.item.configured ? undefined : 'Not saved yet.'}
        onDiscard={() => {
          setAttempted(false);
          save.reset();
          form.reset(form.base);
        }}
      />
      <ConflictDialog
        {...conflict}
        subject="The employer policy"
        mine={describe(draft)}
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
            setAttempted(false);
            conflict.close();
          });
        }}
      />
    </form>
  );
}
