import { useId } from 'react';

import { ApprovalHash } from '../posts/components/approval-hash';
import { FeedCard } from '../posts/components/feed-card';
import { feedAuthor, feedMedia } from '../posts/components/feed-parts';
import { formatMoment } from '../posts/model/time';
import type { GoLiveView } from '../posts/model/types';
import { useBrand } from '../posts/queries';

/**
 * "What goes out": the exact final render (text with the phone fold, the document's first page
 * and title, or the image), the alt text, and the approval it is bound to. Nothing here is
 * editable: what you see is what the tap sends.
 */
export function Proof({ view, text, title = 'What goes out' }: { view: GoLiveView; text?: string; title?: string }) {
  const headingId = useId();
  const brand = useBrand();
  const post = view.post.item;
  const shown = text ?? post.displayText;
  const media = feedMedia(post, { cover: view.cover ?? null, documentTitle: view.documentTitle ?? null });
  const altTexts = view.altTexts.filter((alt): alt is string => typeof alt === 'string' && alt.trim() !== '');

  return (
    <section aria-labelledby={headingId} className="grid gap-4">
      <h3 id={headingId} className="font-serif text-xl font-medium text-ink">
        {title}
      </h3>
      <div className="-mx-1 flex justify-center rounded-xl bg-sunken px-2 py-4 shadow-[inset_0_0_0_1px_var(--color-line)] sm:mx-0 sm:px-4">
        <FeedCard
          text={shown}
          width="mobile"
          foldIndex={text === undefined ? view.foldIndex : undefined}
          hashtags={post.hashtags ?? []}
          author={feedAuthor(brand.data?.item)}
          media={media}
        />
      </div>
      <dl className="grid gap-3 text-sm">
        {view.documentTitle ? (
          <div className="grid gap-0.5">
            <dt className="font-medium text-ink">Document title</dt>
            <dd className="text-ink-muted">{view.documentTitle}</dd>
          </div>
        ) : null}
        {altTexts.length > 0 ? (
          <div className="grid gap-0.5">
            <dt className="font-medium text-ink">{altTexts.length === 1 ? 'Alt text' : 'Alt texts'}</dt>
            {altTexts.map((alt, index) => (
              <dd key={`${String(index)}-${alt}`} className="text-ink-muted">
                {alt}
              </dd>
            ))}
          </div>
        ) : null}
        {view.approvalSha256 ? (
          <div className="grid gap-1">
            <dt className="font-medium text-ink">Approval</dt>
            <dd className="grid gap-1 text-ink-muted">
              {post.approval ? <span>{`Approved ${formatMoment(post.approval.approvedAt)}, bound to this text and media.`}</span> : null}
              <ApprovalHash sha256={view.approvalSha256} />
            </dd>
          </div>
        ) : null}
      </dl>
    </section>
  );
}
