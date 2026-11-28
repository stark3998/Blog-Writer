import { Plus, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';

import { usePillars } from '../piece/piece-queries';

import {
  AUDIENCE_MAX,
  cleanOwnerUrls,
  MAX_OWNER_URLS,
  ownerUrlProblem,
  problemsByField,
  RECENCY_OPTIONS,
  TOPIC_MAX,
  TOPIC_MIN,
  type ResearchRun,
} from './brief-model';
import { START_RUN_PROBLEMS, useStartRun } from './brief-queries';

/** What the form starts with. */
export interface RunDefaults {
  readonly topic: string;
  readonly pillar: string;
  readonly audience: string;
  readonly recencyDays: number;
  readonly ownerUrls: readonly string[];
}

export interface ResearchRunDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaults: RunDefaults;
  /** The run belongs to this article (and idea). */
  articleId?: string | undefined;
  ideaId?: string | null | undefined;
  /** A sentence under the description, about what this run is for (the pipeline's own brief, say). */
  note?: string | undefined;
  /** Open with an empty source link field focused: "Add sources". */
  focusSources?: boolean;
  /** Called with the started run. */
  onStarted?: (run: ResearchRun, jobId: string) => void;
}

interface UrlRow {
  readonly key: number;
  readonly value: string;
}

function RunForm({ defaults, articleId, ideaId, focusSources = false, onStarted, onDone }: Omit<ResearchRunDialogProps, 'open' | 'onOpenChange' | 'note'> & { onDone: () => void }) {
  const formRef = useRef<HTMLFormElement>(null);
  const pillars = usePillars();
  const start = useStartRun();
  const [topic, setTopic] = useState(defaults.topic.slice(0, TOPIC_MAX));
  const [pillar, setPillar] = useState(defaults.pillar);
  const [audience, setAudience] = useState(defaults.audience.slice(0, AUDIENCE_MAX));
  const [recency, setRecency] = useState(String(defaults.recencyDays));
  const [urls, setUrls] = useState<readonly UrlRow[]>(() => {
    const initial = defaults.ownerUrls.slice(0, MAX_OWNER_URLS).map((value, index) => ({ key: index + 1, value }));
    return focusSources && initial.length < MAX_OWNER_URLS ? [...initial, { key: initial.length + 1, value: '' }] : initial;
  });
  const [errors, setErrors] = useState<Readonly<Record<string, string>>>({});

  const server = isProblem(start.error, ['validation_failed', 'confidential_text']) ? problemsByField(start.error) : {};
  const shown = (field: string) => errors[field] ?? server[field];
  const recencyOptions = RECENCY_OPTIONS.some((option) => String(option.days) === recency)
    ? RECENCY_OPTIONS
    : [...RECENCY_OPTIONS, { days: Number(recency), label: `Last ${recency} days` }];

  const validate = (): Record<string, string> => {
    const problems: Record<string, string> = {};
    const trimmed = topic.trim();
    if (trimmed.length < TOPIC_MIN) problems.topic = 'Describe the topic in a few words.';
    else if (trimmed.length > TOPIC_MAX) problems.topic = `Keep the topic to ${String(TOPIC_MAX)} characters.`;
    if (!pillar) problems.pillar = 'Choose a pillar: research searches its trusted sites first.';
    if (audience.trim().length > AUDIENCE_MAX) problems.audience = `Keep it to ${String(AUDIENCE_MAX)} characters.`;
    const kept = urls.filter((item) => item.value.trim() !== '');
    kept.forEach((item) => {
      const problem = ownerUrlProblem(item.value);
      if (problem) problems[`ownerUrls.${String(urls.indexOf(item))}`] = problem;
    });
    return problems;
  };

  const submit = () => {
    const problems = validate();
    flushSync(() => {
      setErrors(problems);
    });
    if (Object.keys(problems).length > 0) {
      formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
      return;
    }
    const ownerUrls = cleanOwnerUrls(urls.map((item) => item.value));
    start.mutate(
      {
        body: {
          topic: topic.trim(),
          pillar,
          audience: audience.trim() === '' ? null : audience.trim(),
          recencyDays: Number(recency),
          ownerUrls,
          ...(articleId ? { articleId } : {}),
          ...(ideaId ? { ideaId } : {}),
        },
      },
      {
        onSuccess: (data) => {
          onStarted?.(data.run.item, data.jobId);
          onDone();
        },
      },
    );
  };

  const pillarList = pillars.data?.item.pillars ?? [];
  // The server names each source link by its place in the list it received (blanks and repeats removed).
  const sent = cleanOwnerUrls(urls.map((item) => item.value));
  const serverUrlProblem = (value: string) => {
    const index = sent.indexOf(value.trim());
    return index < 0 ? undefined : server[`ownerUrls.${String(index)}`];
  };

  return (
    <form
      ref={formRef}
      noValidate
      className="grid gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Field
        label="Topic"
        description="What the research should answer. A sentence works best."
        error={shown('topic')}
        required
      >
        <Textarea
          rows={2}
          value={topic}
          maxLength={TOPIC_MAX}
          className="min-h-16"
          onChange={(event) => {
            setTopic(event.target.value);
          }}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Pillar" error={shown('pillar')} required>
          {(control) => (
            <Select value={pillar} onValueChange={setPillar} disabled={pillars.isPending}>
              <SelectTrigger {...control}>
                <SelectValue placeholder={pillars.isPending ? 'Loading pillars…' : 'Choose a pillar'} />
              </SelectTrigger>
              <SelectContent>
                {pillarList.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="How recent" description="Older sources are left out." error={shown('recencyDays')}>
          {(control) => (
            <Select value={recency} onValueChange={setRecency}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {recencyOptions.map((option) => (
                  <SelectItem key={option.days} value={String(option.days)}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
      </div>
      <Field
        label="Audience"
        description="Who the piece is for. Leave it empty and research works it out."
        error={shown('audience')}
      >
        <Input
          value={audience}
          maxLength={AUDIENCE_MAX}
          autoComplete="off"
          onChange={(event) => {
            setAudience(event.target.value);
          }}
        />
      </Field>
      <Fieldset
        legend="Your sources"
        description={`Pages you want read first, https links only, up to ${String(MAX_OWNER_URLS)}.`}
      >
        <div className="grid gap-2">
          {urls.map((item, index) => (
            <div key={item.key} className="flex items-start gap-2">
              <Field
                label={`Source link ${String(index + 1)}`}
                hideLabel
                error={errors[`ownerUrls.${String(index)}`] ?? serverUrlProblem(item.value)}
                className="flex-1"
              >
                <Input
                  type="url"
                  inputMode="url"
                  autoComplete="off"
                  placeholder="https://"
                  value={item.value}
                  // Focus lands here when the dialog opens for "Add sources".
                  autoFocus={focusSources && index === urls.length - 1 && item.value === ''}
                  onChange={(event) => {
                    const value = event.target.value;
                    setUrls((current) => current.map((entry) => (entry.key === item.key ? { ...entry, value } : entry)));
                  }}
                />
              </Field>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove source link ${String(index + 1)}`}
                onClick={() => {
                  setUrls((current) => current.filter((entry) => entry.key !== item.key));
                }}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
          ))}
          {urls.length < MAX_OWNER_URLS ? (
            <Button
              variant="secondary"
              size="sm"
              className="justify-self-start"
              onClick={() => {
                setUrls((current) => [...current, { key: Math.max(0, ...current.map((entry) => entry.key)) + 1, value: '' }]);
              }}
            >
              <Plus aria-hidden="true" />
              Add a source link
            </Button>
          ) : null}
        </div>
      </Fieldset>

      {start.isError && isProblem(start.error, 'blocklist_not_configured') ? (
        <Alert variant="error">
          <AlertTitle>Research didn’t start.</AlertTitle>
          <AlertDescription>
            <p>Save your blocklist once first, so nothing confidential reaches a search. An empty list is fine.</p>
          </AlertDescription>
          <AlertActions>
            <Button asChild size="sm" variant="secondary">
              <Link to="/settings/policy">Open Blocklist & policy</Link>
            </Button>
          </AlertActions>
        </Alert>
      ) : null}
      {start.isError && !isProblem(start.error, START_RUN_PROBLEMS) ? (
        <ProblemAlert error={start.error} title="Research didn’t start." />
      ) : null}

      <DialogFooter>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={start.isPending}>
          {start.isPending ? 'Starting…' : 'Start research'}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * Starts a research run (`POST /api/research/runs`): topic, pillar, how recent, an optional
 * audience and up to ten source links read first. The blocklist checks the topic, the audience
 * and every link before anything leaves the app; a hit is shown at its field.
 */
export function ResearchRunDialog({ open, onOpenChange, note, ...props }: ResearchRunDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>New research run</DialogTitle>
          <DialogDescription>
            Research reads your sources first, then your pillar’s trusted sites and the open web. It takes a few
            minutes; you can leave this page.
          </DialogDescription>
          {note ? <p className="text-sm text-ink-muted">{note}</p> : null}
        </DialogHeader>
        <RunForm
          {...props}
          onDone={() => {
            onOpenChange(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
