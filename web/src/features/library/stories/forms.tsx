import { Plus, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { PillarThread } from '@/components/ui/pillar-badge';

import { StringListEditor } from '../components/string-list-editor';
import { itemErrors, type ErrorMap } from '../lib/errors';
import { CONFIDENTIALITY_HINT, CONFIDENTIALITY_LABEL } from '../lib/labels';
import { slugify } from '../lib/rules';
import type { Confidentiality, Pillar } from '../lib/types';
import { STORY_LIMITS, type MetricDraft, type PovForm, type StoryForm } from './model';

function ConfidentialityChoice({
  value,
  onChange,
  idPrefix,
}: {
  value: Confidentiality;
  onChange: (value: Confidentiality) => void;
  idPrefix: string;
}) {
  return (
    <Fieldset legend="Confidentiality" description="How the drafts may use it. There is no private level: private details are never stored.">
      {(group) => (
        <RadioGroup
          {...group}
          value={value}
          onValueChange={(next) => {
            onChange(next as Confidentiality);
          }}
        >
          {(['anonymized', 'public'] as const).map((option) => (
            <div key={option} className="flex items-start gap-3">
              <RadioGroupItem id={`${idPrefix}-${option}`} value={option} className="mt-0.5" />
              <div className="grid gap-0.5">
                <Label htmlFor={`${idPrefix}-${option}`}>{CONFIDENTIALITY_LABEL[option]}</Label>
                <p className="text-sm text-ink-muted">{CONFIDENTIALITY_HINT[option]}</p>
              </div>
            </div>
          ))}
        </RadioGroup>
      )}
    </Fieldset>
  );
}

export interface StoryFieldsProps {
  value: StoryForm;
  onChange: (change: (form: StoryForm) => StoryForm) => void;
  errors: ErrorMap;
  pillars: readonly Pillar[];
  /** Prefix for control ids, unique per form on the page. */
  idPrefix: string;
}

/** A story's parts: what happened, what you did and decided, the numbers, the lesson. */
export function StoryFields({ value, onChange, errors, pillars, idPrefix }: StoryFieldsProps) {
  const set = <K extends keyof StoryForm>(field: K, next: StoryForm[K]) => {
    onChange((current) => ({ ...current, [field]: next }));
  };
  const setMetric = (index: number, part: Partial<MetricDraft>) => {
    onChange((current) => ({ ...current, metrics: current.metrics.map((metric, position) => (position === index ? { ...metric, ...part } : metric)) }));
  };
  const text = (field: 'problem' | 'actions' | 'decisions' | 'lessons', label: string, description: string) => (
    <Field label={label} description={description} error={errors[field]}>
      <Textarea
        rows={4}
        maxLength={STORY_LIMITS.text}
        value={value[field]}
        onChange={(event) => {
          set(field, event.target.value);
        }}
      />
    </Field>
  );

  return (
    <div className="space-y-7">
      <Field label="Title" required description="A headline you will recognise later." error={errors.title}>
        <Input
          value={value.title}
          maxLength={STORY_LIMITS.title}
          onChange={(event) => {
            set('title', event.target.value);
          }}
        />
      </Field>
      {text('problem', 'Problem', 'The situation and what made it hard.')}
      {text('actions', 'What you did', 'Your part, in order.')}
      {text('decisions', 'Decisions and trade-offs', 'What you chose, what you gave up and why.')}

      <Fieldset
        legend="Metrics"
        description="Round each number into a range (“2,000+ apps”, “about 40%”) unless it is already public; then link the public page."
        error={errors.metrics}
      >
        {value.metrics.length > 0 ? (
          <ol className="space-y-3">
            {value.metrics.map((metric, index) => {
              const base = `metrics.${String(index)}`;
              const number = String(index + 1);
              return (
                <li key={index} className="grid gap-3 rounded-lg border border-line p-3 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_auto] sm:items-start">
                  <Field label={`Metric ${number}: what it measures`} error={errors[`${base}.label`]}>
                    <Input
                      value={metric.label}
                      maxLength={STORY_LIMITS.metricLabel}
                      placeholder="Apps migrated"
                      onChange={(event) => {
                        setMetric(index, { label: event.target.value });
                      }}
                    />
                  </Field>
                  <Field label={`Metric ${number}: value`} error={errors[`${base}.value`]}>
                    <Input
                      value={metric.value}
                      maxLength={STORY_LIMITS.metricValue}
                      placeholder="2,000+"
                      onChange={(event) => {
                        setMetric(index, { value: event.target.value });
                      }}
                    />
                  </Field>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="sm:mt-6.5"
                    aria-label={`Remove metric ${number}`}
                    onClick={() => {
                      onChange((current) => ({ ...current, metrics: current.metrics.filter((_, position) => position !== index) }));
                    }}
                  >
                    <X aria-hidden="true" />
                  </Button>
                  <Field
                    label="Rounded into a range"
                    orientation="horizontal"
                    className="sm:col-span-3"
                    description={metric.bucketed ? undefined : 'Exact numbers need a public source.'}
                  >
                    <Checkbox
                      checked={metric.bucketed}
                      onCheckedChange={(checked) => {
                        setMetric(index, { bucketed: checked === true });
                      }}
                    />
                  </Field>
                  {metric.bucketed ? null : (
                    <Field label={`Metric ${number}: public source`} className="sm:col-span-3" error={errors[`${base}.publicSource`]}>
                      <Input
                        type="url"
                        inputMode="url"
                        placeholder="https://"
                        value={metric.publicSource}
                        onChange={(event) => {
                          setMetric(index, { publicSource: event.target.value });
                        }}
                      />
                    </Field>
                  )}
                </li>
              );
            })}
          </ol>
        ) : null}
        <div>
          <Button
            variant="secondary"
            size="sm"
            disabled={value.metrics.length >= STORY_LIMITS.metrics}
            onClick={() => {
              onChange((current) => ({ ...current, metrics: [...current.metrics, { label: '', value: '', bucketed: true, publicSource: '' }] }));
            }}
          >
            <Plus aria-hidden="true" />
            Add a metric
          </Button>
        </div>
      </Fieldset>

      {text('lessons', 'What you learned', 'What you would do again, and what you would change.')}

      <Fieldset legend="Pillars" description="One to three." error={errors.pillars}>
        <div className="grid gap-3 sm:grid-cols-2">
          {pillars.map((pillar) => {
            const checked = value.pillars.includes(pillar.id);
            return (
              <div key={pillar.id} className="flex items-center gap-3">
                <Checkbox
                  id={`${idPrefix}-pillar-${pillar.id}`}
                  checked={checked}
                  disabled={!checked && value.pillars.length >= STORY_LIMITS.pillars}
                  onCheckedChange={(next) => {
                    onChange((current) => ({
                      ...current,
                      pillars: next === true ? [...current.pillars, pillar.id] : current.pillars.filter((id) => id !== pillar.id),
                    }));
                  }}
                />
                <Label htmlFor={`${idPrefix}-pillar-${pillar.id}`} className="flex items-center gap-2 pointer-coarse:min-h-11">
                  <PillarThread id={pillar.id} />
                  {pillar.name}
                </Label>
              </div>
            );
          })}
        </div>
      </Fieldset>

      <ConfidentialityChoice
        idPrefix={`${idPrefix}-confidentiality`}
        value={value.confidentiality}
        onChange={(next) => {
          set('confidentiality', next);
        }}
      />

      <StringListEditor
        legend="Tags"
        description="Optional words to find it by, such as “migration”."
        items={value.tags}
        onChange={(tags) => {
          set('tags', tags);
        }}
        addLabel="Add a tag"
        noun="tag"
        max={STORY_LIMITS.tags}
        normalize={(input) => slugify(input, STORY_LIMITS.tag)}
        itemErrors={itemErrors(errors, 'tags')}
        error={errors.tags}
      />
    </div>
  );
}

export interface PovFieldsProps {
  value: PovForm;
  onChange: (change: (form: PovForm) => PovForm) => void;
  errors: ErrorMap;
  pillars: readonly Pillar[];
  idPrefix: string;
  /** False while the statement comes from the intake flow above. */
  showStatement?: boolean;
}

/** A position's pillar, its statement in your words, optional context and confidentiality. */
export function PovFields({ value, onChange, errors, pillars, idPrefix, showStatement = true }: PovFieldsProps) {
  return (
    <div className="space-y-6">
      {showStatement ? (
        <Field label="Your position" required description="In your own words, as you would say it." error={errors.statement}>
          <Textarea
            rows={4}
            maxLength={STORY_LIMITS.statement}
            className="font-serif text-lg leading-prose"
            value={value.statement}
            onChange={(event) => {
              const statement = event.target.value;
              onChange((current) => ({ ...current, statement }));
            }}
          />
        </Field>
      ) : null}
      <Field label="Context" description="Optional: where the position comes from, or when it does not hold." error={errors.context}>
        <Textarea
          rows={3}
          maxLength={STORY_LIMITS.context}
          value={value.context}
          onChange={(event) => {
            const context = event.target.value;
            onChange((current) => ({ ...current, context }));
          }}
        />
      </Field>
      <ConfidentialityChoice
        idPrefix={`${idPrefix}-confidentiality`}
        value={value.confidentiality}
        onChange={(confidentiality) => {
          onChange((current) => ({ ...current, confidentiality }));
        }}
      />
      {/* The pillar comes first in the new-position flow; editing shows it here. */}
      {showStatement ? <PovPillarField value={value} onChange={onChange} errors={errors} pillars={pillars} /> : null}
    </div>
  );
}

/** The pillar of a position (a select: it is one of up to five). */
export function PovPillarField({
  value,
  onChange,
  errors,
  pillars,
}: {
  value: PovForm;
  onChange: (change: (form: PovForm) => PovForm) => void;
  errors: ErrorMap;
  pillars: readonly Pillar[];
}) {
  return (
    <Field label="Pillar" required error={errors.pillar} className="max-w-sm">
      {(control) => (
        <Select
          value={value.pillar}
          onValueChange={(pillar) => {
            onChange((current) => ({ ...current, pillar }));
          }}
        >
          <SelectTrigger {...control}>
            <SelectValue placeholder="Choose a pillar" />
          </SelectTrigger>
          <SelectContent>
            {pillars.map((pillar) => (
              <SelectItem key={pillar.id} value={pillar.id}>
                {pillar.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </Field>
  );
}
