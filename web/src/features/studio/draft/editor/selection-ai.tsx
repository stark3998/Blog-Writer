import { posToDOMRect, type Editor } from '@tiptap/core';
import type { Transaction } from '@tiptap/pm/state';
import { Sparkles } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type SyntheticEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { DiffView } from '@/components/ui/diff-view';
import { Field } from '@/components/ui/field';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { confidentialFindings } from '@/lib/api/problems';
import { apiRequest } from '@/lib/api/request';
import { toastSuccess } from '@/lib/api/toast';
import { useAppData } from '@/lib/app-data';

import { useDraftEnvironment } from '../environment';
import type { DocRange } from '../model/spans';
import type { SelectionOut } from '../model/types';
import { ActionProblem } from '../review/action-problem';
import { MarkedText } from '../review/marked-text';

import { definitionsMarkdown, insertFragment, parseFragment, type CapturedSelection } from './fragments';

/** The route's limits (`SelectionIn`: text 1-8000, instruction 1-500). */
export const SELECTION_MAX_CHARS = 8000;
const INSTRUCTION_MAX = 500;

const QUICK_INSTRUCTIONS = [
  'Tighten it',
  'Make it more specific',
  'Simplify the wording',
  'Make it sound more like me',
] as const;

const NO_PROPOSAL: Readonly<Record<NonNullable<SelectionOut['reason']>, string>> = {
  refused: 'The model declined to rewrite this passage. Try a different instruction.',
  empty: 'The model returned nothing usable. Try a different instruction.',
  blocklist_hit: 'The proposal mentioned a blocklisted term, so it was withheld. Try a different instruction.',
};

type Phase =
  | { readonly kind: 'ask' }
  | { readonly kind: 'asking' }
  | { readonly kind: 'proposal'; readonly proposal: string; readonly reviewOnly: boolean }
  | { readonly kind: 'none'; readonly message: string }
  | { readonly kind: 'failed'; readonly error: unknown };

export interface SelectionAiProps {
  editor: Editor;
  articleId: string;
  /** The selection when the owner asked; `null` while closed. */
  capture: CapturedSelection | null;
  onClose: () => void;
}

function excerpt(text: string, max = 220): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/**
 * Selection AI (`POST .../selection`): the owner says how the passage should change, the model
 * proposes (nothing is saved), the popover shows the proposal as a block-level diff, and Accept
 * replaces the selection through the normal edit and autosave path (one undo step).
 */
export function SelectionAi({ editor, articleId, capture, onClose }: SelectionAiProps) {
  const { api } = useAppData();
  const env = useDraftEnvironment();
  const [instruction, setInstruction] = useState('');
  const [instructionError, setInstructionError] = useState<string>();
  const [phase, setPhase] = useState<Phase>({ kind: 'ask' });
  const [accepting, setAccepting] = useState(false);
  const [acceptProblem, setAcceptProblem] = useState<string>();
  const range = useRef<DocRange | null>(null);
  const stale = useRef(false);
  const abort = useRef<AbortController | null>(null);

  // Follow the selection's place while the popover is open; a deleted range can't be replaced.
  useEffect(() => {
    if (!capture) return undefined;
    range.current = capture.range;
    stale.current = false;
    const onTransaction = ({ transaction }: { transaction: Transaction }) => {
      const current = range.current;
      if (!transaction.docChanged || !current) return;
      const from = transaction.mapping.mapResult(current.from, 1);
      const to = transaction.mapping.mapResult(current.to, -1);
      if (from.deleted && to.deleted) stale.current = true;
      range.current = { from: from.pos, to: Math.max(from.pos, to.pos) };
    };
    editor.on('transaction', onTransaction);
    return () => {
      editor.off('transaction', onTransaction);
      abort.current?.abort();
    };
  }, [capture, editor]);

  const anchor = useMemo(
    () => ({
      current: {
        getBoundingClientRect: () => {
          const current = capture?.range;
          try {
            return current ? posToDOMRect(editor.view, current.from, current.to) : new DOMRect();
          } catch {
            return new DOMRect();
          }
        },
      },
    }),
    [capture, editor],
  );

  const close = () => {
    abort.current?.abort();
    setInstruction('');
    setInstructionError(undefined);
    setPhase({ kind: 'ask' });
    setAcceptProblem(undefined);
    onClose();
  };

  const ask = async (event?: SyntheticEvent) => {
    event?.preventDefault();
    if (!capture) return;
    const text = instruction.trim();
    if (text === '') {
      setInstructionError('Say how it should change.');
      return;
    }
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setPhase({ kind: 'asking' });
    try {
      const answer = await apiRequest(api, 'post', '/api/articles/{article_id}/selection', {
        params: { path: { article_id: articleId } },
        body: { text: capture.markdown, instruction: text },
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (answer.proposal === null || answer.proposal.trim() === '') {
        setPhase({ kind: 'none', message: NO_PROPOSAL[answer.reason ?? 'empty'] });
      } else {
        setPhase({ kind: 'proposal', proposal: answer.proposal, reviewOnly: answer.reviewOnly });
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setPhase({ kind: 'failed', error });
    }
  };

  const accept = async (proposal: string) => {
    const current = range.current;
    if (!capture || !current) return;
    if (stale.current) {
      setAcceptProblem('The selected text was deleted meanwhile, so there is nothing to replace. Select it again.');
      return;
    }
    setAccepting(true);
    setAcceptProblem(undefined);
    try {
      const fragment = await parseFragment(env.dialect, proposal, definitionsMarkdown(editor), env.timings.parseTimeoutMs);
      if (editor.isDestroyed || !editor.isEditable) return;
      insertFragment(editor, range.current ?? current, fragment, capture.inline);
      toastSuccess('Replaced the selection. Undo brings the original back.');
      close();
    } catch {
      setAcceptProblem('The proposal couldn’t be read as Markdown, so nothing was changed. Ask again.');
    } finally {
      setAccepting(false);
    }
  };

  const flagged = phase.kind === 'failed' ? confidentialFindings(phase.error) : [];
  const flaggedText = flagged.filter((finding) => finding.field === 'text');
  const flaggedInstruction = flagged.filter((finding) => finding.field === 'instruction');

  return (
    <Popover
      open={capture !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <PopoverAnchor virtualRef={anchor} />
      <PopoverContent
        side="bottom"
        align="start"
        className="grid w-[min(36rem,calc(100vw-1.5rem))] gap-4"
        aria-label="Rewrite the selection"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (!editor.isDestroyed) editor.commands.focus();
        }}
      >
        <p className="flex items-center gap-2 font-semibold text-ink">
          <Sparkles aria-hidden="true" className="size-4" />
          Rewrite the selection
        </p>
        {capture ? (
          <p className="line-clamp-3 border-l-2 border-line pl-3 font-serif text-sm italic text-ink-muted">
            {excerpt(capture.markdown)}
          </p>
        ) : null}

        {phase.kind === 'ask' || phase.kind === 'failed' || phase.kind === 'none' ? (
          <form className="grid gap-3" onSubmit={(event) => void ask(event)} noValidate>
            <Field
              label="How should it change?"
              error={instructionError ?? (flaggedInstruction.length > 0 ? 'Part of this matches the blocklist. Rephrase it.' : undefined)}
            >
              <Textarea
                rows={2}
                maxLength={INSTRUCTION_MAX}
                value={instruction}
                onChange={(event) => {
                  setInstruction(event.target.value);
                  setInstructionError(undefined);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void ask();
                }}
              />
            </Field>
            {flaggedInstruction.length > 0 ? <MarkedText text={instruction} findings={flaggedInstruction} label="Your instruction" /> : null}
            {flaggedText.length > 0 && capture ? (
              <>
                <p className="text-sm text-danger">The selection itself matches the blocklist. Rephrase that part by hand.</p>
                <MarkedText text={capture.markdown} findings={flaggedText} label="The selection" />
              </>
            ) : null}
            <div className="flex flex-wrap gap-2" role="group" aria-label="Quick instructions">
              {QUICK_INSTRUCTIONS.map((quick) => (
                <Button
                  key={quick}
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setInstruction(quick);
                    setInstructionError(undefined);
                  }}
                >
                  {quick}
                </Button>
              ))}
            </div>
            {phase.kind === 'none' ? (
              <p role="status" className="text-sm text-ink-muted">
                {phase.message}
              </p>
            ) : null}
            {phase.kind === 'failed' && flagged.length === 0 ? (
              <ActionProblem error={phase.error} title="Couldn’t get a proposal." />
            ) : null}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={close}>
                Cancel
              </Button>
              <Button type="submit">Propose a rewrite</Button>
            </div>
          </form>
        ) : null}

        {phase.kind === 'asking' ? (
          <div className="grid gap-3">
            <p role="status" className="text-sm text-ink-muted">
              Writing a proposal…
            </p>
            <Skeleton className="h-20" />
            <div className="flex justify-end">
              <Button variant="ghost" onClick={close}>
                Cancel
              </Button>
            </div>
          </div>
        ) : null}

        {phase.kind === 'proposal' && capture ? (
          <div className="grid gap-3">
            {phase.reviewOnly ? (
              <Alert variant="warn">
                <AlertDescription>
                  <p>A fallback model wrote this, so read it closely.</p>
                </AlertDescription>
              </Alert>
            ) : null}
            <DiffView
              before={capture.markdown}
              after={phase.proposal}
              label="Proposed changes to the selection"
              tabIndex={0}
              className="max-h-[45dvh] overflow-y-auto rounded-md border border-line bg-sheet p-3"
            />
            {acceptProblem ? (
              <p role="alert" className="text-sm text-danger">
                {acceptProblem}
              </p>
            ) : null}
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="ghost" disabled={accepting} onClick={close}>
                Discard
              </Button>
              <Button
                variant="secondary"
                disabled={accepting}
                onClick={() => {
                  setPhase({ kind: 'ask' });
                }}
              >
                Try another instruction
              </Button>
              <Button disabled={accepting} onClick={() => void accept(phase.proposal)}>
                {accepting ? 'Replacing…' : 'Accept'}
              </Button>
            </div>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
