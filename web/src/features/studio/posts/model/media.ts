import { carouselPreviewUrl as previewUrl, mediaUrl as mediaPath } from '@/lib/urls';

import type { MediaRef } from './types';

/**
 * Where a stored file is served (`GET /api/media/{name}`, same origin, sandboxed, immutable).
 * Names are content-addressed paths (`posts/<postId>/<sha256>.png`): `@/lib/urls` keeps the
 * slashes and encodes each segment.
 */
export function mediaUrl(media: Pick<MediaRef, 'name'>): string {
  return mediaPath(media.name);
}

/** The sandboxed carousel preview (`GET /api/posts/{id}/carousel/preview`): the HTML the PDF is printed from. */
export function carouselPreviewUrl(postId: string): string {
  return previewUrl(postId);
}

/** "2.4 MB", "830 kB" (1 kB = 1000 bytes). */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1000) return `${bytes} B`;
  const format = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  if (bytes < 1_000_000) return `${format.format(bytes / 1000)} kB`;
  return `${format.format(bytes / 1_000_000)} MB`;
}

/** "PDF", "PNG image", … for a download's kind. */
export function mediaKindLabel(contentType: MediaRef['contentType']): string {
  switch (contentType) {
    case 'application/pdf':
      return 'PDF';
    case 'image/png':
      return 'PNG image';
    case 'image/jpeg':
      return 'JPEG image';
    case 'image/webp':
      return 'WebP image';
    case 'image/svg+xml':
      return 'SVG image';
  }
}
