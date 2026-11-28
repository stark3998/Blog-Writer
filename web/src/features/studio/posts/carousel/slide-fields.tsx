import { Plus, X } from 'lucide-react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Field, Fieldset } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Textarea } from '@/components/ui/textarea';

import { LIMITS, type Cell, type Slide, type Source, type SpecError, type Step } from '../model/slides';
import { wordCount } from '../model/text';

export interface SlideFieldsProps {
  slide: Slide;
  onChange: (slide: Slide) => void;
  /** Errors the server gave for this slide (`invalid_spec`), by field path (`hook`, `items.1`). */
  errors: readonly SpecError[];
  readOnly: boolean;
  /** "Slide 3", to make repeated field names unique for assistive technology. */
  slideName: string;
}

function errorFor(errors: readonly SpecError[], field: string): string | undefined {
  return errors.find((error) => error.field === field)?.message;
}

function TextInput({
  label,
  value,
  onChange,
  max,
  readOnly,
  error,
  description,
  required = false,
  hideLabel = false,
}: {
  label: string;
  value: string | null | undefined;
  onChange: (value: string) => void;
  max: number;
  readOnly: boolean;
  error?: string;
  description?: ReactNode;
  required?: boolean;
  hideLabel?: boolean;
}) {
  return (
    <Field label={label} description={description} error={error} required={required} hideLabel={hideLabel}>
      <Input
        value={value ?? ''}
        maxLength={max}
        readOnly={readOnly}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    </Field>
  );
}

function TextArea({
  label,
  value,
  onChange,
  max,
  readOnly,
  error,
  description,
  required = false,
  mono = false,
}: {
  label: string;
  value: string | null | undefined;
  onChange: (value: string) => void;
  max: number;
  readOnly: boolean;
  error?: string;
  description?: ReactNode;
  required?: boolean;
  mono?: boolean;
}) {
  return (
    <Field label={label} description={description} error={error} required={required}>
      <Textarea
        value={value ?? ''}
        maxLength={max}
        readOnly={readOnly}
        spellCheck={!mono}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        className={mono ? 'min-h-32 font-mono text-sm' : 'min-h-20'}
      />
    </Field>
  );
}

/** A list of short texts (list or recap items) with add and remove, within the type's limits. */
function ItemList({
  legend,
  items,
  onChange,
  min,
  max,
  itemMax,
  readOnly,
  errors,
  itemName,
}: {
  legend: string;
  items: readonly string[];
  onChange: (items: string[]) => void;
  min: number;
  max: number;
  itemMax: number;
  readOnly: boolean;
  errors: readonly SpecError[];
  itemName: string;
}) {
  return (
    <Fieldset legend={legend} description={`${String(min)} to ${String(max)} items.`} error={errorFor(errors, 'items')}>
      <ol className="grid gap-2">
        {items.map((item, index) => (
          <li key={index} className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <TextInput
                label={`${itemName} ${String(index + 1)}`}
                hideLabel
                value={item}
                max={itemMax}
                readOnly={readOnly}
                error={errorFor(errors, `items.${String(index)}`)}
                onChange={(value) => {
                  onChange(items.map((current, at) => (at === index ? value : current)));
                }}
              />
            </div>
            {!readOnly && items.length > min ? (
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove ${itemName.toLowerCase()} ${String(index + 1)}`}
                onClick={() => {
                  onChange(items.filter((_, at) => at !== index));
                }}
              >
                <X aria-hidden="true" />
              </Button>
            ) : null}
          </li>
        ))}
      </ol>
      {!readOnly && items.length < max ? (
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onChange([...items, '']);
            }}
          >
            <Plus aria-hidden="true" />
            {`Add ${itemName.toLowerCase()}`}
          </Button>
        </div>
      ) : null}
    </Fieldset>
  );
}

/** The fields of one slide, by its type, with the server's layout limits. */
export function SlideFields({ slide, onChange, errors, readOnly, slideName }: SlideFieldsProps) {
  const common = { readOnly };
  switch (slide.type) {
    case 'cover': {
      const words = wordCount(slide.hook);
      return (
        <div className="grid gap-3">
          <TextInput
            label="Hook"
            required
            value={slide.hook}
            max={LIMITS.hook}
            error={errorFor(errors, 'hook')}
            description={
              <span className={words > LIMITS.coverWords ? 'font-medium text-warn' : undefined}>
                {`${String(words)} of ${String(LIMITS.coverWords)} words.`}
              </span>
            }
            onChange={(hook) => {
              onChange({ ...slide, hook });
            }}
            {...common}
          />
          <TextInput
            label="Kicker"
            description="Optional: the series or pillar name above the hook."
            value={slide.kicker}
            max={LIMITS.kicker}
            error={errorFor(errors, 'kicker')}
            onChange={(kicker) => {
              onChange({ ...slide, kicker });
            }}
            {...common}
          />
          <TextInput
            label="Subtitle"
            description="Optional."
            value={slide.subtitle}
            max={LIMITS.subtitle}
            error={errorFor(errors, 'subtitle')}
            onChange={(subtitle) => {
              onChange({ ...slide, subtitle });
            }}
            {...common}
          />
        </div>
      );
    }
    case 'statement':
      return (
        <div className="grid gap-3">
          <TextArea
            label="Statement"
            required
            value={slide.statement}
            max={LIMITS.statement}
            error={errorFor(errors, 'statement')}
            onChange={(statement) => {
              onChange({ ...slide, statement });
            }}
            {...common}
          />
          <TextInput
            label="Note"
            description="Optional: one line under the statement."
            value={slide.note}
            max={LIMITS.note}
            error={errorFor(errors, 'note')}
            onChange={(note) => {
              onChange({ ...slide, note });
            }}
            {...common}
          />
        </div>
      );
    case 'list':
    case 'recap':
      return (
        <div className="grid gap-3">
          <TextInput
            label="Title"
            required
            value={slide.title}
            max={slide.type === 'recap' ? LIMITS.recapTitle : LIMITS.slideTitle}
            error={errorFor(errors, 'title')}
            onChange={(title) => {
              onChange({ ...slide, title });
            }}
            {...common}
          />
          <ItemList
            legend={slide.type === 'recap' ? 'What to do' : 'Items'}
            items={slide.items}
            min={LIMITS.listItemsMin}
            max={LIMITS.listItemsMax}
            itemMax={slide.type === 'recap' ? LIMITS.recapItem : LIMITS.listItem}
            readOnly={readOnly}
            errors={errors}
            itemName="Item"
            onChange={(items) => {
              onChange({ ...slide, items });
            }}
          />
        </div>
      );
    case 'steps':
      return (
        <StepsFields
          title={slide.title}
          steps={slide.steps}
          errors={errors}
          readOnly={readOnly}
          slideName={slideName}
          onChange={(title, steps) => {
            onChange({ ...slide, title, steps });
          }}
        />
      );
    case 'framework':
      return (
        <div className="grid gap-3">
          <TextInput
            label="Title"
            required
            value={slide.title}
            max={LIMITS.slideTitle}
            error={errorFor(errors, 'title')}
            onChange={(title) => {
              onChange({ ...slide, title });
            }}
            {...common}
          />
          <Fieldset legend="Layout">
            {(group) => (
              <SegmentedControl
                {...group}
                value={slide.layout}
                disabled={readOnly}
                onValueChange={(value) => {
                  if (value !== '2x2' && value !== 'layered') return;
                  const cells: Cell[] =
                    value === '2x2'
                      ? Array.from({ length: 4 }, (_, index) => slide.cells[index] ?? { label: '', text: '' })
                      : slide.cells.slice(0, LIMITS.cellsMax);
                  onChange({ ...slide, layout: value, cells, x_axis: value === '2x2' ? slide.x_axis : null, y_axis: value === '2x2' ? slide.y_axis : null });
                }}
              >
                <SegmentedControlItem value="2x2">2 × 2</SegmentedControlItem>
                <SegmentedControlItem value="layered">Layers</SegmentedControlItem>
              </SegmentedControl>
            )}
          </Fieldset>
          <CellsFields
            cells={slide.cells}
            layered={slide.layout === 'layered'}
            errors={errors}
            readOnly={readOnly}
            onChange={(cells) => {
              onChange({ ...slide, cells });
            }}
          />
          {slide.layout === '2x2' ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <TextInput
                label="Across (x axis)"
                description="Optional."
                value={slide.x_axis}
                max={LIMITS.axis}
                error={errorFor(errors, 'x_axis')}
                onChange={(x_axis) => {
                  onChange({ ...slide, x_axis });
                }}
                {...common}
              />
              <TextInput
                label="Up (y axis)"
                description="Optional."
                value={slide.y_axis}
                max={LIMITS.axis}
                error={errorFor(errors, 'y_axis')}
                onChange={(y_axis) => {
                  onChange({ ...slide, y_axis });
                }}
                {...common}
              />
            </div>
          ) : null}
        </div>
      );
    case 'diagram':
      return (
        <div className="grid gap-3">
          <TextInput
            label="Title"
            required
            value={slide.title}
            max={LIMITS.slideTitle}
            error={errorFor(errors, 'title')}
            onChange={(title) => {
              onChange({ ...slide, title });
            }}
            {...common}
          />
          <TextArea
            label="Mermaid source"
            required
            mono
            description="Flowchart, sequence and the other diagrams the tiny Mermaid build draws. Rendered on the server."
            value={slide.mermaid}
            max={LIMITS.mermaid}
            error={errorFor(errors, 'mermaid')}
            onChange={(mermaid) => {
              onChange({ ...slide, mermaid });
            }}
            {...common}
          />
          <TextArea
            label="Alt text"
            required
            description="What the diagram shows, for people who can’t see it."
            value={slide.alt}
            max={LIMITS.alt}
            error={errorFor(errors, 'alt')}
            onChange={(alt) => {
              onChange({ ...slide, alt });
            }}
            {...common}
          />
          <TextInput
            label="Caption"
            description="Optional."
            value={slide.caption}
            max={LIMITS.caption}
            error={errorFor(errors, 'caption')}
            onChange={(caption) => {
              onChange({ ...slide, caption });
            }}
            {...common}
          />
        </div>
      );
    case 'stat':
      return (
        <div className="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)]">
          <TextInput
            label="Number"
            required
            description="2,000 or 73% or $4.2M"
            value={slide.value}
            max={LIMITS.statValue}
            error={errorFor(errors, 'value')}
            onChange={(value) => {
              onChange({ ...slide, value });
            }}
            {...common}
          />
          <TextInput
            label="What it counts"
            required
            value={slide.label}
            max={LIMITS.statLabel}
            error={errorFor(errors, 'label')}
            onChange={(label) => {
              onChange({ ...slide, label });
            }}
            {...common}
          />
          <div className="sm:col-span-2">
            <TextInput
              label="Context"
              description="Optional."
              value={slide.context}
              max={LIMITS.statContext}
              error={errorFor(errors, 'context')}
              onChange={(context) => {
                onChange({ ...slide, context });
              }}
              {...common}
            />
          </div>
          <div className="sm:col-span-2">
            <TextInput
              label="Source"
              description="Publisher and title, never a link."
              value={slide.source}
              max={LIMITS.statSource}
              error={errorFor(errors, 'source')}
              onChange={(source) => {
                onChange({ ...slide, source });
              }}
              {...common}
            />
          </div>
        </div>
      );
    case 'quote':
      return (
        <div className="grid gap-3">
          <TextArea
            label="Quote"
            required
            value={slide.quote}
            max={LIMITS.quote}
            error={errorFor(errors, 'quote')}
            onChange={(quote) => {
              onChange({ ...slide, quote });
            }}
            {...common}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <TextInput
              label="Who said it"
              required
              value={slide.attribution}
              max={LIMITS.attribution}
              error={errorFor(errors, 'attribution')}
              onChange={(attribution) => {
                onChange({ ...slide, attribution });
              }}
              {...common}
            />
            <TextInput
              label="Their role"
              description="Optional."
              value={slide.role}
              max={LIMITS.role}
              error={errorFor(errors, 'role')}
              onChange={(role) => {
                onChange({ ...slide, role });
              }}
              {...common}
            />
          </div>
        </div>
      );
    case 'sources':
      return (
        <SourcesFields
          title={slide.title}
          sources={slide.sources}
          errors={errors}
          readOnly={readOnly}
          onChange={(title, sources) => {
            onChange({ ...slide, title, sources });
          }}
        />
      );
  }
}

function StepsFields({
  title,
  steps,
  errors,
  readOnly,
  slideName,
  onChange,
}: {
  title: string;
  steps: readonly Step[];
  errors: readonly SpecError[];
  readOnly: boolean;
  slideName: string;
  onChange: (title: string, steps: Step[]) => void;
}) {
  const update = (index: number, step: Step) => {
    onChange(
      title,
      steps.map((current, at) => (at === index ? step : current)),
    );
  };
  return (
    <div className="grid gap-3">
      <TextInput
        label="Title"
        required
        value={title}
        max={LIMITS.slideTitle}
        readOnly={readOnly}
        error={errorFor(errors, 'title')}
        onChange={(value) => {
          onChange(value, [...steps]);
        }}
      />
      <Fieldset
        legend="Steps"
        description={`${String(LIMITS.stepsMin)} to ${String(LIMITS.stepsMax)} steps, in order.`}
        error={errorFor(errors, 'steps')}
      >
        <ol className="grid gap-3">
          {steps.map((step, index) => (
            <li key={index} className="grid gap-2 rounded-md border border-line p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-ink">{`Step ${String(index + 1)}`}</p>
                {!readOnly && steps.length > LIMITS.stepsMin ? (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove step ${String(index + 1)} of ${slideName.toLowerCase()}`}
                    onClick={() => {
                      onChange(
                        title,
                        steps.filter((_, at) => at !== index),
                      );
                    }}
                  >
                    <X aria-hidden="true" />
                  </Button>
                ) : null}
              </div>
              <TextInput
                label={`Step ${String(index + 1)} title`}
                required
                value={step.title}
                max={LIMITS.stepTitle}
                readOnly={readOnly}
                error={errorFor(errors, `steps.${String(index)}.title`)}
                onChange={(value) => {
                  update(index, { ...step, title: value });
                }}
              />
              <TextInput
                label={`Step ${String(index + 1)} detail`}
                description="Optional."
                value={step.detail}
                max={LIMITS.stepDetail}
                readOnly={readOnly}
                error={errorFor(errors, `steps.${String(index)}.detail`)}
                onChange={(value) => {
                  update(index, { ...step, detail: value });
                }}
              />
            </li>
          ))}
        </ol>
        {!readOnly && steps.length < LIMITS.stepsMax ? (
          <div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                onChange(title, [...steps, { title: '', detail: null }]);
              }}
            >
              <Plus aria-hidden="true" />
              Add a step
            </Button>
          </div>
        ) : null}
      </Fieldset>
    </div>
  );
}

function CellsFields({
  cells,
  layered,
  errors,
  readOnly,
  onChange,
}: {
  cells: readonly Cell[];
  layered: boolean;
  errors: readonly SpecError[];
  readOnly: boolean;
  onChange: (cells: Cell[]) => void;
}) {
  const names = ['Top left', 'Top right', 'Bottom left', 'Bottom right'];
  return (
    <Fieldset
      legend={layered ? 'Layers' : 'Quadrants'}
      description={layered ? '3 to 5 layers, outermost first.' : 'Four quadrants, row by row.'}
      error={errorFor(errors, 'cells')}
    >
      <ol className="grid gap-3 sm:grid-cols-2">
        {cells.map((cell, index) => {
          const name = layered ? `Layer ${String(index + 1)}` : (names[index] ?? `Cell ${String(index + 1)}`);
          return (
            <li key={index} className="grid gap-2 rounded-md border border-line p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-ink">{name}</p>
                {!readOnly && layered && cells.length > LIMITS.cellsMin ? (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove ${name.toLowerCase()}`}
                    onClick={() => {
                      onChange(cells.filter((_, at) => at !== index));
                    }}
                  >
                    <X aria-hidden="true" />
                  </Button>
                ) : null}
              </div>
              <TextInput
                label={`${name} label`}
                required
                value={cell.label}
                max={LIMITS.cellLabel}
                readOnly={readOnly}
                error={errorFor(errors, `cells.${String(index)}.label`)}
                onChange={(label) => {
                  onChange(cells.map((current, at) => (at === index ? { ...current, label } : current)));
                }}
              />
              <TextInput
                label={`${name} text`}
                required
                value={cell.text}
                max={LIMITS.cellText}
                readOnly={readOnly}
                error={errorFor(errors, `cells.${String(index)}.text`)}
                onChange={(text) => {
                  onChange(cells.map((current, at) => (at === index ? { ...current, text } : current)));
                }}
              />
            </li>
          );
        })}
      </ol>
      {!readOnly && layered && cells.length < LIMITS.cellsMax ? (
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onChange([...cells, { label: '', text: '' }]);
            }}
          >
            <Plus aria-hidden="true" />
            Add a layer
          </Button>
        </div>
      ) : null}
    </Fieldset>
  );
}

function SourcesFields({
  title,
  sources,
  errors,
  readOnly,
  onChange,
}: {
  title: string;
  sources: readonly Source[];
  errors: readonly SpecError[];
  readOnly: boolean;
  onChange: (title: string, sources: Source[]) => void;
}) {
  const update = (index: number, source: Source) => {
    onChange(
      title,
      sources.map((current, at) => (at === index ? source : current)),
    );
  };
  return (
    <div className="grid gap-3">
      <TextInput
        label="Title"
        value={title}
        max={LIMITS.sourcesTitle}
        readOnly={readOnly}
        error={errorFor(errors, 'title')}
        onChange={(value) => {
          onChange(value, [...sources]);
        }}
      />
      <Fieldset legend="Sources" description="Publisher and title, never a link. Up to five." error={errorFor(errors, 'sources')}>
        <ol className="grid gap-3">
          {sources.map((source, index) => (
            <li key={index} className="grid gap-2 rounded-md border border-line p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_7rem]">
              <TextInput
                label={`Source ${String(index + 1)} publisher`}
                required
                value={source.publisher}
                max={LIMITS.publisher}
                readOnly={readOnly}
                error={errorFor(errors, `sources.${String(index)}.publisher`)}
                onChange={(publisher) => {
                  update(index, { ...source, publisher });
                }}
              />
              <TextInput
                label={`Source ${String(index + 1)} title`}
                required
                value={source.title}
                max={LIMITS.sourceTitle}
                readOnly={readOnly}
                error={errorFor(errors, `sources.${String(index)}.title`)}
                onChange={(value) => {
                  update(index, { ...source, title: value });
                }}
              />
              <TextInput
                label={`Source ${String(index + 1)} date`}
                description="Optional."
                value={source.date}
                max={LIMITS.sourceDate}
                readOnly={readOnly}
                error={errorFor(errors, `sources.${String(index)}.date`)}
                onChange={(date) => {
                  update(index, { ...source, date });
                }}
              />
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-2">
          {!readOnly && sources.length < LIMITS.sourcesMax ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                onChange(title, [...sources, { publisher: '', title: '', date: null }]);
              }}
            >
              <Plus aria-hidden="true" />
              Add a source
            </Button>
          ) : null}
          {!readOnly && sources.length > 1 ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                onChange(title, sources.slice(0, -1));
              }}
            >
              <X aria-hidden="true" />
              Remove the last source
            </Button>
          ) : null}
        </div>
      </Fieldset>
    </div>
  );
}
