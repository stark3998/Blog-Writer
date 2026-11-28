import { useId, type CSSProperties } from 'react';

import type { BrandColorToken } from '../lib/contrast';
import { mediaUrl } from '../lib/media';
import type { MediaRef } from '../lib/types';

export interface BrandPreviewProps {
  /** Valid colours only; a token that is missing falls back to the app's own colours. */
  colors: Partial<Record<BrandColorToken, string>>;
  authorName: string;
  authorHeadline: string;
  headshot: MediaRef | null;
  nameMark: MediaRef | null;
  /** The short disclaimer from the employer policy (the footer of every slide). */
  disclaimer: string | null | undefined;
}

function paint(color: string | undefined, background?: string): CSSProperties | undefined {
  if (color === undefined && background === undefined) return undefined;
  return { ...(color ? { color } : {}), ...(background ? { backgroundColor: background } : {}) };
}

/**
 * Two sample slides in the kit as it is being edited: a cover on ink and a content slide on
 * paper, using every colour token, the name, headline, headshot, name mark and footer. It
 * updates as the owner types; the server's own render (Inter, exact layout) is below it.
 */
export function BrandPreview({ colors, authorName, authorHeadline, headshot, nameMark, disclaimer }: BrandPreviewProps) {
  const captionId = useId();
  const name = authorName.trim() || 'Your name';
  const headline = authorHeadline.trim();
  const footer = disclaimer?.trim() ?? '';

  return (
    <figure aria-labelledby={captionId} className="space-y-3">
      <div aria-hidden="true" className="grid grid-cols-2 gap-3">
        {/* Cover: ink surface, paper text, the signal accent. */}
        <div
          className="flex aspect-[4/5] min-w-0 flex-col justify-between overflow-hidden rounded-md border border-line bg-ink p-4 text-ink-inverse"
          style={paint(colors.paper, colors.ink)}
        >
          <div>
            <span className="block h-1 w-8 rounded-full bg-ink-inverse" style={paint(undefined, colors.signal)} />
            <p className="mt-4 text-base font-bold leading-snug">Three checks before an agent touches production</p>
            <p className="mt-2 text-xs" style={paint(colors.mutedOnInk)}>
              What I ask before any MCP server gets a token.
            </p>
          </div>
          <div>
            <div className="flex items-center gap-2">
              {headshot ? <img src={mediaUrl(headshot.name)} alt="" className="size-8 shrink-0 rounded-full object-cover" /> : null}
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold">{name}</p>
                {headline ? (
                  <p className="truncate text-xs" style={paint(colors.mutedOnInk)}>
                    {headline}
                  </p>
                ) : null}
              </div>
              <span
                className="ml-auto shrink-0 rounded-full bg-ink-inverse px-2 py-0.5 text-xs font-semibold text-ink"
                style={paint(colors.ink, colors.signal)}
              >
                Swipe
              </span>
            </div>
            <span className="mt-3 block h-px bg-line-strong" style={paint(undefined, colors.ruleOnInk)} />
            <p className="mt-1.5 flex justify-between gap-2 text-xs" style={paint(colors.mutedOnInk)}>
              <span className="truncate">{footer}</span>
              <span className="shrink-0 tabular-nums">1/9</span>
            </p>
          </div>
        </div>

        {/* A content slide: paper surface, ink and muted text, signal text, the rule. */}
        <div
          className="flex aspect-[4/5] min-w-0 flex-col justify-between overflow-hidden rounded-md border border-line bg-sheet p-4 text-ink"
          style={paint(colors.ink, colors.paper)}
        >
          <div>
            <p className="text-xs font-semibold" style={paint(colors.signalText)}>
              Check 2 of 3
            </p>
            <p className="mt-2 text-base font-bold leading-snug">Scope every token to one tool</p>
            <p className="mt-2 text-xs text-ink-muted" style={paint(colors.muted)}>
              An agent that holds one credential for ten tools is one prompt injection away from all ten.
            </p>
          </div>
          <div>
            <span className="block h-px bg-line-strong" style={paint(undefined, colors.rule)} />
            <p className="mt-1.5 flex items-center justify-between gap-2 text-xs text-ink-muted" style={paint(colors.muted)}>
              {nameMark ? (
                <img src={mediaUrl(nameMark.name)} alt="" className="h-4 max-w-[50%] object-contain object-left" />
              ) : (
                <span className="truncate">{name}</span>
              )}
              <span className="truncate">{footer}</span>
              <span className="shrink-0 tabular-nums">3/9</span>
            </p>
          </div>
        </div>
      </div>
      <figcaption id={captionId} className="text-sm text-ink-muted">
        A sample cover and content slide with your kit, as you edit it. Slides are set in Inter; this preview uses the app’s
        typeface.
      </figcaption>
    </figure>
  );
}
