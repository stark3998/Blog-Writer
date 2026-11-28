import { useId, useState, type SyntheticEvent, type RefObject } from 'react';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';

import { ApprovalMedia } from '../images/approval-media';
import { fromLocalInput, toLocalInput } from '../model/format';
import type { Article, GatesView } from '../model/types';

import { ActionProblem } from './action-problem';
import { useReturnFocus } from './return-focus';
import { Preflight, preflight } from './preflight';

const CORRECTION_MAX = 1000;

export interface ApproveRequest {
  readonly publishAt?: string;
  readonly correctionNote?: string;
  readonly slug?: string;
}

export interface ApproveDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  article: Article;
  gates: GatesView | undefined;
  onApprove: (request: ApproveRequest) => Promise<unknown>;
  returnFocus?: RefObject<HTMLElement | null>;
}

/**
 * Approve for publishing (`POST .../approve`): when it goes out (the next article slot by
 * default), a dated correction note for a live article, and a confirmed slug change for a
 * published one. What approving needs is listed first; the server has the last word.
 */
export function ApproveDialog({ open, onOpenChange, article, gates, onApprove, returnFocus }: ApproveDialogProps) {
  const focus = useReturnFocus(returnFocus);
  const timeId = useId();
  const [when, setWhen] = useState<'slot' | 'time'>('slot');
  const [time, setTime] = useState('');
  const [note, setNote] = useState('');
  const [moveConfirmed, setMoveConfirmed] = useState(false);
  const [errors, setErrors] = useState<{ time?: string; note?: string; move?: string }>({});
  const [problem, setProblem] = useState<unknown>();
  const [pending, setPending] = useState(false);

  const live = article.publish?.live ?? false;
  const publishedSlug = article.publish?.slug ?? null;
  const slug = article.meta?.slug ?? null;
  const renamed = publishedSlug !== null && slug !== null && slug !== publishedSlug;
  const items = preflight(article, gates);
  const blocked = items.some((item) => !item.ok);

  const reset = () => {
    setWhen('slot');
    setTime('');
    setNote('');
    setMoveConfirmed(false);
    setErrors({});
    setProblem(undefined);
  };

  const submit = async (event: SyntheticEvent) => {
    event.preventDefault();
    const next: typeof errors = {};
    const publishAt = when === 'time' ? fromLocalInput(time) : undefined;
    if (when === 'time' && !publishAt) next.time = 'Pick a date and time.';
    else if (publishAt && Date.parse(publishAt) < Date.now() - 60_000) next.time = 'Pick a time that hasn’t passed.';
    if (live && note.trim() === '') next.note = 'Say what the correction changes. Readers see a dated note.';
    if (renamed && !moveConfirmed) next.move = 'Confirm the new address first.';
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    setPending(true);
    setProblem(undefined);
    try {
      await onApprove({
        ...(publishAt ? { publishAt } : {}),
        ...(live ? { correctionNote: note.trim() } : {}),
        ...(renamed && slug ? { slug } : {}),
      });
      reset();
      onOpenChange(false);
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
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-xl" {...focus}>
        <DialogHeader>
          <DialogTitle>{live ? 'Approve the correction' : 'Approve for publishing'}</DialogTitle>
          <DialogDescription>
            {live
              ? 'The corrected version replaces the live page, with a dated note. The live page stays up meanwhile.'
              : 'Approved articles publish to your portfolio at the time you choose. You can pull it back until then.'}
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-5" onSubmit={(event) => void submit(event)} noValidate>
          <Preflight items={items} />
          <ApprovalMedia article={article} />
          <Fieldset legend="When it publishes">
            {(group) => (
              <RadioGroup
                {...group}
                value={when}
                onValueChange={(value) => {
                  setWhen(value === 'time' ? 'time' : 'slot');
                  if (value === 'time' && time === '') setTime(toLocalInput(new Date(Date.now() + 60 * 60_000)));
                }}
              >
                <div className="flex items-center gap-3">
                  <RadioGroupItem id={`${timeId}-slot`} value="slot" />
                  <Label htmlFor={`${timeId}-slot`}>At the next article slot</Label>
                </div>
                <div className="flex items-center gap-3">
                  <RadioGroupItem id={`${timeId}-time`} value="time" />
                  <Label htmlFor={`${timeId}-time`}>At a time I choose</Label>
                </div>
              </RadioGroup>
            )}
          </Fieldset>
          {when === 'time' ? (
            <Field label="Publish at" description="Your local time." error={errors.time}>
              <Input
                type="datetime-local"
                value={time}
                onChange={(event) => {
                  setTime(event.target.value);
                }}
              />
            </Field>
          ) : null}
          {live ? (
            <Field
              label="Correction note"
              description="Shown on the page with today’s date, so readers know what changed."
              error={errors.note}
              required
            >
              <Textarea
                rows={3}
                maxLength={CORRECTION_MAX}
                value={note}
                onChange={(event) => {
                  setNote(event.target.value);
                }}
              />
            </Field>
          ) : null}
          {renamed ? (
            <Field
              orientation="horizontal"
              label={`Move it to /blog/${slug}`}
              description={`The old address, /blog/${publishedSlug}, redirects to the new one.`}
              error={errors.move}
            >
              <Checkbox
                checked={moveConfirmed}
                onCheckedChange={(checked) => {
                  setMoveConfirmed(checked === true);
                }}
              />
            </Field>
          ) : null}
          {problem ? <ActionProblem error={problem} title="Couldn’t approve it." /> : null}
          <DialogFooter>
            <Button
              variant="secondary"
              disabled={pending}
              onClick={() => {
                reset();
                onOpenChange(false);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={pending || blocked}>
              {pending ? 'Approving…' : 'Approve'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
