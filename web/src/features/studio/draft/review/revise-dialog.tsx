import { useState, type SyntheticEvent, type RefObject } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { confidentialFindings } from '@/lib/api/problems';

import { ActionProblem } from './action-problem';
import { useReturnFocus } from './return-focus';
import { MarkedText } from './marked-text';

const INSTRUCTION_MAX = 1000;

const SUGGESTIONS = [
  'Tighten the introduction and get to the point faster.',
  'Add a concrete example from my own experience where the draft stays general.',
  'Make the conclusion more actionable.',
  'Cut repetition so it reads about 20% shorter.',
] as const;

export interface ReviseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRevise: (instruction: string) => Promise<unknown>;
  returnFocus?: RefObject<HTMLElement | null>;
}

/**
 * Request a revision (`POST .../revise`): the pipeline rewrites the draft from the brief and
 * the instruction, then runs the gates again. The editor is read-only until it hands back.
 */
export function ReviseDialog({ open, onOpenChange, onRevise, returnFocus }: ReviseDialogProps) {
  const focus = useReturnFocus(returnFocus);
  const [instruction, setInstruction] = useState('');
  const [error, setError] = useState<string>();
  const [problem, setProblem] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const flagged = confidentialFindings(problem).filter((finding) => finding.field === 'instruction');

  const close = () => {
    setInstruction('');
    setError(undefined);
    setProblem(undefined);
    onOpenChange(false);
  };

  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    const text = instruction.trim();
    if (text === '') {
      setError('Say what to change.');
      return;
    }
    setPending(true);
    setProblem(undefined);
    try {
      await onRevise(text);
      close();
    } catch (failure) {
      setProblem(failure);
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return;
        if (next) onOpenChange(true);
        else close();
      }}
    >
      <DialogContent className="max-w-xl" {...focus}>
        <DialogHeader>
          <DialogTitle>Request a revision</DialogTitle>
          <DialogDescription>
            The pipeline rewrites the draft from the brief and your instruction, then runs the gates again. Edits you
            made are kept as a version.
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={(event) => void submit(event)} noValidate>
          <Field
            label="What should change?"
            description="Plain words. It goes to the model, so the blocklist is checked first."
            error={error ?? (flagged.length > 0 ? 'Part of this matches the blocklist. Rephrase it.' : undefined)}
            required
          >
            <Textarea
              rows={4}
              maxLength={INSTRUCTION_MAX}
              value={instruction}
              onChange={(event) => {
                setInstruction(event.target.value);
                setError(undefined);
              }}
            />
          </Field>
          {flagged.length > 0 ? <MarkedText text={instruction} findings={flagged} label="Your instruction" /> : null}
          <div className="flex flex-wrap gap-2" role="group" aria-label="Suggested instructions">
            {SUGGESTIONS.map((suggestion) => (
              <Button
                key={suggestion}
                type="button"
                size="sm"
                variant="secondary"
                className="h-auto whitespace-normal py-1.5 text-left"
                onClick={() => {
                  setInstruction(suggestion);
                  setError(undefined);
                }}
              >
                {suggestion}
              </Button>
            ))}
          </div>
          {problem && flagged.length === 0 ? <ActionProblem error={problem} title="Couldn’t start the revision." /> : null}
          <DialogFooter>
            <Button variant="secondary" disabled={pending} onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? 'Starting…' : 'Revise the draft'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
