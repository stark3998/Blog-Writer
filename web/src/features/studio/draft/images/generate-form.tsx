import { ImagePlus } from 'lucide-react';
import { useState, type SyntheticEvent } from 'react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Textarea } from '@/components/ui/textarea';
import { formatCost } from '@/features/studio/piece';
import { confidentialFindings, isProblem } from '@/lib/api/problems';

import type { GenerateRequest } from '../api/illustrations';
import { DIRECTION_MAX, estimateUsd, ILLUSTRATION_COUNTS, type ImagePrices } from '../model/illustrations';
import { ActionProblem } from '../review/action-problem';

/** Where a blocklist hit sits, in words (the term itself is never sent back). */
const FIELD_WORDS: Readonly<Record<string, string>> = {
  title: 'the title',
  excerpt: 'the summary',
  pillar: 'the pillar’s name',
  artBrief: 'the pillar’s art brief (Library > Brand kit)',
  direction: 'the art direction',
};

function fieldWords(field: string): string {
  return FIELD_WORDS[field] ?? (field.startsWith('headings[') ? 'a section heading' : 'the article’s details');
}

/** A refused start: a blocklist hit in the direction shows on the field; the rest above the button. */
function directionError(problem: unknown): string | undefined {
  return confidentialFindings(problem).some((finding) => finding.field === 'direction')
    ? 'Part of this matches your blocklist. Rephrase it.'
    : undefined;
}

function StartProblem({ problem }: { problem: unknown }) {
  if (!problem) return null;
  if (isProblem(problem, 'confidential_text')) {
    const parts = [...new Set(confidentialFindings(problem).map((finding) => fieldWords(finding.field)))].filter(
      (words) => words !== FIELD_WORDS.direction,
    );
    if (parts.length === 0) return null;
    return (
      <Alert variant="error">
        <AlertTitle>Couldn’t start the illustrations.</AlertTitle>
        <AlertDescription>
          <p>{`Your blocklist matches ${parts.join(', ')}. Rephrase it, then try again; the term itself is never shown.`}</p>
          {problem.requestId ? (
            <p className="text-xs">
              Request ID <code className="font-mono">{problem.requestId}</code>
            </p>
          ) : null}
        </AlertDescription>
      </Alert>
    );
  }
  return <ActionProblem error={problem} title="Couldn’t start the illustrations." />;
}

export interface GenerateFormProps {
  /** A run can't start now: the button stays off. */
  disabled: boolean;
  /** Why, under the button (left out when the panel already says it). */
  reason: string | undefined;
  pending: boolean;
  prices: ImagePrices | undefined;
  /** The last refusal. */
  problem: unknown;
  onGenerate: (request: GenerateRequest) => void;
  /** The direction changed: an old refusal no longer applies. */
  onEdit: () => void;
}

/** How many images (a hero plus up to two for the text) and an optional art direction. */
export function GenerateForm({ disabled, reason, pending, prices, problem, onGenerate, onEdit }: GenerateFormProps) {
  const [count, setCount] = useState<number>(1);
  const [direction, setDirection] = useState('');
  const estimate = prices ? formatCost(estimateUsd(prices, count)) : undefined;

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (disabled || pending) return;
    const text = direction.replace(/\s+/g, ' ').trim();
    onGenerate({ count, direction: text === '' ? null : text.slice(0, DIRECTION_MAX) });
  };

  return (
    <form className="grid gap-4" onSubmit={submit} noValidate aria-label="Make illustrations">
      <Fieldset
        legend="How many images"
        description="The first is a landscape hero; the others are square, for the text."
      >
        {(group) => (
          <SegmentedControl
            {...group}
            disabled={disabled || pending}
            value={String(count)}
            onValueChange={(value) => {
              setCount(Number(value));
            }}
            fullWidth
          >
            {ILLUSTRATION_COUNTS.map((value) => (
              <SegmentedControlItem key={value} value={String(value)}>
                {String(value)}
              </SegmentedControlItem>
            ))}
          </SegmentedControl>
        )}
      </Fieldset>
      <Field
        label="Art direction (optional)"
        description="A few words, like “warmer light, a bridge rather than a gate”. Checked against your blocklist."
        error={directionError(problem)}
      >
        <Textarea
          rows={2}
          maxLength={DIRECTION_MAX}
          disabled={disabled || pending}
          value={direction}
          onChange={(event) => {
            setDirection(event.target.value);
            if (problem) onEdit();
          }}
        />
      </Field>
      <StartProblem problem={problem} />
      <div className="grid gap-2">
        <Button type="submit" disabled={pending || disabled} className="justify-self-start">
          <ImagePlus aria-hidden="true" />
          {pending ? 'Starting…' : 'Make illustrations'}
        </Button>
        {reason ? (
          <p className="text-sm text-ink-muted">{reason}</p>
        ) : disabled ? null : estimate ? (
          <p className="text-sm text-ink-muted">{`About ${estimate}, a little more if an image needs a second try.`}</p>
        ) : null}
      </div>
    </form>
  );
}
