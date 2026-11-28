import { cn } from '@/lib/cn';

export interface SlidePreviewFrameProps {
  /** Same-origin URL of the server's preview HTML (`/api/posts/{id}/carousel/preview`). */
  src: string;
  /** Names the frame for assistive technology. */
  title: string;
  /** Changing it reloads the frame (the saved slides changed). */
  reloadKey?: string | null;
  className?: string;
}

/**
 * The server's carousel HTML, the exact document the PDF is printed from, in a sandboxed frame:
 * `sandbox` with no permissions (no scripts, no same-origin), loaded by URL and never with
 * `srcdoc` (contract section 10). Slides are 1080 × 1350 px; the frame shows one slide at 30 %
 * and scrolls inside to the next.
 */
export function SlidePreviewFrame({ src, title, reloadKey, className }: SlidePreviewFrameProps) {
  return (
    <div
      data-slot="slide-preview"
      className={cn('relative h-[405px] w-[324px] max-w-full overflow-hidden rounded-md border border-line bg-sunken', className)}
    >
      <iframe
        key={reloadKey ?? 'initial'}
        src={src}
        title={title}
        sandbox=""
        referrerPolicy="no-referrer"
        loading="lazy"
        className="h-[1350px] w-[1080px] origin-top-left scale-[0.3] border-0 bg-sheet"
      />
    </div>
  );
}
