import { useId, useState, type SyntheticEvent, type RefObject } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Fieldset } from '@/components/ui/field';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';

import type { Article, StartFrom } from '../model/types';

import { ActionProblem } from './action-problem';
import { useReturnFocus } from './return-focus';

const STARTS: readonly { readonly value: StartFrom; readonly label: string; readonly detail: string; readonly needsBrief: boolean }[] = [
  {
    value: 'research',
    label: 'From research',
    detail: 'Research the topic again, ask you a few questions, then write the draft.',
    needsBrief: false,
  },
  {
    value: 'interview',
    label: 'From the interview',
    detail: 'Keep the brief, ask you a few questions, then write the draft.',
    needsBrief: true,
  },
  {
    value: 'draft',
    label: 'From the draft',
    detail: 'Keep the brief and your answers, and write the draft again.',
    needsBrief: true,
  },
];

export interface PipelineDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  article: Article;
  onStart: (startFrom: StartFrom) => Promise<unknown>;
  returnFocus?: RefObject<HTMLElement | null>;
}

/** Start (or restart) the pipeline (`POST .../pipeline`), from research, the interview or the draft. */
export function PipelineDialog({ open, onOpenChange, article, onStart, returnFocus }: PipelineDialogProps) {
  const focus = useReturnFocus(returnFocus);
  const baseId = useId();
  const hasBrief = Boolean(article.briefId);
  const [startFrom, setStartFrom] = useState<StartFrom>('research');
  const [problem, setProblem] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const replacing = article.markdown.trim() !== '';
  const restart = article.status !== 'planned';

  const close = () => {
    setProblem(undefined);
    onOpenChange(false);
  };

  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    setPending(true);
    setProblem(undefined);
    try {
      await onStart(startFrom);
      close();
    } catch (error) {
      setProblem(error);
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
          <DialogTitle>{restart ? 'Restart the pipeline' : 'Start the pipeline'}</DialogTitle>
          <DialogDescription>
            The pipeline researches, interviews you, drafts, critiques and runs the gates, then hands the draft back for
            review. You can follow it here.
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-5" onSubmit={(event) => void submit(event)} noValidate>
          <Fieldset legend="Where to start">
            {(group) => (
              <RadioGroup
                {...group}
                value={startFrom}
                onValueChange={(value) => {
                  const match = STARTS.find((start) => start.value === value);
                  if (match) setStartFrom(match.value);
                }}
              >
                {STARTS.map((start) => {
                  const id = `${baseId}-${start.value}`;
                  const disabled = start.needsBrief && !hasBrief;
                  return (
                    <div key={start.value} className="flex items-start gap-3">
                      <RadioGroupItem id={id} value={start.value} disabled={disabled} className="mt-0.5" aria-describedby={`${id}-detail`} />
                      <div className="grid gap-0.5">
                        <Label htmlFor={id} className={disabled ? 'text-ink-subtle' : undefined}>
                          {start.label}
                        </Label>
                        <p id={`${id}-detail`} className="text-sm text-ink-muted">
                          {disabled ? 'Needs a research brief: start from research first.' : start.detail}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </RadioGroup>
            )}
          </Fieldset>
          {replacing ? (
            <Alert variant="warn">
              <AlertDescription>
                <p>The new draft replaces the current text. The current text stays in Versions.</p>
              </AlertDescription>
            </Alert>
          ) : null}
          {problem ? <ActionProblem error={problem} title="Couldn’t start the pipeline." /> : null}
          <DialogFooter>
            <Button variant="secondary" disabled={pending} onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? 'Starting…' : restart ? 'Restart' : 'Start'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
