import type { ReactNode } from 'react';

import { Field, Fieldset, type FieldControlProps } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/cn';

import { WEEKDAY_LABELS, WEEKDAYS, type Weekday } from '../format';
import type { WeeklyTimeDraft } from './cadence-draft';

/** A day of the week, as a list (its trigger takes the Field's wiring). */
export function WeekdaySelect({
  value,
  onChange,
  control,
  className,
}: {
  value: Weekday;
  onChange: (day: Weekday) => void;
  control: FieldControlProps;
  className?: string;
}) {
  return (
    <Select
      value={value}
      onValueChange={(next) => {
        const day = WEEKDAYS.find((candidate) => candidate === next);
        if (day) onChange(day);
      }}
    >
      <SelectTrigger {...control} className={cn('min-w-36', className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {WEEKDAYS.map((day) => (
          <SelectItem key={day} value={day}>
            {WEEKDAY_LABELS[day]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** A time of day (`HH:MM`, 24-hour, local to the cadence's time zone). */
export function TimeInput({
  value,
  onChange,
  className,
  ...props
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <Input
      type="time"
      step={60}
      value={value}
      className={cn('max-w-36', className)}
      onChange={(event) => {
        onChange(event.target.value);
      }}
      {...props}
    />
  );
}

/** A weekly moment ("Friday at 12:00"): a group with a day and a time. */
export function WeeklyTimeField({
  legend,
  description,
  value,
  onChange,
  error,
  dayError,
  timeError,
}: {
  legend: string;
  description?: ReactNode;
  value: WeeklyTimeDraft;
  onChange: (value: WeeklyTimeDraft) => void;
  /** A problem with the pair as a whole ("at least 48 hours before…"). */
  error?: string;
  dayError?: string;
  timeError?: string;
}) {
  return (
    <Fieldset legend={legend} description={description} error={error}>
      <div className="flex flex-wrap items-start gap-3">
        <Field label={`${legend}: day`} hideLabel error={dayError}>
          {(control) => (
            <WeekdaySelect
              control={control}
              value={value.day}
              onChange={(day) => {
                onChange({ ...value, day });
              }}
            />
          )}
        </Field>
        <Field label={`${legend}: time`} hideLabel error={timeError}>
          <TimeInput
            value={value.time}
            onChange={(time) => {
              onChange({ ...value, time });
            }}
          />
        </Field>
      </div>
    </Fieldset>
  );
}

/** A whole number with its unit after it ("120 minutes"). */
export function NumberField({
  label,
  description,
  unit,
  value,
  onChange,
  error,
  min,
  max,
  hideLabel = false,
  className,
}: {
  label: string;
  description?: ReactNode;
  unit?: string;
  /** Keeps the label for screen readers only (a row of controls with one visible heading). */
  hideLabel?: boolean;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  min?: number;
  max?: number;
  className?: string;
}) {
  return (
    <Field label={label} description={description} error={error} hideLabel={hideLabel} className={className}>
      {(control) => (
        <div className="flex items-center gap-2">
          <Input
            {...control}
            type="number"
            inputMode="numeric"
            step={1}
            min={min}
            max={max}
            value={value}
            className="max-w-28"
            onChange={(event) => {
              onChange(event.target.value);
            }}
          />
          {unit ? <span className="text-sm text-ink-muted">{unit}</span> : null}
        </div>
      )}
    </Field>
  );
}
