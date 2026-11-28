import { FileText, Globe, ImageOff, MessageSquare, Repeat2, Send, ThumbsUp } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

import { DESKTOP_FOLD, foldIndex, hashtagSpans, MOBILE_FOLD, utf16Offset } from '../model/text';

export type FeedWidth = 'mobile' | 'desktop';

export interface FeedAuthor {
  name: string;
  headline?: string | null;
  /** Same-origin URL of the headshot (`/api/media/...`); initials without one. */
  avatarUrl?: string | null;
}

/** What sits under the text: a document (carousel), an image, or media that is not rendered yet. */
export type FeedMedia =
  | {
      kind: 'document';
      title: string | null;
      pageCount?: number | null;
      /** The first page as an image. */
      coverUrl?: string | null;
      coverAlt?: string | null;
    }
  | { kind: 'image'; url: string | null; alt: string | null }
  | { kind: 'pending'; label: string };

export interface FeedCardProps {
  /** The display text, exactly as it goes out. */
  text: string;
  width: FeedWidth;
  /**
   * The phone fold as the server measured it (a code-point index, `foldIndex` of the lint or
   * go-live view). The same rule computes it when absent; the desktop fold always comes from the
   * desktop model.
   */
  foldIndex?: number;
  /** Approved hashtags: they show as hashtags, as LinkedIn links them. */
  hashtags?: readonly string[];
  author: FeedAuthor;
  media?: FeedMedia | null;
  /** Start with the feed's own view (text up to the fold, then "…see more"). Default: the proof with the fold marked. */
  defaultCollapsed?: boolean;
  className?: string;
}

const WIDTH_CLASS: Readonly<Record<FeedWidth, string>> = {
  mobile: 'max-w-[22.5rem]',
  desktop: 'max-w-[34.75rem]',
};

const WIDTH_NAME: Readonly<Record<FeedWidth, string>> = {
  mobile: 'phone',
  desktop: 'desktop',
};

/** "Jay Crapple" -> "JC". */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/u).filter(Boolean);
  const letters = parts.length > 1 ? [parts[0], parts.at(-1)] : parts.slice(0, 1);
  return letters
    .map((part) => Array.from(part ?? '')[0] ?? '')
    .join('')
    .toLocaleUpperCase();
}

/** `text` with the approved hashtags set apart (they become links on LinkedIn). */
function withHashtags(text: string, offset: number, spans: readonly { start: number; end: number }[]): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    const start = span.start - offset;
    const end = span.end - offset;
    if (end <= 0 || start >= text.length) continue;
    const from = Math.max(start, 0);
    const to = Math.min(end, text.length);
    if (from > cursor) nodes.push(text.slice(cursor, from));
    nodes.push(
      <span key={`${String(span.start)}-${String(span.end)}`} className="font-semibold text-ink">
        {text.slice(from, to)}
      </span>,
    );
    cursor = to;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

/**
 * A LinkedIn feed card as the post will appear: author line, the text with its fold, the
 * document or image, and the action row. The fold marker is the point of the preview: the feed
 * shows the text above it, then "…see more". "Show it as the feed does" cuts the text there,
 * exactly as a reader first sees it.
 */
export function FeedCard({
  text,
  width,
  foldIndex: serverFold,
  hashtags = [],
  author,
  media,
  defaultCollapsed = false,
  className,
}: FeedCardProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const foldNoteId = useId();
  const fold = width === 'mobile' ? (serverFold ?? foldIndex(text, MOBILE_FOLD)) : foldIndex(text, DESKTOP_FOLD);
  const cut = utf16Offset(text, fold);
  const above = text.slice(0, cut);
  const below = text.slice(cut);
  const folds = below.trim() !== '';
  const spans = hashtagSpans(text, hashtags);
  const name = author.name.trim() === '' ? 'You' : author.name;

  return (
    <figure
      aria-label={`LinkedIn preview, ${WIDTH_NAME[width]} width`}
      data-width={width}
      className={cn('m-0 w-full', WIDTH_CLASS[width], className)}
    >
      <div className="rounded-lg border border-line bg-sheet text-ink">
        <div className="flex items-start gap-3 px-4 pt-3">
          {author.avatarUrl ? (
            <img
              src={author.avatarUrl}
              alt=""
              className={cn('shrink-0 rounded-full object-cover', width === 'mobile' ? 'size-10' : 'size-12')}
            />
          ) : (
            <span
              aria-hidden="true"
              className={cn(
                'flex shrink-0 items-center justify-center rounded-full bg-sunken font-semibold text-ink-muted ring-1 ring-line',
                width === 'mobile' ? 'size-10 text-sm' : 'size-12 text-base',
              )}
            >
              {initials(name)}
            </span>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold leading-5">{name}</p>
            {author.headline ? <p className="truncate text-xs leading-4 text-ink-muted">{author.headline}</p> : null}
            <p className="flex items-center gap-1 text-xs leading-4 text-ink-subtle">
              Now
              <Globe aria-hidden="true" className="size-3" />
              <span className="sr-only">, visible to anyone</span>
            </p>
          </div>
        </div>

        <div className="px-4 pb-3 pt-2 text-sm leading-5">
          {text.trim() === '' ? (
            <p className="text-ink-subtle">Nothing written yet.</p>
          ) : collapsed && folds ? (
            <p className="whitespace-pre-wrap break-words">
              {withHashtags(above.trimEnd(), 0, spans)}
              <span aria-hidden="true">…</span>
              <button
                type="button"
                onClick={() => {
                  setCollapsed(false);
                }}
                className="ml-0.5 font-medium text-ink-muted hover:text-ink hover:underline pointer-coarse:py-2"
              >
                see more
              </button>
            </p>
          ) : (
            <>
              <p className="whitespace-pre-wrap break-words">{withHashtags(above, 0, spans)}</p>
              {folds ? (
                <>
                  <div
                    role="separator"
                    aria-labelledby={foldNoteId}
                    data-slot="fold-marker"
                    className="relative my-1.5 flex items-center gap-2"
                  >
                    <span aria-hidden="true" className="h-0 flex-1 border-t border-dashed border-ink" />
                    <span id={foldNoteId} className="shrink-0 text-xs font-medium text-ink">
                      <span aria-hidden="true">…see more</span>
                      <span className="sr-only">
                        The fold: the feed shows the text above, then “see more”.
                      </span>
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap break-words">{withHashtags(below, cut, spans)}</p>
                </>
              ) : null}
            </>
          )}
        </div>

        {media ? <FeedMediaBlock media={media} /> : null}

        <div
          aria-hidden="true"
          className="flex items-center justify-between gap-2 border-t border-line px-4 py-2 text-xs font-medium text-ink-subtle"
        >
          <span className="flex items-center gap-1.5">
            <ThumbsUp className="size-4" />
            Like
          </span>
          <span className="flex items-center gap-1.5">
            <MessageSquare className="size-4" />
            Comment
          </span>
          <span className="flex items-center gap-1.5">
            <Repeat2 className="size-4" />
            Repost
          </span>
          <span className="flex items-center gap-1.5">
            <Send className="size-4" />
            Send
          </span>
        </div>
      </div>
      {folds ? (
        <figcaption className="mt-2 text-sm">
          <button
            type="button"
            onClick={() => {
              setCollapsed((value) => !value);
            }}
            className="font-medium text-ink-muted underline decoration-line-strong underline-offset-4 hover:text-ink hover:decoration-ink pointer-coarse:py-2"
          >
            {collapsed ? 'Show the whole post' : 'Show it as the feed does'}
          </button>
        </figcaption>
      ) : null}
    </figure>
  );
}

function FeedMediaBlock({ media }: { media: FeedMedia }) {
  switch (media.kind) {
    case 'document':
      return (
        <div className="border-t border-line">
          <p className="flex items-baseline justify-between gap-3 px-4 py-2 text-sm">
            <span className="min-w-0 truncate font-semibold">{media.title ?? 'Untitled document'}</span>
            {media.pageCount ? (
              <span className="shrink-0 text-xs text-ink-subtle">
                {media.pageCount} {media.pageCount === 1 ? 'page' : 'pages'}
              </span>
            ) : null}
          </p>
          {media.coverUrl ? (
            <img
              src={media.coverUrl}
              alt={media.coverAlt ?? 'The first page of the document'}
              className="aspect-[4/5] w-full bg-sunken object-cover"
            />
          ) : (
            <MediaPlaceholder icon={<FileText aria-hidden="true" className="size-6" />} label="The first page shows here once the slides are rendered." />
          )}
        </div>
      );
    case 'image':
      return media.url ? (
        <img
          src={media.url}
          alt={media.alt ?? ''}
          className="aspect-[4/5] w-full border-t border-line bg-sunken object-cover"
        />
      ) : (
        <MediaPlaceholder icon={<ImageOff aria-hidden="true" className="size-6" />} label="The image shows here once it is rendered." />
      );
    case 'pending':
      return <MediaPlaceholder icon={<FileText aria-hidden="true" className="size-6" />} label={media.label} />;
  }
}

function MediaPlaceholder({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <div className="flex aspect-[4/5] w-full flex-col items-center justify-center gap-2 border-t border-line bg-sunken px-6 text-center text-sm text-ink-muted">
      {icon}
      <p>{label}</p>
    </div>
  );
}
