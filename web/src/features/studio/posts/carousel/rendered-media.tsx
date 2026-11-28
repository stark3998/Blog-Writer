import { Download, FileText } from 'lucide-react';

import { buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/cn';

import { formatBytes, mediaUrl } from '../model/media';
import { mediaWithRole, type Post } from '../model/types';

/**
 * What the last render stored (`posts.render`): for a carousel the PDF (one page per slide) and
 * its first page as an image; for an image card the PNG. These exact files are what approval
 * binds and what goes to LinkedIn. Nothing here is fetched as HTML: images and a download link only.
 */
export function RenderedMedia({ post }: { post: Pick<Post, 'format' | 'media'> }) {
  if (post.format === 'image_card') {
    const card = mediaWithRole(post, 'image_card');
    if (!card) return null;
    return (
      <figure className="m-0 grid gap-2">
        <img
          src={mediaUrl(card)}
          alt={card.altText ?? 'The rendered image card'}
          className="aspect-[4/5] w-full max-w-[16rem] rounded-md border border-line bg-sunken object-cover"
        />
        <figcaption className="grid gap-1 text-sm text-ink-muted">
          <span>{`PNG, ${formatBytes(card.sizeBytes)}${card.width && card.height ? `, ${String(card.width)} × ${String(card.height)}` : ''}`}</span>
          {card.altText ? <span>{`Alt text: ${card.altText}`}</span> : null}
          <a
            href={mediaUrl(card)}
            download={card.filename ?? 'image-card.png'}
            className={cn(buttonVariants({ variant: 'link', size: 'sm' }), 'w-fit')}
          >
            <Download aria-hidden="true" />
            Download the image
          </a>
        </figcaption>
      </figure>
    );
  }

  const pdf = mediaWithRole(post, 'carousel_pdf');
  const cover = mediaWithRole(post, 'carousel_cover');
  if (!pdf) return null;
  const pages = pdf.pageCount ?? 0;
  return (
    <div className="grid gap-3 sm:grid-cols-[8rem_minmax(0,1fr)] sm:items-start">
      {cover ? (
        <img
          src={mediaUrl(cover)}
          alt={cover.altText ? `Page 1: ${cover.altText}` : 'Page 1 of the rendered PDF'}
          className="aspect-[4/5] w-32 rounded-md border border-line bg-sunken object-cover"
        />
      ) : (
        <div className="flex aspect-[4/5] w-32 items-center justify-center rounded-md border border-line bg-sunken">
          <FileText aria-hidden="true" className="size-6 text-ink-subtle" />
        </div>
      )}
      <div className="grid gap-1 text-sm">
        <p className="font-medium text-ink">{pdf.filename ?? 'carousel.pdf'}</p>
        <p className="text-ink-muted">
          {`${pages > 0 ? `${String(pages)} ${pages === 1 ? 'page' : 'pages'}, ` : ''}${formatBytes(pdf.sizeBytes)}. Page 1 is what the feed shows first.`}
        </p>
        <a
          href={mediaUrl(pdf)}
          download={pdf.filename ?? 'carousel.pdf'}
          className={cn(buttonVariants({ variant: 'link', size: 'sm' }), 'w-fit')}
        >
          <Download aria-hidden="true" />
          Download the PDF
        </a>
      </div>
    </div>
  );
}
