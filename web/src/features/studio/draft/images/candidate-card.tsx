import { CornerDownRight, FileText, PencilLine, Star, Trash2 } from 'lucide-react';
import { useId, useRef, useState, type SyntheticEvent } from 'react';
import { flushSync } from 'react-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/field';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { formatCost } from '@/features/studio/piece';
import { confidentialFindings, isProblem, problemMessage } from '@/lib/api/problems';
import { mediaUrl } from '@/lib/urls';

import { ALT_TEXT_MAX, altTextProblem } from '../editor/alt-text-popover';
import { UnsavedChangesError } from '../review/use-article-commands';
import {
  ROLE_LABELS,
  suggestedHeading,
  type CandidateUse,
  type DraftHeading,
  type Illustration,
} from '../model/illustrations';

/** What an action on a card came to: done, handed to the conflict dialog, or refused. */
export type ActionOutcome =
  | { readonly kind: 'done' }
  | { readonly kind: 'conflict' }
  | { readonly kind: 'failed'; readonly error: unknown };

export type CardAction = 'hero' | 'insert' | 'alt' | 'discard';

/** One sentence for a refused action: unsaved edits say so, the rest is the problem's sentence. */
export function actionSentence(error: unknown): string {
  return error instanceof UnsavedChangesError ? error.message : problemMessage(error);
}

const HEADING_INDENT: Readonly<Record<number, string>> = { 2: 'pl-2.5', 3: 'pl-5', 4: 'pl-8' };

function altError(error: unknown): string {
  if (isProblem(error, 'confidential_text') || confidentialFindings(error).length > 0) {
    return 'Part of this matches your blocklist. Rephrase it.';
  }
  return actionSentence(error);
}

interface AltTextFormProps {
  initial: string;
  pending: boolean;
  onSave: (altText: string) => Promise<ActionOutcome>;
  onClose: () => void;
}

function AltTextForm({ initial, pending, onSave, onClose }: AltTextFormProps) {
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string>();

  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    const value = text.replace(/\s+/g, ' ').trim();
    const problem = altTextProblem(value);
    if (problem) {
      setError(problem);
      return;
    }
    if (value === initial) {
      onClose();
      return;
    }
    const outcome = await onSave(value);
    if (outcome.kind === 'failed') setError(altError(outcome.error));
    else onClose();
  };

  return (
    <form className="grid gap-2" onSubmit={(event) => void submit(event)} noValidate>
      <Field
        label="Alt text"
        description="One plain sentence for readers who can’t see the image. It changes wherever the image is used."
        error={error}
      >
        <Textarea
          rows={3}
          maxLength={ALT_TEXT_MAX}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            setError(undefined);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          {pending ? 'Saving…' : 'Save alt text'}
        </Button>
        <Button size="sm" variant="ghost" disabled={pending} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

interface InsertFormProps {
  headings: readonly DraftHeading[];
  section: string | null | undefined;
  pending: boolean;
  onInsert: (heading: DraftHeading) => Promise<ActionOutcome>;
  onClose: (inserted: boolean) => void;
}

function InsertForm({ headings, section, pending, onInsert, onClose }: InsertFormProps) {
  // The server inserts after the first heading with that text, so a repeated heading is one choice.
  const options = headings.filter((heading, index) => headings.findIndex((other) => other.source === heading.source) === index);
  const suggested = suggestedHeading(options, section);
  const [choice, setChoice] = useState(suggested === undefined ? '' : (options[suggested]?.source ?? ''));
  const [error, setError] = useState<string>();

  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    // Matched by text: the list follows the saved draft, so a heading may have moved meanwhile.
    const heading = options.find((option) => option.source === choice);
    if (!heading) {
      setError('Choose the heading the image goes after.');
      return;
    }
    const outcome = await onInsert(heading);
    if (outcome.kind === 'failed') setError(actionSentence(outcome.error));
    else onClose(outcome.kind === 'done');
  };

  if (options.length === 0) {
    return (
      <div className="grid gap-2 text-sm text-ink-muted">
        <p>The draft has no section headings yet. Add one (level 2 to 4) to place an image after it.</p>
        <Button
          size="sm"
          variant="ghost"
          className="justify-self-start"
          onClick={() => {
            onClose(false);
          }}
        >
          Close
        </Button>
      </div>
    );
  }

  return (
    <form className="grid gap-2" onSubmit={(event) => void submit(event)} noValidate>
      <Field
        label="After this heading"
        description="A new version of the text: run the gates again before you approve."
        error={error}
      >
        {(control) => (
          <Select
            value={choice}
            onValueChange={(value) => {
              setChoice(value);
              setError(undefined);
            }}
          >
            <SelectTrigger {...control}>
              <SelectValue placeholder="Choose a heading" />
            </SelectTrigger>
            <SelectContent>
              {options.map((heading) => (
                <SelectItem key={heading.source} value={heading.source} className={HEADING_INDENT[heading.level]}>
                  {heading.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          {pending ? 'Inserting…' : 'Insert'}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={pending}
          onClick={() => {
            onClose(false);
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}

export interface CandidateCardProps {
  illustration: Illustration;
  use: CandidateUse;
  headings: readonly DraftHeading[];
  /** Hero, insert and alt text: where the owner writes. */
  canEdit: boolean;
  /** Discard: any status but archived. */
  canDiscard: boolean;
  /** Another image action is running. */
  busy: boolean;
  /** This card's running action. */
  pending: CardAction | null;
  onHero: () => void;
  onInsert: (heading: DraftHeading) => Promise<ActionOutcome>;
  onAlt: (altText: string) => Promise<ActionOutcome>;
  onDiscard: () => void;
}

/**
 * One candidate: the image (from `/api/media/<name>`, same origin), what it was asked to show,
 * how it is used, its cost and alt text, and what can be done with it.
 */
export function CandidateCard({
  illustration,
  use,
  headings,
  canEdit,
  canDiscard,
  busy,
  pending,
  onHero,
  onInsert,
  onAlt,
  onDiscard,
}: CandidateCardProps) {
  const titleId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const altButton = useRef<HTMLButtonElement>(null);
  const insertButton = useRef<HTMLButtonElement>(null);
  const [form, setForm] = useState<'alt' | 'insert' | null>(null);
  const { media } = illustration;
  const cost = formatCost(illustration.costUsd);
  const inUse = use.hero || use.inBody;

  /** Closes the open form, then puts focus back on what opened it (or the title, once that is gone). */
  const close = (target: 'alt' | 'insert' | 'title') => {
    flushSync(() => {
      setForm(null);
    });
    const element = target === 'alt' ? altButton.current : target === 'insert' ? insertButton.current : null;
    element?.focus();
    if (!element || document.activeElement !== element) titleRef.current?.focus();
  };

  return (
    <article aria-labelledby={titleId} className="grid gap-3">
      <img
        src={mediaUrl(media.name)}
        alt={illustration.altText}
        width={media.width ?? undefined}
        height={media.height ?? undefined}
        loading="lazy"
        decoding="async"
        draggable={false}
        className="h-auto w-full rounded-md border border-line bg-sunken"
      />
      <div className="grid gap-1.5">
        <h4 id={titleId} ref={titleRef} tabIndex={-1} className="font-medium text-ink outline-none">
          {illustration.promptSummary}
        </h4>
        <ul aria-label="About this image" className="flex flex-wrap items-center gap-1.5">
          <li>
            <Badge>{ROLE_LABELS[illustration.role]}</Badge>
          </li>
          {use.hero ? (
            <li>
              <Badge variant="success">
                <Star aria-hidden="true" />
                Hero image
              </Badge>
            </li>
          ) : null}
          {use.inBody ? (
            <li>
              <Badge variant="outline">
                <FileText aria-hidden="true" />
                In the draft
              </Badge>
            </li>
          ) : null}
          {cost ? <li className="text-xs text-ink-muted">{`Cost ${cost}`}</li> : null}
        </ul>
      </div>

      {form === 'alt' ? (
        <AltTextForm
          initial={illustration.altText}
          pending={pending === 'alt'}
          onSave={onAlt}
          onClose={() => {
            close('alt');
          }}
        />
      ) : (
        <p className="text-sm text-ink-muted">
          <span className="font-medium text-ink">Alt text: </span>
          {illustration.altText}
        </p>
      )}

      {form === 'insert' ? (
        <InsertForm
          headings={headings}
          section={illustration.section}
          pending={pending === 'insert'}
          onInsert={onInsert}
          onClose={(inserted) => {
            close(inserted ? 'title' : 'insert');
          }}
        />
      ) : null}

      {form === null && (canEdit || canDiscard) ? (
        <div className="flex flex-wrap gap-2">
          {canEdit && !use.hero ? (
            <Button size="sm" variant="secondary" disabled={busy} onClick={onHero}>
              <Star aria-hidden="true" />
              {pending === 'hero' ? 'Setting…' : 'Use as hero'}
            </Button>
          ) : null}
          {canEdit && !use.inBody ? (
            <Button
              ref={insertButton}
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setForm('insert');
              }}
            >
              <CornerDownRight aria-hidden="true" />
              Insert after a heading
            </Button>
          ) : null}
          {canEdit ? (
            <Button
              ref={altButton}
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                setForm('alt');
              }}
            >
              <PencilLine aria-hidden="true" />
              Edit alt text
            </Button>
          ) : null}
          {canDiscard && !inUse ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onDiscard}>
              <Trash2 aria-hidden="true" />
              {pending === 'discard' ? 'Discarding…' : 'Discard'}
            </Button>
          ) : null}
        </div>
      ) : null}
      {form === null && canDiscard && inUse ? (
        <p className="text-xs text-ink-subtle">
          {use.hero && use.inBody
            ? 'Used as the hero and in the draft, so it can’t be discarded.'
            : use.hero
              ? 'Used as the hero, so it can’t be discarded. Use the title card first.'
              : 'In the draft, so it can’t be discarded. Remove it from the text first.'}
        </p>
      ) : null}
    </article>
  );
}
