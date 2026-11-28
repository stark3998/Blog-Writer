import { Wand2 } from 'lucide-react';
import { useId } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { FRONTMATTER_LIMITS } from '@/features/editor';
import { cn } from '@/lib/cn';
import { PILLAR_THREAD_CLASS, type PillarId } from '@/lib/pillars';
import { useFormatPacks, usePillars } from '@/features/studio/piece';

import { formatNumber } from '../model/format';
import { parseTags, slugify, type MetaDraft, type MetaErrors } from '../model/meta';

function isPillarId(id: string): id is PillarId {
  return id === 'agents' || id === 'identity' || id === 'governance';
}

function PillarSwatch({ id }: { id: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn('inline-block h-3 w-1 rounded-full', isPillarId(id) ? PILLAR_THREAD_CLASS[id] : 'bg-line-strong')}
    />
  );
}

export interface DetailsPanelProps {
  meta: MetaDraft;
  errors: MetaErrors;
  onChange: (next: MetaDraft) => void;
  /** Editable only where the owner writes. */
  readOnly: boolean;
  /** The slug the article was published under (immutable once live; a change redirects). */
  publishedSlug: string | null;
}

/** The article details (dialect frontmatter): saved with the text, checked as you type. */
export function DetailsPanel({ meta, errors, onChange, readOnly, publishedSlug }: DetailsPanelProps) {
  const headingId = useId();
  const pillars = usePillars();
  const formats = useFormatPacks();
  const pillarOptions = pillars.data?.item.pillars ?? [];
  const packs = formats.data?.item.packs ?? [];
  const set = (patch: Partial<MetaDraft>) => {
    onChange({ ...meta, ...patch });
  };
  const series = meta.series;
  const tagCount = parseTags(meta.tags).length;
  const renamed = publishedSlug !== null && meta.slug !== '' && meta.slug !== publishedSlug;

  return (
    <section aria-labelledby={headingId} className="grid gap-4">
      <h2 id={headingId} className="font-serif text-xl font-medium text-ink">
        Article details
      </h2>
      <p className="-mt-2 text-sm text-ink-muted">
        The title, address and summary readers see. All but the series are needed to publish.
      </p>
      <fieldset disabled={readOnly} className="m-0 grid min-w-0 gap-4 border-0 p-0">
        <legend className="sr-only">Article details</legend>
        <Field label="Title" error={errors.title} description={`Up to ${String(FRONTMATTER_LIMITS.title)} characters, plain text.`}>
          <Input
            value={meta.title}
            onChange={(event) => {
              set({ title: event.target.value });
            }}
          />
        </Field>
        <Field label="Excerpt" error={errors.excerpt} description="One or two sentences for the blog index and link previews.">
          <Textarea
            rows={3}
            value={meta.excerpt}
            onChange={(event) => {
              set({ excerpt: event.target.value });
            }}
          />
        </Field>
        <Field
          label="Slug"
          error={errors.slug}
          description={meta.slug ? `The address: /blog/${meta.slug}` : 'The page address after /blog/.'}
        >
          {(control) => (
            <div className="flex gap-2">
              <Input
                {...control}
                value={meta.slug}
                spellCheck={false}
                autoComplete="off"
                onChange={(event) => {
                  set({ slug: event.target.value });
                }}
              />
              <Button
                variant="secondary"
                size="icon"
                aria-label="Make the slug from the title"
                disabled={readOnly || meta.title.trim() === ''}
                onClick={() => {
                  set({ slug: slugify(meta.title) });
                }}
              >
                <Wand2 aria-hidden="true" />
              </Button>
            </div>
          )}
        </Field>
        {renamed ? (
          <Alert variant="warn">
            <AlertDescription>
              <p>{`It was published at /blog/${publishedSlug}. Approving the new slug moves it there, and the old address redirects.`}</p>
            </AlertDescription>
          </Alert>
        ) : null}
        <Field label="Pillar" error={errors.pillar}>
          {(control) => (
            <Select
              value={meta.pillar === '' ? undefined : meta.pillar}
              onValueChange={(value) => {
                set({ pillar: value });
              }}
              disabled={readOnly}
            >
              <SelectTrigger {...control}>
                <SelectValue placeholder={pillars.isPending ? 'Loading pillars…' : 'Choose a pillar'} />
              </SelectTrigger>
              <SelectContent>
                {pillarOptions.map((pillar) => (
                  <SelectItem key={pillar.id} value={pillar.id}>
                    <span className="flex items-center gap-2">
                      <PillarSwatch id={pillar.id} />
                      {pillar.name}
                    </span>
                  </SelectItem>
                ))}
                {meta.pillar !== '' && !pillarOptions.some((pillar) => pillar.id === meta.pillar) ? (
                  <SelectItem value={meta.pillar}>{meta.pillar}</SelectItem>
                ) : null}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field label="Format" error={errors.format}>
          {(control) => (
            <Select
              value={meta.format === '' ? undefined : meta.format}
              onValueChange={(value) => {
                set({ format: value });
              }}
              disabled={readOnly}
            >
              <SelectTrigger {...control}>
                <SelectValue placeholder={formats.isPending ? 'Loading formats…' : 'Choose a format'} />
              </SelectTrigger>
              <SelectContent>
                {packs.map((pack) => (
                  <SelectItem key={pack.id} value={pack.id}>
                    {`${pack.name}, ${formatNumber(pack.minWords)}–${formatNumber(pack.maxWords)} words`}
                  </SelectItem>
                ))}
                {meta.format !== '' && !packs.some((pack) => pack.id === meta.format) ? (
                  <SelectItem value={meta.format}>{meta.format}</SelectItem>
                ) : null}
              </SelectContent>
            </Select>
          )}
        </Field>
        <Field
          label="Tags"
          error={errors.tags}
          description={`Up to ${String(FRONTMATTER_LIMITS.tags)}, separated by commas, like agents, mcp. ${String(tagCount)} now.`}
        >
          <Input
            value={meta.tags}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => {
              set({ tags: event.target.value });
            }}
          />
        </Field>
        <Field orientation="horizontal" label="Part of a series">
          <Checkbox
            checked={series !== null}
            onCheckedChange={(checked) => {
              set({ series: checked === true ? { id: '', title: '', part: '1', total: '' } : null });
            }}
          />
        </Field>
        {series ? (
          <div className="grid gap-4 border-l-2 border-line pl-4">
            <Field label="Series id" error={errors['series.id']} description="A slug shared by every part.">
              <Input
                value={series.id}
                spellCheck={false}
                onChange={(event) => {
                  set({ series: { ...series, id: event.target.value } });
                }}
              />
            </Field>
            <Field label="Series title" error={errors['series.title']}>
              <Input
                value={series.title}
                onChange={(event) => {
                  set({ series: { ...series, title: event.target.value } });
                }}
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Part" error={errors['series.part']}>
                <Input
                  inputMode="numeric"
                  value={series.part}
                  onChange={(event) => {
                    set({ series: { ...series, part: event.target.value } });
                  }}
                />
              </Field>
              <Field label="Of" error={errors['series.total']} description="Optional.">
                <Input
                  inputMode="numeric"
                  value={series.total}
                  onChange={(event) => {
                    set({ series: { ...series, total: event.target.value } });
                  }}
                />
              </Field>
            </div>
          </div>
        ) : null}
      </fieldset>
    </section>
  );
}
