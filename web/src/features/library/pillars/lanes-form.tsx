import { Plus, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';

import { NumberField } from '../components/number-field';
import type { ErrorMap } from '../lib/errors';
import { slugify, uniqueSlug } from '../lib/rules';
import type { LanesDraft, SeriesDraft } from './lanes-model';

let newSeries = 0;

export interface LanesFormProps {
  draft: LanesDraft;
  errors: ErrorMap;
  /** The saved pillars: a series belongs to one of them. */
  pillars: readonly { readonly id: string; readonly name: string }[];
  onChange: (change: (draft: LanesDraft) => LanesDraft) => void;
}

function SwitchField({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <Field label={label} description={description} orientation="horizontal">
      <Switch checked={checked} onCheckedChange={onChange} />
    </Field>
  );
}

/** The series per pillar and the weekly lane rules the planner follows. */
export function LanesForm({ draft, errors, pillars, onChange }: LanesFormProps) {
  const setTop = <K extends keyof LanesDraft>(field: K, value: LanesDraft[K]) => {
    onChange((current) => ({ ...current, [field]: value }));
  };
  const setQuick = (part: Partial<LanesDraft['quickTake']>) => {
    onChange((current) => ({ ...current, quickTake: { ...current.quickTake, ...part } }));
  };
  const setFormats = (part: Partial<LanesDraft['formats']>) => {
    onChange((current) => ({ ...current, formats: { ...current.formats, ...part } }));
  };
  const setRepurpose = (part: Partial<LanesDraft['repurpose']>) => {
    onChange((current) => ({ ...current, repurpose: { ...current.repurpose, ...part } }));
  };
  const setSeries = (index: number, change: (series: SeriesDraft) => SeriesDraft) => {
    onChange((current) => ({
      ...current,
      series: current.series.map((series, position) => (position === index ? change(series) : series)),
    }));
  };
  const nameOf = (id: string) => pillars.find((pillar) => pillar.id === id)?.name ?? id;

  return (
    <div className="space-y-10">
      <Fieldset
        legend="Series"
        description="Recurring titles a pillar’s posts can belong to, such as “Okta to Entra field notes”. A paused series keeps its posts but gets no new ones."
        error={errors.series}
      >
        {draft.series.length === 0 ? <p className="text-sm text-ink-muted">No series yet.</p> : null}
        <ol className="space-y-3">
          {draft.series.map((series, index) => {
            const base = `series.${String(index)}`;
            const number = String(index + 1);
            return (
              <li
                key={series.key}
                className="grid gap-3 rounded-lg border border-line p-3 sm:grid-cols-[minmax(0,1fr)_12rem_auto_auto] sm:items-start"
              >
                <Field label={`Series ${number} title`} hideLabel error={errors[`${base}.title`]}>
                  <Input
                    value={series.title}
                    maxLength={150}
                    placeholder="Series title"
                    onChange={(event) => {
                      const title = event.target.value;
                      onChange((current) => ({
                        ...current,
                        series: current.series.map((item, position) => {
                          if (position !== index) return item;
                          if (item.key.startsWith('saved:')) return { ...item, title };
                          // A new series takes its id from its title; saved ids never change.
                          const taken = current.series.filter((_, other) => other !== index).map((other) => other.id);
                          return { ...item, title, id: uniqueSlug(slugify(title, 80), taken, 80) };
                        }),
                      }));
                    }}
                  />
                </Field>
                <Field label={`Series ${number} pillar`} hideLabel error={errors[`${base}.pillar`]}>
                  {(control) => (
                    <Select
                      value={series.pillar}
                      onValueChange={(pillar) => {
                        setSeries(index, (item) => ({ ...item, pillar }));
                      }}
                    >
                      <SelectTrigger {...control}>
                        <SelectValue placeholder="Choose a pillar">{nameOf(series.pillar)}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {pillars.map((pillar) => (
                          <SelectItem key={pillar.id} value={pillar.id}>
                            {pillar.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                </Field>
                <Field label="Active" orientation="horizontal" className="sm:pt-2">
                  <Switch
                    aria-label={`Series ${number} active`}
                    checked={series.active}
                    onCheckedChange={(active) => {
                      setSeries(index, (item) => ({ ...item, active }));
                    }}
                  />
                </Field>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove series ${number}${series.title.trim() ? `, ${series.title.trim()}` : ''}`}
                  onClick={() => {
                    onChange((current) => ({ ...current, series: current.series.filter((_, position) => position !== index) }));
                  }}
                >
                  <X aria-hidden="true" />
                </Button>
              </li>
            );
          })}
        </ol>
        <div>
          <Button
            variant="secondary"
            size="sm"
            disabled={pillars.length === 0 || draft.series.length >= 50}
            onClick={() => {
              newSeries += 1;
              const first = pillars[0]?.id ?? '';
              onChange((current) => ({
                ...current,
                series: [
                  ...current.series,
                  { key: `new:${String(newSeries)}`, id: '', pillar: first, title: '', active: true, extra: {} },
                ],
              }));
            }}
          >
            <Plus aria-hidden="true" />
            Add a series
          </Button>
        </div>
      </Fieldset>

      <Fieldset legend="Each week" description="How the planner mixes a week’s posts.">
        <div className="grid gap-5 sm:grid-cols-2">
          <NumberField
            label="Derivative posts, at most"
            unit="a week"
            description="Posts cut from the week’s flagship article."
            value={draft.derivativesMaxPerWeek}
            error={errors.derivativesMaxPerWeek}
            onChange={(value) => {
              setTop('derivativesMaxPerWeek', value);
            }}
          />
          <NumberField
            label="Native posts, at least"
            unit="a week"
            description="Posts from your story and position banks."
            value={draft.nativeMinPerWeek}
            error={errors.nativeMinPerWeek}
            onChange={(value) => {
              setTop('nativeMinPerWeek', value);
            }}
          />
          <NumberField
            label="On-pillar share, at least"
            unit="%"
            decimal
            description="Of each week’s posts."
            value={draft.onPillarMinShare}
            error={errors.onPillarMinShare}
            onChange={(value) => {
              setTop('onPillarMinShare', value);
            }}
          />
          <NumberField
            label="Hook variants per post"
            description="Between 1 and 5."
            value={draft.hookVariants}
            error={errors.hookVariants}
            onChange={(value) => {
              setTop('hookVariants', value);
            }}
          />
        </div>
        <SwitchField
          label="Native posts come from another pillar"
          description="Than the week’s flagship, so one week covers more ground."
          checked={draft.nativeOtherPillar}
          onChange={(checked) => {
            setTop('nativeOtherPillar', checked);
          }}
        />
      </Fieldset>

      <Fieldset legend="Quick takes" description="Short posts from the Ideas inbox that go out the same week.">
        <div className="grid gap-5 sm:grid-cols-3">
          <NumberField
            label="Shortest, in characters"
            value={draft.quickTake.minChars}
            error={errors['quickTake.minChars']}
            onChange={(minChars) => {
              setQuick({ minChars });
            }}
          />
          <NumberField
            label="Longest, in characters"
            value={draft.quickTake.maxChars}
            error={errors['quickTake.maxChars']}
            onChange={(maxChars) => {
              setQuick({ maxChars });
            }}
          />
          <NumberField
            label="Post within"
            unit="hours"
            value={draft.quickTake.maxAgeHours}
            error={errors['quickTake.maxAgeHours']}
            onChange={(maxAgeHours) => {
              setQuick({ maxAgeHours });
            }}
          />
        </div>
        <SwitchField
          label="Your take is required"
          description="A quick take cannot be generated until you have written what you think."
          checked={draft.quickTake.ownerTakeRequired}
          onChange={(ownerTakeRequired) => {
            setQuick({ ownerTakeRequired });
          }}
        />
      </Fieldset>

      <Fieldset legend="Post formats">
        <div className="grid gap-5 sm:grid-cols-2">
          <NumberField
            label="Carousel, fewest slides"
            value={draft.formats.carouselMinSlides}
            error={errors['formats.carouselMinSlides']}
            onChange={(carouselMinSlides) => {
              setFormats({ carouselMinSlides });
            }}
          />
          <NumberField
            label="Carousel, most slides"
            value={draft.formats.carouselMaxSlides}
            error={errors['formats.carouselMaxSlides']}
            onChange={(carouselMaxSlides) => {
              setFormats({ carouselMaxSlides });
            }}
          />
          <NumberField
            label="Text post, fewest characters"
            value={draft.formats.textMinChars}
            error={errors['formats.textMinChars']}
            onChange={(textMinChars) => {
              setFormats({ textMinChars });
            }}
          />
          <NumberField
            label="Text post, most characters"
            description="LinkedIn allows 3,000."
            value={draft.formats.textMaxChars}
            error={errors['formats.textMaxChars']}
            onChange={(textMaxChars) => {
              setFormats({ textMaxChars });
            }}
          />
          <NumberField
            label="Polls, at most"
            unit="a month"
            value={draft.formats.pollMaxPerMonth}
            error={errors['formats.pollMaxPerMonth']}
            onChange={(pollMaxPerMonth) => {
              setFormats({ pollMaxPerMonth });
            }}
          />
        </div>
        <SwitchField
          label="Single-image framework cards"
          description="One slide from your brand kit’s templates."
          checked={draft.formats.imageCardEnabled}
          onChange={(imageCardEnabled) => {
            setFormats({ imageCardEnabled });
          }}
        />
      </Fieldset>

      <Fieldset legend="Repurposing" description="Evergreen posts may be cut again with a new angle.">
        <div className="grid gap-5 sm:grid-cols-2">
          <NumberField
            label="After at least"
            unit="weeks"
            value={draft.repurpose.minWeeks}
            error={errors['repurpose.minWeeks']}
            onChange={(minWeeks) => {
              setRepurpose({ minWeeks });
            }}
          />
          <NumberField
            label="And at most"
            unit="weeks"
            value={draft.repurpose.maxWeeks}
            error={errors['repurpose.maxWeeks']}
            onChange={(maxWeeks) => {
              setRepurpose({ maxWeeks });
            }}
          />
        </div>
        <SwitchField
          label="Check novelty first"
          description="A re-cut must pass the near-duplicate check against recent posts."
          checked={draft.repurpose.requiresNoveltyCheck}
          onChange={(requiresNoveltyCheck) => {
            setRepurpose({ requiresNoveltyCheck });
          }}
        />
      </Fieldset>
    </div>
  );
}
