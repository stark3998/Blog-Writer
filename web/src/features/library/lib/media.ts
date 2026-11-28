import type { Client } from 'openapi-fetch';

import { apiRequest } from '@/lib/api/request';
import type { paths } from '@/lib/api/schema';
import { mediaUrl as mediaPath } from '@/lib/urls';

import type { MediaRef } from './types';

/** What `POST /api/media` accepts (`storage`: PNG, JPEG or WebP, re-encoded on the server). */
export const UPLOAD_TYPES = ['image/png', 'image/jpeg', 'image/webp'] as const;
export type UploadType = (typeof UPLOAD_TYPES)[number];

/** `BW_STORAGE_MAX_UPLOAD_BYTES` (the server's default; it refuses larger bodies with 413). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export type UploadRole = 'headshot' | 'name_mark';

const EXTENSIONS: Readonly<Record<UploadType, string>> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
};

function isUploadType(type: string): type is UploadType {
  return (UPLOAD_TYPES as readonly string[]).includes(type);
}

/** Why a file cannot be uploaded, checked before any byte is sent; `undefined` when it can. */
export function uploadProblem(file: Pick<File, 'type' | 'size'>): string | undefined {
  if (!isUploadType(file.type)) return 'Choose a PNG, JPEG or WebP image.';
  if (file.size === 0) return 'That file is empty.';
  if (file.size > MAX_UPLOAD_BYTES) return 'Choose an image of 10 MB or less.';
  return undefined;
}

/**
 * The same-origin address of a stored media file (`GET /api/media/{name}`). Names have
 * folders (`uploads/2026-11/<sha256>.png`): `@/lib/urls` keeps the slashes.
 */
export function mediaUrl(name: string): string {
  return mediaPath(name);
}

/**
 * Uploads an image as the raw request body (`POST /api/media?role=`: the route reads the bytes
 * themselves, PNG, JPEG or WebP, no multipart). The file's own name is not sent: a local file
 * name can carry a client's name, so the stored name is generic (`headshot.png`).
 */
export async function uploadMedia(api: Client<paths>, file: File, role: UploadRole): Promise<MediaRef> {
  const type = file.type;
  if (!isUploadType(type)) throw new TypeError('unsupported upload type');
  const bytes = await file.arrayBuffer();
  return apiRequest(api, 'post', '/api/media', {
    params: { query: { role, filename: `${role === 'headshot' ? 'headshot' : 'name-mark'}.${EXTENSIONS[type]}` } },
    body: bytes,
    bodySerializer: (body) => body,
    headers: { 'Content-Type': type },
  });
}
