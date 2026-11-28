import { Monitor, RotateCw, Smartphone } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { cn } from '@/lib/cn';

export interface PreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  articleId: string;
  /** A stored version; the current saved text when left out. */
  version?: number;
  /** The article's title, for the frame's name. */
  title: string;
}

/** The preview route for an article (and version): same origin, read-only, rendered by the server. */
export function previewUrl(articleId: string, version?: number): string {
  const base = `/api/articles/${encodeURIComponent(articleId)}/preview`;
  return version === undefined ? base : `${base}?version=${String(version)}`;
}

/**
 * "Preview as portfolio page": the publish renderer's HTML, sanitised, with images and diagrams
 * inlined, in a sandboxed iframe (no scripts, no same-origin access; contract section 10). The
 * page itself is never put in this document.
 */
export function PreviewDialog({ open, onOpenChange, articleId, version, title }: PreviewDialogProps) {
  const [width, setWidth] = useState<'desktop' | 'mobile'>('desktop');
  const [reloads, setReloads] = useState(0);
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
        <div className="flex min-h-0 flex-1 justify-center overflow-auto rounded-lg bg-desk p-2 sm:p-4">
          <iframe
            key={reloads}
            src={previewUrl(articleId, version)}
            title={`Portfolio preview of “${title}”`}
            sandbox=""
            referrerPolicy="no-referrer"
            loading="lazy"
            className={cn(
              'h-full shrink-0 rounded-md border border-line bg-sheet',
              width === 'mobile' ? 'w-[24.375rem] max-w-full' : 'w-full',
            )}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
