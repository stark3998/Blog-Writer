import { Sparkles } from 'lucide-react';
import { useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, Fieldset } from '@/components/ui/field';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Textarea } from '@/components/ui/textarea';
import { confidentialFindings, isProblem, problemMessage } from '@/lib/api/problems';

export type RewriteWhat = 'hooks' | 'text' | 'carousel';

export interface RewriteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What to rewrite when the dialog opens. */
  initialWhat: RewriteWhat;
  /** Slides can be rewritten only for carousels and image cards. */
  slides: boolean;
  approved: boolean;
  pending: boolean;
  error: unknown;
  onSubmit: (what: RewriteWhat, instruction: string | undefined) => void;
}

const MAX_INSTRUCTION = 1000;

/**
 * Asks the writer to rewrite the hooks, the text or the slides, with an optional instruction
 * (`POST /api/posts/{id}/regenerate`). The instruction is checked against the blocklist first; a
 * match is pointed at without repeating the term.
 */
export function RewriteDialog({
  open,
  onOpenChange,
  initialWhat,
  slides,
  approved,
  pending,
  error,
  onSubmit,
}: RewriteDialogProps) {
  const [what, setWhat] = useState<RewriteWhat>(initialWhat);
  const [instruction, setInstruction] = useState('');
  const findings = confidentialFindings(error).filter((finding) => finding.field === 'instruction');
  const instructionError =
    findings.length > 0
      ? 'Part of this instruction matches the blocklist. Rephrase it.'
      : isProblem(error, 'validation_failed')
        ? 'Keep the instruction under 1,000 characters.'
        : undefined;
  const otherError = error && instructionError === undefined && !isProblem(error, 'confidential_text') ? error : undefined;

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = instruction.trim();
    onSubmit(what, trimmed === '' ? undefined : trimmed);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <form onSubmit={submit} className="grid gap-5">
          <DialogHeader>
            <DialogTitle>Rewrite with the writer</DialogTitle>
            <DialogDescription>
              {approved
                ? 'The post goes back to Draft while it is rewritten. Approve it again afterwards.'
                : 'The post is rewritten in the background; this page updates when it is done.'}
            </DialogDescription>
          </DialogHeader>
          <Fieldset legend="What to rewrite">
            {(group) => (
              <SegmentedControl
                {...group}
                value={what}
                onValueChange={(value) => {
                  if (value === 'hooks' || value === 'text' || value === 'carousel') setWhat(value);
                }}
                fullWidth
              >
                <SegmentedControlItem value="hooks">Hooks</SegmentedControlItem>
                <SegmentedControlItem value="text">Text</SegmentedControlItem>
                {slides ? <SegmentedControlItem value="carousel">Slides</SegmentedControlItem> : null}
              </SegmentedControl>
            )}
          </Fieldset>
          <Field
            label="Instruction"
            description="Optional. Say what to change, for example “lead with the 2,000-app number”."
            error={instructionError}
          >
            <Textarea
              value={instruction}
              maxLength={MAX_INSTRUCTION}
              onChange={(event) => {
                setInstruction(event.target.value);
              }}
              className="min-h-24"
            />
          </Field>
          {otherError ? (
            <Alert variant="error">
              <AlertDescription>
                <p>{problemMessage(otherError)}</p>
              </AlertDescription>
            </Alert>
          ) : null}
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              <Sparkles aria-hidden="true" />
              {pending ? 'Starting…' : 'Rewrite'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
