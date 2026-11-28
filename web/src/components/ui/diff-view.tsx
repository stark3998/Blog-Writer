import { diffArrays, diffWordsWithSpace } from 'diff';
import { useMemo, type ComponentProps } from 'react';

import { cn } from '@/lib/cn';

/** A run of words inside a changed paragraph. */
export interface DiffSegment {
  readonly kind: 'same' | 'added' | 'removed';
  readonly text: string;
}

export type DiffBlock =
  | { readonly kind: 'same' | 'added' | 'removed'; readonly text: string }
  /** A paragraph edited in place: its words, kept, added and removed. */
  | { readonly kind: 'changed'; readonly segments: readonly DiffSegment[] }
  /** Unchanged paragraphs folded away (see `context`). */
  | { readonly kind: 'skipped'; readonly count: number };

export interface DiffSummary {
  readonly changed: number;
  readonly added: number;
  readonly removed: number;
}

/** Word diffs give up beyond this many edits and show the paragraph as replaced instead. */
const MAX_WORD_EDITS = 400;

/**
 * Splits Markdown into blocks at blank lines, keeping a fenced code block (which may contain
 * blank lines) in one piece.
 */
export function splitBlocks(text: string): string[] {
  const blocks: string[] = [];
  let current: string[] = [];
  let fence: string | undefined;
  const flush = () => {
    if (current.length > 0) blocks.push(current.join('\n'));
    current = [];
  };
  for (const line of text.replace(/\r\n?/g, '\n').split('\n')) {
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (fence !== undefined) {
      current.push(line);
      const trimmed = line.trim();
      if (marker?.startsWith(fence) && trimmed === marker) fence = undefined;
      continue;
    }
    if (marker !== undefined) {
      fence = marker;
      current.push(line);
      continue;
    }
    if (line.trim() === '') {
      flush();
      continue;
    }
    current.push(line);
  }
  flush();
  return blocks;
}

function wordSegments(before: string, after: string): DiffSegment[] | undefined {
  const changes = diffWordsWithSpace(before, after, { maxEditLength: MAX_WORD_EDITS });
  if (changes === undefined) return undefined;
  return changes.map((change) => ({
    kind: change.added ? 'added' : change.removed ? 'removed' : 'same',
    text: change.value,
  }));
}

/** Keeps `context` unchanged blocks next to each change and folds the rest into a count. */
function foldUnchanged(blocks: readonly DiffBlock[], context: number): DiffBlock[] {
  const result: DiffBlock[] = [];
  let index = 0;
  while (index < blocks.length) {
    const block = blocks[index];
    if (block?.kind !== 'same') {
      if (block) result.push(block);
      index += 1;
      continue;
    }
    let end = index;
    while (blocks[end]?.kind === 'same') end += 1;
    const run = blocks.slice(index, end);
    const keepStart = index === 0 ? 0 : context;
    const keepEnd = end === blocks.length ? 0 : context;
    if (run.length > keepStart + keepEnd + 1) {
      result.push(...run.slice(0, keepStart));
      result.push({ kind: 'skipped', count: run.length - keepStart - keepEnd });
      result.push(...run.slice(run.length - keepEnd));
    } else {
      result.push(...run);
    }
    index = end;
  }
  return result;
}

/**
 * A block-level diff of two Markdown texts (jsdiff): paragraphs kept, added and removed, and a
 * word-level diff inside each paragraph that was edited in place.
 */
export function diffBlocks(before: string, after: string, { context }: { context?: number } = {}): DiffBlock[] {
  const blocks: DiffBlock[] = [];
  let pendingRemoved: string[] = [];
  const flushRemoved = () => {
    for (const text of pendingRemoved) blocks.push({ kind: 'removed', text });
    pendingRemoved = [];
  };
  for (const change of diffArrays(splitBlocks(before), splitBlocks(after))) {
    if (change.removed) {
      pendingRemoved.push(...change.value);
      continue;
    }
    if (change.added) {
      // Pair each added paragraph with a removed one just before it: an edit in place.
      for (const text of change.value) {
        const previous = pendingRemoved.shift();
        const segments = previous === undefined ? undefined : wordSegments(previous, text);
        if (previous !== undefined && segments === undefined) blocks.push({ kind: 'removed', text: previous });
        blocks.push(segments ? { kind: 'changed', segments } : { kind: 'added', text });
      }
      flushRemoved();
      continue;
    }
    flushRemoved();
    for (const text of change.value) blocks.push({ kind: 'same', text });
  }
  flushRemoved();
  return context === undefined ? blocks : foldUnchanged(blocks, Math.max(0, Math.trunc(context)));
}

export function summarizeDiff(blocks: readonly DiffBlock[]): DiffSummary {
  const count = (kind: DiffBlock['kind']) => blocks.filter((block) => block.kind === kind).length;
  return { changed: count('changed'), added: count('added'), removed: count('removed') };
}

function plural(count: number, one: string, many: string): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}

/** "2 paragraphs changed, 1 added." */
export function describeDiff({ changed, added, removed }: DiffSummary): string {
  const parts = [
    changed > 0 ? `${plural(changed, 'paragraph', 'paragraphs')} changed` : undefined,
    added > 0 ? `${changed > 0 ? String(added) : plural(added, 'paragraph', 'paragraphs')} added` : undefined,
    removed > 0
      ? `${changed > 0 || added > 0 ? String(removed) : plural(removed, 'paragraph', 'paragraphs')} removed`
      : undefined,
  ].filter((part) => part !== undefined);
  return parts.length === 0 ? 'No changes.' : `${parts.join(', ')}.`;
}

/** Removed text is struck through and added words underlined, so neither depends on colour. */
const removedClass = 'bg-danger-soft text-ink line-through decoration-danger';
const addedClass = 'bg-success-soft text-ink underline decoration-success decoration-2 underline-offset-2';
/** A whole added paragraph is marked by its rule and tint rather than an underline on every line. */
const addedBlockClass = 'bg-success-soft text-ink no-underline';

function Segment({ segment }: { segment: DiffSegment }) {
  switch (segment.kind) {
    case 'same':
      return segment.text;
    case 'removed':
      return (
        <del className={cn('rounded-[0.1875rem]', removedClass)}>
          <span className="sr-only">removed </span>
          {segment.text}
        </del>
      );
    case 'added':
      return (
        <ins className={cn('rounded-[0.1875rem]', addedClass)}>
          <span className="sr-only">added </span>
          {segment.text}
        </ins>
      );
  }
}

const blockClass = 'block whitespace-pre-wrap break-words border-l-2 py-1.5 pl-3 pr-2';

function Block({ block }: { block: DiffBlock }) {
  switch (block.kind) {
    case 'same':
      return <p className={cn(blockClass, 'border-transparent text-ink-muted')}>{block.text}</p>;
    case 'removed':
      return (
        <del className={cn(blockClass, 'border-danger', removedClass)}>
          <span className="sr-only">Removed paragraph: </span>
          {block.text}
        </del>
      );
    case 'added':
      return (
        <ins className={cn(blockClass, 'border-success', addedBlockClass)}>
          <span className="sr-only">Added paragraph: </span>
          {block.text}
        </ins>
      );
    case 'changed':
      return (
        <p className={cn(blockClass, 'border-ink text-ink')}>
          <span className="sr-only">Changed paragraph: </span>
          {block.segments.map((segment, index) => (
            <Segment key={index} segment={segment} />
          ))}
        </p>
      );
    case 'skipped':
      return (
        <p className="py-1 pl-3.5 text-xs text-ink-subtle">{`${plural(block.count, 'unchanged paragraph', 'unchanged paragraphs')} not shown`}</p>
      );
  }
}

export interface DiffViewProps extends Omit<ComponentProps<'div'>, 'children'> {
  before: string;
  after: string;
  /** Unchanged paragraphs kept around each change; the others fold into a count. Default: all shown. */
  context?: number;
  /** Names the diff for screen readers, for example "Proposed changes to the selection". */
  label?: string;
  /** The line above the diff that counts the changes. Shown by default. */
  showSummary?: boolean;
}

/**
 * Shows how `after` differs from `before`, paragraph by paragraph: a removed paragraph is struck
 * through on red, an added one underlined on green, and a paragraph edited in place shows its
 * words that way. Never colour alone: strike-through and underline carry it too, and screen
 * readers hear "removed" and "added". For the selection-AI proposal and the conflict dialog.
 */
export function DiffView({
  before,
  after,
  context,
  label = 'Changes',
  showSummary = true,
  className,
  ...props
}: DiffViewProps) {
  const blocks = useMemo(() => diffBlocks(before, after, { context }), [before, after, context]);
  const summary = describeDiff(summarizeDiff(blocks));
  return (
    <div data-slot="diff-view" role="group" aria-label={label} className={cn('text-sm', className)} {...props}>
      {showSummary ? <p className="mb-2 text-xs font-medium text-ink-subtle">{summary}</p> : null}
      <div className="space-y-1">
        {blocks.map((block, index) => (
          <Block key={index} block={block} />
        ))}
      </div>
    </div>
  );
}
