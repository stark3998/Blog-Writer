import type { ApiClient } from '@/lib/api/client';
import { apiRequest } from '@/lib/api/request';
import { mediaUrl } from '@/lib/urls';

import type { MediaRef } from '../model/types';

/**
 * `POST /api/media` takes the image as a raw body (no multipart) with its own content type and
 * answers the stored `MediaRef` (contract W-A1); the file goes through the app's client
 * (session handling, CSRF) as it is.
 */

export const UPLOAD_TYPES = ['image/png', 'image/jpeg', 'image/webp'] as const;
export type UploadType = (typeof UPLOAD_TYPES)[number];

/** The server's default cap (`BW_STORAGE_MAX_UPLOAD_BYTES`); the server's own answer wins. */
export const UPLOAD_MAX_BYTES = 10 * 1024 * 1024;

export function isUploadType(type: string): type is UploadType {
  return (UPLOAD_TYPES as readonly string[]).includes(type);
}

/** The name the server accepts (`^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,119}$`), or `undefined`. */
export function uploadFilename(name: string): string | undefined {
  const cleaned = name
    .replace(/[^A-Za-z0-9 ._()-]+/g, '-')
    .replace(/^[^A-Za-z0-9]+/, '')
    .slice(0, 120)
    .trim();
  return cleaned === '' ? undefined : cleaned;
}

/** What the file input can say before anything is sent; `undefined` when the file can go. */
export function uploadProblem(file: Pick<File, 'type' | 'size'>): string | undefined {
  if (!isUploadType(file.type)) return 'Use a PNG, JPEG or WebP image.';
  if (file.size > UPLOAD_MAX_BYTES) return 'The image is over 10 MB. Use a smaller one.';
  return undefined;
}

/** The `src` an uploaded image has in the article (same-origin; the preview and publishing inline it). */
export function mediaSrc(media: Pick<MediaRef, 'name'>): string {
  return mediaUrl(media.name);
}

export async function uploadImage(api: ApiClient, file: File, signal?: AbortSignal): Promise<MediaRef> {
  const filename = uploadFilename(file.name);
  return apiRequest(api, 'post', '/api/media', {
    params: { query: { role: 'image', ...(filename ? { filename } : {}) } },
    body: file,
    bodySerializer: (body) => body,
    headers: { 'Content-Type': file.type },
    signal,
  });
}
