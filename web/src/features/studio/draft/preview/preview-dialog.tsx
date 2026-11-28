import { Monitor, RotateCw, Smartphone } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { cn } from '@/lib/cn';
import { mediaUrl } from '@/lib/urls';

import { plural } from '../model/format';
import { previewLeavesOut } from '../model/illustrations';
import type { MediaRef } from '../model/types';

export interface PreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  articleId: string;
  /** A stored version; the current saved text when left out. */
  version?: number;
  /** The article's title, for the frame's name. */
  title: string;
  /**
   * The owner's hero image (an illustration they chose). The frame inlines images up to 2 MB, so a
   * larger hero is shown above it, where the published page has it.
   */
  hero?: MediaRef | undefined;
  /** Images in the text the frame shows as their alt text (over its 2 MB inline limit). */
  largeImages?: number;
}

/** The preview route for an article (and version): same origin, read-only, rendered by the server. */
export function previewUrl(articleId: string, version?: number): string {
  const base = `/api/articles/${encodeURIComponent(articleId)}/preview`;
  return version === undefined ? base : `${base}?version=${String(version)}`;
}

/**
 * "Preview as portfolio page": the publish renderer's HTML, sanitised, with images and diagrams
 * inlined, in a sandboxed iframe (no scripts, no same-origin access; contract section 10). The
 * page itself is never put in this document; a hero too large for the frame to inline is shown
 * above it from `/api/media` (same origin, an `<img>` with its alt text).
 */
export function PreviewDialog({ open, onOpenChange, articleId, version, title, hero, largeImages = 0 }: PreviewDialogProps) {
  const [width, setWidth] = useState<'desktop' | 'mobile'>('desktop');
  const [reloads, setReloads] = useState(0);
  const heroOutside = hero !== undefined && previewLeavesOut(hero) ? hero : undefined;
  // The page's own column: 720 px less its padding, or the phone frame.
  const column = width === 'mobile' ? 'w-[24.375rem] max-w-full' : 'w-full max-w-[42.5rem]';
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[92dvh] w-[calc(100%-1rem)] max-w-6xl flex-col gap-4 p-4 sm:p-6">
        <DialogHeader className="gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="grid gap-1.5">
            <DialogTitle>Preview as portfolio page</DialogTitle>
            <DialogDescription>
              {version === undefined
                ? 'The saved text, rendered the way your portfolio shows it.'
                : `Version ${String(version)}, rendered the way your portfolio shows it.`}
            </DialogDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SegmentedControl
              aria-label="Preview width"
              value={width}
              onValueChange={(value) => {
                setWidth(value === 'mobile' ? 'mobile' : 'desktop');
              }}
            >
              <SegmentedControlItem value="desktop">
                <Monitor aria-hidden="true" />
                Desktop
              </SegmentedControlItem>
              <SegmentedControlItem value="mobile">
                <Smartphone aria-hidden="true" />
                Phone
              </SegmentedControlItem>
            </SegmentedControl>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setReloads((count) => count + 1);
              }}
            >
              <RotateCw aria-hidden="true" />
              Reload
            </Button>
          </div>
        </DialogHeader>
        <div className="flex min-h-0 flex-1 flex-col items-center gap-3 overflow-auto rounded-lg bg-desk p-2 sm:p-4">
          {heroOutside ? (
            <figure className={cn('grid shrink-0 gap-1.5', column)}>
              <img
                src={mediaUrl(heroOutside.name)}
                alt={heroOutside.altText ?? title}
                width={heroOutside.width ?? undefined}
                height={heroOutside.height ?? undefined}
                decoding="async"
                draggable={false}
                className="h-auto w-full rounded-md border border-line bg-sheet"
              />
              <figcaption className="text-xs text-ink-muted">
                The hero image, at the top of the published page. It is over 2 MB, so the preview below leaves it out.
              </figcaption>
            </figure>
          ) : null}
          {largeImages > 0 ? (
            <p className={cn('shrink-0 text-xs text-ink-muted', column)}>
              {`${plural(largeImages, 'image', 'images')} in the text ${largeImages === 1 ? 'is' : 'are'} over 2 MB, so the preview shows ${largeImages === 1 ? 'its' : 'their'} alt text. The published page shows the ${largeImages === 1 ? 'image' : 'images'}.`}
            </p>
          ) : null}
          <iframe
            key={reloads}
            src={previewUrl(articleId, version)}
            title={`Portfolio preview of “${title}”`}
            sandbox=""
            referrerPolicy="no-referrer"
            loading="lazy"
            className={cn(
              'min-h-[24rem] flex-1 shrink-0 rounded-md border border-line bg-sheet',
              width === 'mobile' ? 'w-[24.375rem] max-w-full' : 'w-full',
            )}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
