import { ArrowDown, ArrowUp, ImageUp, LayoutPanelTop, Plus, Sparkles, Trash2 } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { JobTimeline } from '@/features/jobs/job-timeline';
import { cn } from '@/lib/cn';

import { SlidePreviewFrame } from '../components/slide-preview-frame';
import { carouselPreviewUrl } from '../model/media';
import {
  convertSlide,
  LIMITS,
  moveItem,
  newSlide,
  slideHints,
  slideWordLimit,
  slideWords,
  SLIDE_TYPE_HINTS,
  SLIDE_TYPE_LABELS,
  SLIDE_TYPES,
  starterCard,
  starterSpec,
  type CarouselSpec,
  type OpaqueSlide,
  type Slide,
  type SlideType,
  type SpecError,
} from '../model/slides';
import { findingSlide, mediaWithRole, type Finding, type Post } from '../model/types';

import { RenderedMedia } from './rendered-media';
import { SlideFields } from './slide-fields';

/** The DOM id of a slide in the builder ("Go to slide 3" focuses it). */
export function slideDomId(postId: string, index: number): string {
  return `post-${postId}-slide-${String(index)}`;
}

/** Brings slide `index` into view and puts focus on it. */
export function focusSlide(postId: string, index: number): void {
  const element = document.getElementById(slideDomId(postId, index));
  if (!element) return;
  element.scrollIntoView({ block: 'start' });
  element.focus({ preventScroll: true });
}

export interface CarouselBuilderProps {
  post: Post;
  /** The saved post's etag: the preview reloads when it changes. */
  etag: string | null;
  spec: CarouselSpec | undefined;
  onChange: (update: (spec: CarouselSpec | undefined) => CarouselSpec | undefined) => void;
  /** True when the slides on screen differ from the saved ones. */
  dirty: boolean;
  readOnly: boolean;
  /** The server's errors from the last save (`invalid_spec`). */
  errors: readonly SpecError[];
  /** Lint findings (stored or live); those about a slide show under it. */
  findings: readonly Finding[];
  /** The hook, to start a new deck from. */
  hook: string;
  /** `posts.render`: start it; the job's timeline while there is one; busy while it starts or runs. */
  onRender: () => void;
  renderJobId: string | null;
  renderBusy: boolean;
  renderError?: ReactNode;
  /** Asks the writer for slides (`regenerate` with `what: carousel`). */
  onAskWriter?: () => void;
}

/**
 * The carousel (or image card) builder: the document title and the slides, each with its type,
 * word count, the rules it breaks and the server's errors; move, add and remove without dragging.
 * Below it, the server's own HTML of the saved slides in a sandboxed frame, and the rendered PDF.
 */
export function CarouselBuilder({
  post,
  etag,
  spec,
  onChange,
  dirty,
  readOnly,
  errors,
  findings,
  hook,
  onRender,
  renderJobId,
  renderBusy,
  renderError,
  onAskWriter,
}: CarouselBuilderProps) {
  const card = post.format === 'image_card';
  const titleId = useId();
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    document.getElementById(target)?.focus();
  });

  const savedSpec = post.carouselSpec !== null && post.carouselSpec !== undefined;
  const rendered = card ? mediaWithRole(post, 'image_card') : mediaWithRole(post, 'carousel_pdf');

  if (spec === undefined) {
    return (
      <div className="grid gap-4">
        <p className="text-ink-muted">
          {card
            ? 'No card yet. An image card is one slide, rendered with your brand kit at 1080 × 1350.'
            : 'No slides yet. Carousels use 8 to 12 slides at 1080 × 1350: the cover is the hook, the recap comes last.'}
        </p>
        {readOnly ? null : (
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <Button
              variant="secondary"
              onClick={() => {
                const title = post.documentTitle ?? hook.slice(0, LIMITS.titleRecommended);
                onChange(() => (card ? starterCard(title, hook) : starterSpec(title, hook)));
              }}
            >
              <LayoutPanelTop aria-hidden="true" />
              {card ? 'Start the card' : 'Start from a blank deck'}
            </Button>
            {onAskWriter ? (
              <Button variant="ghost" onClick={onAskWriter}>
                <Sparkles aria-hidden="true" />
                {card ? 'Ask the writer for the card' : 'Ask the writer for slides'}
              </Button>
            ) : null}
          </div>
        )}
      </div>
    );
  }

  const hints = slideHints(spec, { card });
  const deckHints = hints.filter((hint) => hint.slide === undefined);
  const deckErrors = errors.filter((error) => error.slide === undefined);
  const count = spec.slides.length;

  const update = (index: number, slide: Slide | OpaqueSlide) => {
    onChange((current) =>
      current ? { ...current, slides: current.slides.map((item, at) => (at === index ? slide : item)) } : current,
    );
  };
  const move = (index: number, to: number, control: 'up' | 'down') => {
    onChange((current) => (current ? { ...current, slides: moveItem(current.slides, index, to) } : current));
    // Keep focus on the moved slide's button; at the end of the list the other button.
    const atEdge = (control === 'up' && to === 0) || (control === 'down' && to === count - 1);
    const kept = atEdge ? (control === 'up' ? 'down' : 'up') : control;
    pendingFocus.current = `${slideDomId(post.id, to)}-${kept}`;
  };
  const remove = (index: number) => {
    onChange((current) => (current ? { ...current, slides: current.slides.filter((_, at) => at !== index) } : current));
    const next = Math.min(index, count - 2);
    pendingFocus.current = next >= 0 ? slideDomId(post.id, next) : `${slideDomId(post.id, 0)}-add`;
  };
  const add = (type: SlideType) => {
    // A new slide goes before a trailing recap and sources, where content belongs.
    const tail = spec.slides.reduceRight(
      (n, slide, index) => (n === index + 1 && (slide.type === 'sources' || slide.type === 'recap') ? index : n),
      count,
    );
    const at = type === 'recap' || type === 'sources' ? count : tail;
    onChange((current) => {
      if (!current) return current;
      const slides = [...current.slides];
      slides.splice(at, 0, newSlide(type));
      return { ...current, slides };
    });
    pendingFocus.current = slideDomId(post.id, at);
  };

  return (
    <div className="grid gap-6">
      <Field
        id={titleId}
        label={card ? 'Title' : 'Document title'}
        description={
          card
            ? 'Names the card. LinkedIn doesn’t show it.'
            : `LinkedIn shows it above the document: a second hook. ${String(spec.title.trim().length)} of ${String(LIMITS.titleRecommended)} characters.`
        }
        required
      >
        <Input
          value={spec.title}
          maxLength={LIMITS.title}
          readOnly={readOnly}
          onChange={(event) => {
            const title = event.target.value;
            onChange((current) => (current ? { ...current, title } : current));
          }}
        />
      </Field>

      {deckHints.length > 0 || deckErrors.length > 0 ? (
        <Alert variant="warn">
          <AlertDescription>
            <ul className="grid gap-1">
              {deckErrors.map((error) => (
                <li key={`error-${error.field ?? ''}-${error.message}`}>{error.message}</li>
              ))}
              {deckHints.map((hint) => (
                <li key={`hint-${hint.message}`}>{hint.message}</li>
              ))}
            </ul>
            <p>These block approval, not saving or previewing.</p>
          </AlertDescription>
        </Alert>
      ) : null}

      <ol className="grid gap-4" aria-label={card ? 'The card' : `Slides (${String(count)})`}>
        {spec.slides.map((slide, index) => {
          const domId = slideDomId(post.id, index);
          const headingId = `${domId}-heading`;
          const slideErrors = errors.filter((error) => error.slide === index);
          const slideHintsHere = hints.filter((hint) => hint.slide === index);
          const slideFindings = findings.filter((finding) => findingSlide(finding) === index);
          const words = slideWords(slide);
          const limit = slideWordLimit(slide);
          const name = card ? 'The card' : `Slide ${String(index + 1)}`;
          return (
            <li
              key={index}
              id={domId}
              tabIndex={-1}
              aria-labelledby={headingId}
              className="grid gap-4 rounded-lg border border-line bg-sheet p-4 outline-offset-2 sm:p-5"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="grid gap-0.5">
                  <h3 id={headingId} className="font-semibold text-ink">
                    {slide.type === 'opaque' ? name : `${name}: ${SLIDE_TYPE_LABELS[slide.type]}`}
                  </h3>
                  {limit !== undefined ? (
                    <p className={cn('text-sm tabular-nums', words > limit ? 'font-medium text-warn' : 'text-ink-muted')}>
                      {`${String(words)} of ${String(limit)} words`}
                    </p>
                  ) : null}
                </div>
                {readOnly ? null : (
                  <div className="flex flex-wrap items-center gap-1">
                    <Select
                      value={slide.type === 'opaque' ? undefined : slide.type}
                      onValueChange={(value) => {
                        if ((SLIDE_TYPES as readonly string[]).includes(value)) update(index, convertSlide(slide, value as SlideType));
                      }}
                    >
                      <SelectTrigger aria-label={`Type of ${name.toLowerCase()}`} className="h-9 w-36 pointer-coarse:h-11">
                        <SelectValue placeholder="Type" />
                      </SelectTrigger>
                      <SelectContent>
                        {SLIDE_TYPES.map((type) => (
                          <SelectItem key={type} value={type}>
                            {SLIDE_TYPE_LABELS[type]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {card ? null : (
                      <>
                        <Button
                          id={`${domId}-up`}
                          variant="ghost"
                          size="icon"
                          aria-label={`Move slide ${String(index + 1)} up`}
                          disabled={index === 0}
                          onClick={() => {
                            move(index, index - 1, 'up');
                          }}
                        >
                          <ArrowUp aria-hidden="true" />
                        </Button>
                        <Button
                          id={`${domId}-down`}
                          variant="ghost"
                          size="icon"
                          aria-label={`Move slide ${String(index + 1)} down`}
                          disabled={index === count - 1}
                          onClick={() => {
                            move(index, index + 1, 'down');
                          }}
                        >
                          <ArrowDown aria-hidden="true" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove slide ${String(index + 1)}`}
                          disabled={count <= 1}
                          onClick={() => {
                            remove(index);
                          }}
                        >
                          <Trash2 aria-hidden="true" />
                        </Button>
                      </>
                    )}
                  </div>
                )}
              </div>

              {slide.type !== 'opaque' ? (
                <p className="-mt-2 text-sm text-ink-subtle">{SLIDE_TYPE_HINTS[slide.type]}</p>
              ) : null}

              {slideHintsHere.length > 0 || slideFindings.length > 0 || slideErrors.some((error) => error.field === undefined) ? (
                <ul className="grid gap-1 text-sm">
                  {slideErrors
                    .filter((error) => error.field === undefined)
                    .map((error) => (
                      <li key={`e-${error.message}`} className="font-medium text-danger">
                        {error.message}
                      </li>
                    ))}
                  {slideFindings.map((finding, at) => (
                    <li
                      key={`f-${finding.code}-${String(at)}`}
                      className={finding.severity === 'block' ? 'font-medium text-danger' : 'text-warn'}
                    >
                      {finding.message}
                    </li>
                  ))}
                  {slideHintsHere.map((hint) => (
                    <li key={`h-${hint.message}`} className="text-warn">
                      {hint.message}
                    </li>
                  ))}
                </ul>
              ) : null}

              {slide.type === 'opaque' ? (
                <p className="text-sm text-ink-muted">
                  This slide’s type can’t be edited here. It stays as it is; move or remove it, or change its type.
                </p>
              ) : (
                <SlideFields
                  slide={slide}
                  slideName={name}
                  readOnly={readOnly}
                  errors={slideErrors}
                  onChange={(next) => {
                    update(index, next);
                  }}
                />
              )}
            </li>
          );
        })}
      </ol>

      {readOnly || card ? null : (
        <div className="flex flex-wrap items-center gap-3">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button id={`${slideDomId(post.id, 0)}-add`} variant="secondary" disabled={count >= LIMITS.slidesHardMax}>
                <Plus aria-hidden="true" />
                Add a slide
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuLabel>Slide type</DropdownMenuLabel>
              {SLIDE_TYPES.map((type) => (
                <DropdownMenuItem
                  key={type}
                  onSelect={() => {
                    add(type);
                  }}
                >
                  <span className="grid">
                    <span className="font-medium">{SLIDE_TYPE_LABELS[type]}</span>
                    <span className="text-xs text-ink-subtle">{SLIDE_TYPE_HINTS[type]}</span>
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          {onAskWriter ? (
            <Button variant="ghost" onClick={onAskWriter}>
              <Sparkles aria-hidden="true" />
              Ask the writer to redo the slides
            </Button>
          ) : null}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[auto_minmax(0,1fr)] lg:items-start">
        <section aria-labelledby={`${titleId}-preview`} className="grid gap-2">
          <h3 id={`${titleId}-preview`} className="font-semibold text-ink">
            Preview
          </h3>
          {savedSpec ? (
            <>
              <SlidePreviewFrame
                src={carouselPreviewUrl(post.id)}
                reloadKey={etag}
                title={card ? 'Preview of the saved image card' : 'Preview of the saved slides; scroll inside it to page through'}
              />
              <p className="max-w-[20.25rem] text-sm text-ink-muted">
                {dirty
                  ? 'This shows the saved slides. Save to see your changes here.'
                  : 'The same document the PDF is printed from. Scroll inside it to page through.'}
              </p>
            </>
          ) : (
            <p className="text-sm text-ink-muted">Save the slides to see them as they will print.</p>
          )}
        </section>

        <section aria-labelledby={`${titleId}-files`} className="grid gap-3">
          <h3 id={`${titleId}-files`} className="font-semibold text-ink">
            Rendered files
          </h3>
          {rendered ? (
            <RenderedMedia post={post} />
          ) : (
            <p className="text-sm text-ink-muted">
              Not rendered yet. Approval covers the exact files, so render them once the slides are final.
            </p>
          )}
          {readOnly ? null : (
            <div className="grid gap-2">
              <div>
                <Button
                  variant="secondary"
                  disabled={dirty || !savedSpec || renderBusy}
                  onClick={onRender}
                >
                  <ImageUp aria-hidden="true" />
                  {rendered ? 'Render again' : card ? 'Render the card' : 'Render the slides'}
                </Button>
              </div>
              {dirty ? <p className="text-sm text-ink-muted">Save the slides first: rendering uses the saved ones.</p> : null}
              {renderError}
              {renderJobId ? (
                <JobTimeline
                  jobId={renderJobId}
                  title={card ? 'Rendering the card' : 'Rendering the slides'}
                  labels={{ render: 'Render with your brand kit', save: 'Check and store the files' }}
                />
              ) : null}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
