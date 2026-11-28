import { mediaUrl } from '@/lib/urls';

import { plural } from '../model/format';
import { bodyImageCount, heroOf } from '../model/illustrations';
import type { Article } from '../model/types';

/**
 * What an approval covers besides the words: the hero the page opens with and the images in the
 * text (contract revision 5: picking a hero changes the etag the approval binds, an inserted
 * image is part of the content hash). Shown only when the article has images to speak of.
 */
export function ApprovalMedia({ article }: { article: Article }) {
  const hero = heroOf(article);
  const inText = bodyImageCount(article.markdown);
  if (!hero.image && inText === 0 && (article.illustrations ?? []).length === 0) return null;
  return (
    <div role="group" aria-label="Images you approve" className="grid gap-2 rounded-md border border-line p-3 text-sm">
      <p className="font-medium text-ink">Your approval covers the images too.</p>
      <div className="flex items-center gap-3">
        {hero.image ? (
          <img
            src={mediaUrl(hero.image.name)}
            alt=""
            width={hero.image.width ?? undefined}
            height={hero.image.height ?? undefined}
            decoding="async"
            draggable={false}
            className="aspect-[3/2] w-20 shrink-0 rounded-sm border border-line bg-sunken object-cover"
          />
        ) : null}
        <p className="text-ink-muted">
          {hero.image
            ? `Hero image: ${hero.image.altText ?? 'an image you chose'}`
            : 'Hero image: the title card (your title on the brand colours).'}
        </p>
      </div>
      <p className="text-ink-muted">
        {inText === 0 ? 'No images in the text.' : `${plural(inText, 'image', 'images')} in the text.`}
      </p>
    </div>
  );
}
