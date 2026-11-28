import type { Ref } from 'react';

import { Field, Fieldset } from '@/components/ui/field';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/cn';

import { codePointLength, estimatePayloadUnits, foldIndex, formatCount, MAX_PAYLOAD_UNITS } from '../model/text';

/** The server's answer for the text on screen (`POST /api/posts/{id}/lint`), when it has one. */
export interface LiveMeasure {
  /** Escaped payload length in UTF-16 units; `undefined` until the lint answers for this text. */
  readonly payloadUnits?: number;
  /** The phone fold as a code-point index. */
  readonly foldIndex?: number;
}

export interface TextEditorProps {
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
  /** Why it is read-only, said under the label. */
  readOnlyReason?: string;
  measure: LiveMeasure;
  /** The pillar's approved hashtags, in their approved spelling. */
  approvedHashtags: readonly string[];
  /** The post's hashtags (a subset of the approved ones, at most three). */
  hashtags: readonly string[];
  onToggleHashtag: (tag: string, on: boolean) => void;
  textareaRef?: Ref<HTMLTextAreaElement>;
  error?: string;
}

const MAX_HASHTAGS = 3;

/** How full the payload is, in words and a bar (a state colour only when it means one). */
function Counter({ units, exact }: { units: number; exact: boolean }) {
  const over = units - MAX_PAYLOAD_UNITS;
  const tone = over > 0 ? 'danger' : units >= MAX_PAYLOAD_UNITS * 0.9 ? 'warn' : 'ink';
  const summary = `${formatCount(units)} of ${formatCount(MAX_PAYLOAD_UNITS)} characters`;
  return (
    <div className="grid gap-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
        <p className={cn('font-medium tabular-nums', over > 0 ? 'text-danger' : 'text-ink')}>
          {summary}
          {exact ? null : <span className="sr-only"> (estimate, checking)</span>}
        </p>
        <p className="text-ink-subtle">Counted as LinkedIn receives it, escapes included.</p>
      </div>
      <Progress
        value={Math.min(units, MAX_PAYLOAD_UNITS)}
        max={MAX_PAYLOAD_UNITS}
        tone={tone}
        size="sm"
        aria-label="Length of the post"
        getValueLabel={() => summary}
      />
      {over > 0 ? (
        <p className="text-sm font-medium text-danger">
          {`${formatCount(over)} over the limit. LinkedIn would cut the post short, so approval is blocked.`}
        </p>
      ) : null}
    </div>
  );
}

/** Where the fold falls and whether the hook ends before it. */
function FoldNote({ text, fold }: { text: string; fold: number }) {
  const total = codePointLength(text);
  if (total === 0) return null;
  const lead = text.length - text.trimStart().length;
  const firstBreak = text.indexOf('\n', lead);
  const hookEnd = codePointLength(text.slice(0, firstBreak === -1 ? text.length : firstBreak));
  if (fold >= total) {
    return <p className="text-sm text-ink-muted">The whole post shows without “see more”.</p>;
  }
  return (
    <p className="text-sm text-ink-muted">
      {`On a phone the feed shows ${formatCount(fold)} characters, then “see more”. `}
      {hookEnd > fold ? (
        <span className="font-medium text-warn">The first line runs past the fold.</span>
      ) : (
        'The first line ends before the fold.'
      )}
    </p>
  );
}

/**
 * The post text with its counter (the 3,000-character limit, counted on the escaped payload),
 * the fold, and the pillar's approved hashtags. 16 px text, so phones never zoom.
 */
export function TextEditor({
  value,
  onChange,
  readOnly,
  readOnlyReason,
  measure,
  approvedHashtags,
  hashtags,
  onToggleHashtag,
  textareaRef,
  error,
}: TextEditorProps) {
  const units = measure.payloadUnits ?? estimatePayloadUnits(value, hashtags);
  const fold = measure.foldIndex ?? foldIndex(value);
  const atLimit = hashtags.length >= MAX_HASHTAGS;

  return (
    <div className="grid gap-4">
      <Field
        label="Post text"
        description={readOnly ? readOnlyReason : 'What readers see. The first paragraph is the hook.'}
        error={error}
      >
        <Textarea
          ref={textareaRef}
          value={value}
          readOnly={readOnly}
          spellCheck
          onChange={(event) => {
            onChange(event.target.value);
          }}
          className="min-h-56 leading-relaxed read-only:bg-sunken"
        />
      </Field>
      <Counter units={units} exact={measure.payloadUnits !== undefined} />
      <FoldNote text={value} fold={fold} />
      {approvedHashtags.length > 0 ? (
        <Fieldset
          legend="Hashtags"
          description={`Up to ${MAX_HASHTAGS} of the pillar’s approved hashtags. Any other # word stays plain text.`}
        >
          {(group) => (
            <ToggleGroup
              {...group}
              type="multiple"
              variant="outline"
              size="sm"
              value={[...hashtags]}
              disabled={readOnly}
              onValueChange={(next) => {
                for (const tag of approvedHashtags) {
                  const was = hashtags.includes(tag);
                  const now = next.includes(tag);
                  if (was !== now) onToggleHashtag(tag, now);
                }
              }}
            >
              {approvedHashtags.map((tag) => (
                <ToggleGroupItem key={tag} value={tag} disabled={readOnly || (atLimit && !hashtags.includes(tag))}>
                  {`#${tag}`}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          )}
        </Fieldset>
      ) : null}
    </div>
  );
}
