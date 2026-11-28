import { Plus, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field, Fieldset, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import type { Versioned } from '@/lib/api/envelope';
import { fieldErrors, isProblem } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';

import { SaveBar, errorAt, useVersionedDraft } from '../components/form-kit';
import { SettingsBlock } from '../components/settings-block';
import { WEEKDAY_LABELS, WEEKDAYS, formatHours, formatMinutes, type Weekday } from '../format';
import { settingsKeys } from '../queries';
import {
  MAX_SLOTS,
  cadenceProblems,
  newSlotKey,
  numberList,
  slotsByWeek,
  toCadenceBody,
  toCadenceDraft,
  type CadenceDraft,
  type CadenceSettings,
  type SlotDraft,
  type WeekMode,
  type WeeklyField,
  type WeeklyTimeDraft,
} from './cadence-draft';
import { NumberField, TimeInput, WeekdaySelect, WeeklyTimeField } from './controls';
import { WeekStrip } from './week-strip';

const TIME_ZONES: readonly string[] = (() => {
  try {
    return Intl.supportedValuesOf('timeZone');
  } catch {
    return [];
  }
})();

const MODES: readonly { value: WeekMode; label: string }[] = [
  { value: 'normal', label: 'Normal' },
  { value: 'light', label: 'Light' },
  { value: 'away', label: 'Away' },
];

/** What a field is called, for errors listed away from it. */
const FIELD_NAMES: Readonly<Record<string, string>> = {
  timezone: 'Time zone',
  planWeek: 'Weekly plan',
  planLeadMinutes: 'Plan preparation',
  researchStart: 'Research starts',
  interviewDue: 'Interview answers due',
  draftDay: 'Drafting starts',
  vetoNotice: 'Veto notice',
  articlePublish: 'Article publishes',
  minVetoLeadHours: 'Veto notice lead',
  businessHoursStart: 'Business hours',
  businessHoursEnd: 'Business hours',
  postSlots: 'Post slots',
  postsPerWeek: 'Posts per week',
  sameDayBlock: 'Two posts on a day',
  minGapHoursWarn: 'Spacing warning',
  missedSlotGraceMinutes: 'Missed-slot grace',
  stageLeadMinutes: 'Staging',
  reminders: 'Reminders',
  crawlIntervalHours: 'Feed crawl',
  scout: 'Weekly scouting',
  digest: 'Voice digest',
  backupTime: 'Daily backup',
  defaultMode: 'Default mode',
};

function fieldName(path: string): string {
  const [head = '', second] = path.split('.');
  if (head === 'postSlots' && second !== undefined && /^\d+$/.test(second)) return `Post slot ${String(Number(second) + 1)}`;
  return FIELD_NAMES[head] ?? head;
}

function describeCadence(draft: CadenceDraft): string {
  const at = (value: WeeklyTimeDraft) => `${WEEKDAY_LABELS[value.day]} ${value.time}`;
  return [
    `Time zone: ${draft.timezone}`,
    `Post slots: ${slotsByWeek(draft.postSlots)
      .map((slot) => at(slot))
      .join(', ')}`,
    `Posts per week: normal ${draft.postsNormal}, light ${draft.postsLight}, maximum ${draft.postsMax}`,
    `Default mode: ${draft.defaultMode}`,
    `Weekly plan: ${at(draft.planWeek)}`,
    `Research starts: ${at(draft.researchStart)}`,
    `Interview due: ${at(draft.interviewDue)}`,
    `Drafting starts: ${at(draft.draftDay)}`,
    `Veto notice: ${at(draft.vetoNotice)}`,
    `Article publishes: ${at(draft.articlePublish)}`,
    `Business hours: ${draft.businessHoursStart} to ${draft.businessHoursEnd}`,
    `Reply reminders (minutes): ${draft.replyMinutes}`,
    `Snapshot reminders (hours): ${draft.snapshotHours}`,
    `Feed crawl: every ${draft.crawlIntervalHours} hours`,
    `Scouting: ${at(draft.scout)}`,
    `Voice digest: ${at(draft.digest)}`,
    `Daily backup: ${draft.backupTime}`,
  ].join('\n');
}

const KIND_LABELS: Readonly<Record<SlotDraft['kind'], string>> = {
  primary: 'Every week',
  optional: 'Busy weeks only',
};

function SlotRow({
  slot,
  index,
  errors,
  onChange,
  onRemove,
}: {
  slot: SlotDraft;
  index: number;
  errors: Readonly<Record<string, string>>;
  onChange: (slot: SlotDraft) => void;
  onRemove: () => void;
}) {
  const name = `Slot ${String(index + 1)}`;
  const base = `postSlots.${String(index)}`;
  return (
    <li data-slot-key={slot.key} className="border-b border-line py-4">
      <Fieldset legend={name} className="min-w-0">
        <div className="flex flex-wrap items-start gap-3">
          <Field label={`${name}: day`} hideLabel error={errors[`${base}.day`]}>
            {(control) => (
              <WeekdaySelect
                control={control}
                value={slot.day}
                onChange={(day) => {
                  onChange({ ...slot, day });
                }}
              />
            )}
          </Field>
          {/* A slot listed twice is fixed by changing its time: the message sits there. */}
          <Field label={`${name}: time`} hideLabel error={errors[`${base}.time`] ?? errors[base]}>
            <TimeInput
              value={slot.time}
              onChange={(time) => {
                onChange({ ...slot, time });
              }}
            />
          </Field>
          <Field label={`${name}: used in`} hideLabel>
            {(control) => (
              <Select
                value={slot.kind}
                onValueChange={(kind) => {
                  onChange({ ...slot, kind: kind === 'optional' ? 'optional' : 'primary' });
                }}
              >
                <SelectTrigger {...control} className="min-w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="primary">{KIND_LABELS.primary}</SelectItem>
                  <SelectItem value="optional">{KIND_LABELS.optional}</SelectItem>
                </SelectContent>
              </Select>
            )}
          </Field>
          <NumberField
            label={`${name}: window`}
            hideLabel
            unit="min to post in"
            min={0}
            max={240}
            value={slot.windowMinutes}
            error={errors[`${base}.windowMinutes`]}
            onChange={(windowMinutes) => {
              onChange({ ...slot, windowMinutes });
            }}
          />
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto"
            aria-label={`Remove ${name} (${WEEKDAY_LABELS[slot.day]} ${slot.time})`}
            onClick={onRemove}
          >
            <Trash2 aria-hidden="true" />
          </Button>
        </div>
      </Fieldset>
    </li>
  );
}

export interface CadenceFormProps {
  source: Versioned<CadenceSettings>;
  /** The newest version read, for the conflict comparison. */
  latest: Versioned<CadenceSettings>;
  onReload: () => Promise<Versioned<CadenceSettings> | undefined>;
}

/** The whole cadence (one document, one save): slots, modes, the weekly calendar, reminders and background times. */
export function CadenceForm({ source, latest, onReload }: CadenceFormProps) {
  const form = useVersionedDraft(source, toCadenceDraft);
  const { draft } = form;
  const conflict = useConflict();
  const [attempted, setAttempted] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const save = useApiMutation('put', '/api/kb/cadence', {
    updates: () => settingsKeys.cadence,
    // Saving reseeds the schedule items on the server.
    invalidates: [settingsKeys.schedules],
    successToast: 'Cadence saved. The schedules follow the new times.',
    errorToast: 'Couldn’t save the cadence.',
    handles: ['etag_mismatch', 'validation_failed'],
  });

  const clientErrors = cadenceProblems(draft);
  const serverErrors = fieldErrors(save.error);
  const errors: Record<string, string> = { ...serverErrors, ...(attempted ? clientErrors : {}) };
  const serverInvalid = isProblem(save.error, 'validation_failed');

  useEffect(() => {
    if (serverInvalid) focusFirstInvalid(formRef.current);
  }, [serverInvalid]);

  const update = (change: (current: CadenceDraft) => CadenceDraft) => {
    if (save.isError) save.reset();
    form.setDraft(change);
  };
  const setWeekly = (field: WeeklyField) => (value: WeeklyTimeDraft) => {
    update((current) => ({ ...current, [field]: value }));
  };
  const setText = (field: keyof CadenceDraft) => (value: string) => {
    update((current) => ({ ...current, [field]: value }));
  };

  const send = (etag: string | null) => {
    save.mutate(
      { body: toCadenceBody(draft, form.base.item), ifMatch: etag },
      {
        onSuccess: (data) => {
          form.reset(data);
          setAttempted(false);
          conflict.close();
        },
        onError: (error) => {
          if (conflict.show(error)) void onReload();
        },
      },
    );
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (Object.keys(clientErrors).length > 0) {
      flushSync(() => {
        setAttempted(true);
      });
      focusFirstInvalid(formRef.current);
      return;
    }
    send(form.base.etag);
  };

  const addSlot = () => {
    const used = new Set(draft.postSlots.map((slot) => slot.day));
    const day: Weekday = WEEKDAYS.find((candidate) => !used.has(candidate)) ?? 'mon';
    const key = newSlotKey();
    flushSync(() => {
      update((current) => ({
        ...current,
        postSlots: [...current.postSlots, { key, day, time: '10:00', kind: 'optional', windowMinutes: '120' }],
      }));
    });
    listRef.current?.querySelector<HTMLElement>(`[data-slot-key="${key}"] button`)?.focus();
  };

  const listedErrors = Object.entries(serverErrors);

  return (
    <form ref={formRef} noValidate onSubmit={submit} aria-label="Cadence" className="space-y-16">
      {serverInvalid && listedErrors.length > 0 ? (
        <Alert variant="error">
          <AlertTitle>The server didn’t accept these settings.</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-1 pl-5">
              {listedErrors.map(([path, message]) => (
                <li key={path}>{`${fieldName(path)}: ${message}`}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <SettingsBlock
        title="Post slots"
        description="When posts go out each week, in your time zone. A post waits for its slot; a missed one is offered the next free slot, never stacked."
      >
        <div className="space-y-6">
          <WeekStrip slots={draft.postSlots} />
          {errors.postSlots ? <p className="text-sm font-medium text-danger">{errors.postSlots}</p> : null}
          <ol ref={listRef} aria-label="Post slots" className="max-w-3xl border-t border-line">
            {draft.postSlots.map((slot, index) => (
              <SlotRow
                key={slot.key}
                slot={slot}
                index={index}
                errors={errors}
                onChange={(next) => {
                  update((current) => ({
                    ...current,
                    postSlots: current.postSlots.map((item) => (item.key === slot.key ? next : item)),
                  }));
                }}
                onRemove={() => {
                  flushSync(() => {
                    update((current) => ({
                      ...current,
                      postSlots: current.postSlots.filter((item) => item.key !== slot.key),
                    }));
                  });
                  addRef.current?.focus();
                }}
              />
            ))}
          </ol>
          {draft.postSlots.length === 0 ? (
            <p className="text-ink-muted">No slots. Posts can still be approved, but nothing has a time to go out.</p>
          ) : null}
          <Button
            ref={addRef}
            variant="secondary"
            disabled={draft.postSlots.length >= MAX_SLOTS}
            onClick={addSlot}
          >
            <Plus aria-hidden="true" />
            Add a slot
          </Button>
          <div className="grid max-w-measure gap-6">
            <Field
              orientation="horizontal"
              label="Block two posts on the same day"
              description="A second post on a day is refused. Leave this on: same-day posts compete with each other."
              error={errors.sameDayBlock}
            >
              <Switch
                checked={draft.sameDayBlock}
                onCheckedChange={(sameDayBlock) => {
                  update((current) => ({ ...current, sameDayBlock }));
                }}
              />
            </Field>
            <NumberField
              label="Warn when posts are closer than"
              unit="hours"
              min={0}
              max={72}
              value={draft.minGapHoursWarn}
              error={errors.minGapHoursWarn}
              onChange={setText('minGapHoursWarn')}
            />
            <NumberField
              label="Grace for a missed slot"
              description={`The publish page stays open this long after the slot (${formatMinutes(Number(draft.missedSlotGraceMinutes) || 0)}); after it, the post is offered the next free slot.`}
              unit="minutes"
              min={0}
              max={720}
              value={draft.missedSlotGraceMinutes}
              error={errors.missedSlotGraceMinutes}
              onChange={setText('missedSlotGraceMinutes')}
            />
            <NumberField
              label="Get posts ready before the slot"
              description="Rendering, uploads and the token check happen this long before, so the tap at the slot is instant."
              unit="minutes"
              min={15}
              max={240}
              value={draft.stageLeadMinutes}
              error={errors.stageLeadMinutes}
              onChange={setText('stageLeadMinutes')}
            />
          </div>
        </div>
      </SettingsBlock>

      <SettingsBlock
        title="Posts per week"
        description="How many posts each kind of week plans. Set each week’s mode in Calendar > Availability."
      >
        <div className="grid max-w-measure gap-6">
          <Fieldset legend="Default mode for a new week" error={errors.defaultMode}>
            {(group) => (
              <SegmentedControl
                {...group}
                value={draft.defaultMode}
                onValueChange={(value) => {
                  const mode = MODES.find((item) => item.value === value);
                  if (mode) update((current) => ({ ...current, defaultMode: mode.value }));
                }}
                fullWidth
              >
                {MODES.map((mode) => (
                  <SegmentedControlItem key={mode.value} value={mode.value}>
                    {mode.label}
                  </SegmentedControlItem>
                ))}
              </SegmentedControl>
            )}
          </Fieldset>
          {errors.postsPerWeek ? <p className="text-sm font-medium text-danger">{errors.postsPerWeek}</p> : null}
          <div className="grid gap-6 sm:grid-cols-2">
            <NumberField
              label="Normal week"
              unit="posts"
              min={0}
              max={7}
              value={draft.postsNormal}
              error={errors['postsPerWeek.normal']}
              onChange={setText('postsNormal')}
            />
            <NumberField
              label="Light week"
              unit="posts"
              min={0}
              max={7}
              value={draft.postsLight}
              error={errors['postsPerWeek.light']}
              onChange={setText('postsLight')}
            />
            <NumberField
              label="At most"
              description="Only when every post passes the gates."
              unit="posts"
              min={1}
              max={7}
              value={draft.postsMax}
              error={errors['postsPerWeek.max']}
              onChange={setText('postsMax')}
            />
            <div className="grid content-start gap-1.5">
              <p className="text-sm font-medium text-ink">Away week</p>
              <p className="text-sm text-ink-muted">Nothing is generated. Approved posts can go out through the kit.</p>
            </div>
          </div>
        </div>
      </SettingsBlock>

      <SettingsBlock
        title="The weekly calendar"
        description="The anchors of the week: when the plan is ready, when research starts, when your answers are due, and when the article goes live."
      >
        <div className="grid max-w-measure gap-8">
          <Field
            label="Time zone"
            description="Every time on this page is in this zone, and the schedules follow its daylight saving."
            error={errors.timezone}
            required
          >
            {(control) => (
              <>
                <Input
                  {...control}
                  list="settings-time-zones"
                  autoComplete="off"
                  spellCheck={false}
                  value={draft.timezone}
                  onChange={(event) => {
                    setText('timezone')(event.target.value);
                  }}
                />
                <datalist id="settings-time-zones">
                  {TIME_ZONES.map((zone) => (
                    <option key={zone} value={zone} />
                  ))}
                </datalist>
              </>
            )}
          </Field>
          <WeeklyTimeField
            legend="Your plan for next week is ready"
            value={draft.planWeek}
            onChange={setWeekly('planWeek')}
            error={errors.planWeek}
            timeError={errors['planWeek.time']}
          />
          <NumberField
            label="Prepare the plan ahead by"
            description="The planner runs this long before, so the plan is waiting for you."
            unit="minutes"
            min={0}
            max={720}
            value={draft.planLeadMinutes}
            error={errors.planLeadMinutes}
            onChange={setText('planLeadMinutes')}
          />
          <WeeklyTimeField
            legend="Research starts"
            value={draft.researchStart}
            onChange={setWeekly('researchStart')}
            timeError={errors['researchStart.time']}
          />
          <WeeklyTimeField
            legend="Interview answers due"
            value={draft.interviewDue}
            onChange={setWeekly('interviewDue')}
            timeError={errors['interviewDue.time']}
          />
          <WeeklyTimeField
            legend="Drafting starts"
            value={draft.draftDay}
            onChange={setWeekly('draftDay')}
            timeError={errors['draftDay.time']}
          />
          <WeeklyTimeField
            legend="Veto notice goes out"
            description="Before an auto-published article goes live, you get a notice and a chance to stop it."
            value={draft.vetoNotice}
            onChange={setWeekly('vetoNotice')}
            error={errors.vetoNotice}
            timeError={errors['vetoNotice.time']}
          />
          <WeeklyTimeField
            legend="Article publishes"
            value={draft.articlePublish}
            onChange={setWeekly('articlePublish')}
            timeError={errors['articlePublish.time']}
          />
          <NumberField
            label="Veto notice at least this long before publishing"
            unit="hours"
            min={24}
            max={168}
            value={draft.minVetoLeadHours}
            error={errors.minVetoLeadHours}
            onChange={setText('minVetoLeadHours')}
          />
          <Fieldset
            legend="Business hours"
            description="Notices that need an answer go out inside these hours."
            error={errors.businessHoursEnd ?? errors.businessHoursStart}
          >
            <div className="flex flex-wrap items-center gap-3">
              <Field label="Business hours start" hideLabel>
                <TimeInput value={draft.businessHoursStart} onChange={setText('businessHoursStart')} />
              </Field>
              <span aria-hidden="true" className="text-ink-muted">
                to
              </span>
              <Field label="Business hours end" hideLabel>
                <TimeInput value={draft.businessHoursEnd} onChange={setText('businessHoursEnd')} />
              </Field>
            </div>
          </Fieldset>
        </div>
      </SettingsBlock>

      <SettingsBlock
        title="Reminders"
        description="Nudges after a post goes live. They arrive as notifications and as items in Today."
      >
        <div className="grid max-w-measure gap-6">
          <Field
            label="Reply reminders, in minutes after going live"
            description={`Separate with commas. Now: ${
              numberList(draft.replyMinutes)
                .filter((value): value is number => value !== undefined)
                .map(formatMinutes)
                .join(', ') || 'none'
            }.`}
            error={errorAt(errors, 'reminders.replyMinutes')}
          >
            <Input
              inputMode="numeric"
              value={draft.replyMinutes}
              onChange={(event) => {
                setText('replyMinutes')(event.target.value);
              }}
            />
          </Field>
          <Field
            label="Snapshot reminders, in hours after going live"
            description={`Separate with commas. Now: ${
              numberList(draft.snapshotHours)
                .filter((value): value is number => value !== undefined)
                .map(formatHours)
                .join(', ') || 'none'
            }.`}
            error={errorAt(errors, 'reminders.snapshotHours')}
          >
            <Input
              inputMode="numeric"
              value={draft.snapshotHours}
              onChange={(event) => {
                setText('snapshotHours')(event.target.value);
              }}
            />
          </Field>
        </div>
      </SettingsBlock>

      <SettingsBlock title="Background work" description="When the app reads feeds, scouts, sends the voice digest and backs up.">
        <div className="grid max-w-measure gap-8">
          <NumberField
            label="Crawl the feeds every"
            unit="hours"
            min={1}
            max={48}
            value={draft.crawlIntervalHours}
            error={errors.crawlIntervalHours}
            onChange={setText('crawlIntervalHours')}
          />
          <WeeklyTimeField
            legend="Weekly scouting"
            value={draft.scout}
            onChange={setWeekly('scout')}
            timeError={errors['scout.time']}
          />
          <WeeklyTimeField
            legend="Voice digest"
            description="Weekly for the first three months, then monthly."
            value={draft.digest}
            onChange={setWeekly('digest')}
            timeError={errors['digest.time']}
          />
          <Field label="Daily backup at" error={errors.backupTime}>
            <TimeInput value={draft.backupTime} onChange={setText('backupTime')} />
          </Field>
        </div>
      </SettingsBlock>

      <SaveBar
        sticky
        dirty={form.dirty}
        saving={save.isPending}
        paused={save.isPaused}
        saveLabel="Save the cadence"
        allowClean={form.base.etag === null}
        idleNote={form.base.etag === null ? 'The defaults apply until you save.' : undefined}
        onDiscard={() => {
          setAttempted(false);
          save.reset();
          form.reset(form.base);
        }}
      />
      <ConflictDialog
        {...conflict}
        subject="The cadence"
        mine={describeCadence(draft)}
        theirs={latest.etag === form.base.etag ? undefined : describeCadence(toCadenceDraft(latest.item))}
        saving={save.isPending}
        onKeepMine={() => {
          const etag = conflict.conflict?.etag;
          if (etag) {
            send(etag);
          } else {
            void onReload().then(() => {
              conflict.close();
            });
          }
        }}
        onTakeTheirs={() => {
          void onReload().then((current) => {
            if (current) form.reset(current);
            setAttempted(false);
            conflict.close();
          });
        }}
      />
    </form>
  );
}
