import { useState, type SubmitEvent } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

import { DialogActions, ResponsiveDialog } from '../components/responsive-dialog';
import { FORMAT_LABEL, LANE_HINT, LANE_LABEL } from '../lib/labels';
import {
  blockMessage,
  dayOptions,
  type Lane,
  type MoveContext,
  type PlannedSlot,
  type PostFormat,
  type SlotContent,
} from '../lib/plan';
import type { PillarView } from '../lib/settings';
import { dayLabel, isLocalTime, slotLabel } from '../lib/time';

const LANES: readonly Lane[] = ['native', 'quick_take', 'derivative', 'repurpose'];
const FORMATS: readonly PostFormat[] = ['text', 'carousel', 'image_card', 'poll'];
const NO_PILLAR = 'none';

export interface SlotDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The slot to edit; `undefined` adds a new one. */
  slot: PlannedSlot | undefined;
  /** A post fills the slot: it sets the lane, pillar and format. */
  hasPost: boolean;
  /** The plan is approved: changing what a slot holds sends it back for approval. */
  approved: boolean;
  context: MoveContext;
  pillars: readonly PillarView[];
  imageCardEnabled: boolean;
  /** The day a new slot starts on. */
  initialDate: string;
  initialTime: string;
  onSave: (content: SlotContent) => void;
  saving: boolean;
}

/** Add a slot, or change a slot's day, time and contents. */
export function SlotDialog({ open, onOpenChange, slot, ...rest }: SlotDialogProps) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={slot ? 'Edit the slot' : 'Add a slot'}
      description={
        slot
          ? `Now ${slotLabel(slot.slot.localDate, slot.slot.localTime)}. Times are in the plan’s time zone.`
          : 'A post slot for this week. Times are in the plan’s time zone.'
      }
    >
      {/* Mounted only while open, so it starts from the slot each time. */}
      {open ? <SlotForm slot={slot} onCancel={() => { onOpenChange(false); }} {...rest} /> : null}
    </ResponsiveDialog>
  );
}

function SlotForm({
  slot,
  hasPost,
  approved,
  context,
  pillars,
  imageCardEnabled,
  initialDate,
  initialTime,
  onSave,
  saving,
  onCancel,
}: Omit<SlotDialogProps, 'open' | 'onOpenChange'> & { onCancel: () => void }) {
  const [date, setDate] = useState(slot?.slot.localDate ?? initialDate);
  const [time, setTime] = useState(slot?.slot.localTime ?? initialTime);
  const [lane, setLane] = useState<Lane>(slot?.lane ?? 'native');
  const [pillar, setPillar] = useState<string>(slot?.pillar ?? pillars[0]?.id ?? NO_PILLAR);
  const [format, setFormat] = useState<PostFormat>(slot?.format ?? 'text');
  const [title, setTitle] = useState(slot?.title ?? '');
  const [submitted, setSubmitted] = useState(false);

  const probe: PlannedSlot = slot ?? {
    id: 'new-slot',
    slot: { localDate: date, localTime: time, timezone: context.zone, utc: '', kind: 'custom' },
    postId: null,
    lane,
    pillar: pillar === NO_PILLAR ? null : pillar,
    format,
    title: null,
  };
  const timeValid = isLocalTime(time);
  // While the time is being typed, the days keep the last valid time's answer.
  const options = dayOptions(probe, context, timeValid ? time : (slot?.slot.localTime ?? initialTime));
  const chosen = options.find((option) => option.date === date);
  const formats = FORMATS.filter((value) => value !== 'image_card' || imageCardEnabled || format === 'image_card');

  // A slot left where it is may stay there, even when its time has passed.
  const unchanged = slot?.slot.localDate === date && slot.slot.localTime === time;
  const errors = {
    time: timeValid ? undefined : 'Enter a time as HH:MM, for example 10:00.',
    date: chosen?.block && !unchanged ? `${blockMessage(chosen.block)}. Pick another day.` : undefined,
    pillar:
      !hasPost && lane === 'native' && pillar === NO_PILLAR ? 'Pick a pillar for a native post.' : undefined,
    title: title.length > 300 ? 'Keep the working title under 300 characters.' : undefined,
  };
  const invalid = Object.values(errors).some(Boolean);
  const shown = submitted ? errors : { ...errors, pillar: undefined, title: undefined };
  const contentChanged =
    slot?.lane !== lane || (pillar === NO_PILLAR ? null : pillar) !== (slot.pillar ?? null) || format !== slot.format;

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitted(true);
    if (invalid) {
      const form = event.currentTarget;
      // After the re-render that marks the fields invalid.
      setTimeout(() => focusFirstInvalid(form), 0);
      return;
    }
    onSave({
      localDate: date,
      localTime: time,
      lane,
      pillar: pillar === NO_PILLAR ? null : pillar,
      format,
      title: title.trim() === '' ? null : title.trim(),
    });
  };

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Day" error={shown.date}>
          {(control) => (
            <Select value={date} onValueChange={setDate}>
              <SelectTrigger {...control}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((option) => (
                  <SelectItem
                    key={option.date}
                    value={option.date}
                    disabled={option.block !== undefined && option.date !== date}
                  >
                    {dayLabel(option.date)}
                    {option.block ? ` (${option.block.reason === 'past' ? 'passed' : 'taken'})` : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Time" description="24-hour clock" error={shown.time}>
          <Input type="time" value={time} onChange={(event) => { setTime(event.target.value); }} required />
        </Field>
      </div>

      {chosen && chosen.warnings.length > 0 && !chosen.block ? (
        <Alert variant="warn" role="status">
          <AlertDescription>
            {chosen.warnings.map((warning) => (
              <p key={warning.with.id}>{warning.message}. Allowed, but the two posts compete for attention.</p>
            ))}
          </AlertDescription>
        </Alert>
      ) : null}

      {hasPost ? (
        <p className="text-sm text-ink-muted">
          A post fills this slot, so its lane, pillar and format come from the post. Change them on the post’s page.
        </p>
      ) : (
        <>
          <Field label="Lane" description={LANE_HINT[lane]}>
            {(control) => (
              <Select value={lane} onValueChange={(value) => { setLane(value as Lane); }}>
                <SelectTrigger {...control}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {LANE_LABEL[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Pillar" error={shown.pillar}>
              {(control) => (
                <Select value={pillar} onValueChange={setPillar}>
                  <SelectTrigger {...control}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {pillars.map((option) => (
                      <SelectItem key={option.id} value={option.id}>
                        {option.name}
                      </SelectItem>
                    ))}
                    <SelectItem value={NO_PILLAR}>Off pillar</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </Field>
            <Field label="Format">
              {(control) => (
                <Select value={format} onValueChange={(value) => { setFormat(value as PostFormat); }}>
                  <SelectTrigger {...control}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {formats.map((value) => (
                      <SelectItem key={value} value={value}>
                        {FORMAT_LABEL[value]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
          </div>
        </>
      )}

      <Field label="Working title" description="Only for you: the post gets its real hook when it is written." error={shown.title}>
        <Input value={title} maxLength={300} onChange={(event) => { setTitle(event.target.value); }} />
      </Field>

      {approved && !hasPost && contentChanged ? (
        <Alert variant="info">
          <AlertDescription>
            <p>This plan is approved. Changing what a slot holds sends it back to you for approval.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <DialogActions>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={saving}>
          {saving ? 'Saving…' : slot ? 'Save slot' : 'Add slot'}
        </Button>
      </DialogActions>
    </form>
  );
}
