import { mediaUrl } from '../model/media';
import { mediaWithRole, type BrandKit, type MediaRef, type Post } from '../model/types';

import type { FeedAuthor, FeedMedia } from './feed-card';

/** The author line of the feed card, from the brand kit (`authorName`, `authorHeadline`, headshot). */
export function feedAuthor(brand: BrandKit | undefined): FeedAuthor {
  const headshot = brand?.headshot;
  const name = brand?.authorName?.trim() ?? '';
  return {
    name: name === '' ? 'You' : name,
    headline: brand?.authorHeadline ?? null,
    avatarUrl: headshot?.container === 'media' ? mediaUrl(headshot) : null,
  };
}

/**
 * What the feed card shows under the text for a post: the document's first page and title
 * (carousel), the image (image card), or a note that the media is not rendered yet.
 */
export function feedMedia(
  post: Pick<Post, 'format' | 'media' | 'documentTitle'>,
  overrides: { cover?: MediaRef | null; documentTitle?: string | null } = {},
): FeedMedia | null {
  switch (post.format) {
    case 'carousel': {
      const pdf = mediaWithRole(post, 'carousel_pdf');
      const cover = overrides.cover ?? mediaWithRole(post, 'carousel_cover');
      const title = overrides.documentTitle ?? post.documentTitle ?? null;
      if (!pdf && !cover) return { kind: 'document', title, pageCount: null, coverUrl: null };
      return {
        kind: 'document',
        title,
        pageCount: pdf?.pageCount ?? null,
        coverUrl: cover ? mediaUrl(cover) : null,
        coverAlt: cover?.altText ?? null,
      };
    }
    case 'image_card': {
      const image = overrides.cover ?? mediaWithRole(post, 'image_card') ?? mediaWithRole(post, 'image');
      return { kind: 'image', url: image ? mediaUrl(image) : null, alt: image?.altText ?? null };
    }
    case 'text':
    case 'poll':
      return null;
  }
}
