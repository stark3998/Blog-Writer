import type { ApiClient } from '@/lib/api/client';
import { isProblem } from '@/lib/api/problems';
import { apiRequest } from '@/lib/api/request';
import type { components } from '@/lib/api/schema';

/**
 * Live Mermaid previews in the editor: `POST /api/render/mermaid {source}` answers the diagram as
 * a sanitised SVG (strict Mermaid, sanitised by the render package, the portfolio preview's
 * look) with its size. 422 `mermaid_error` for a diagram Mermaid cannot read, 422
 * `diagram_too_long` past the renderer's limit, 503 `render_unavailable` when rendering is off.
 */

/** The answer. The SVG is only ever shown as an image (a base64 `data:` URI), never inlined. */
export type MermaidRender = components['schemas']['MermaidOut'];

/** The renderer's limit for one diagram's source (`MAX_MERMAID_SOURCE`): longer ones never render. */
export const MERMAID_PREVIEW_MAX_CHARS = 3_000;

export async function renderMermaid(api: ApiClient, source: string, signal?: AbortSignal): Promise<MermaidRender> {
  return apiRequest(api, 'post', '/api/render/mermaid', { body: { source }, signal });
}

/** True when this server cannot render at all right now (no renderer, or its browser is missing). */
export function isRenderUnavailable(error: unknown): boolean {
  return isProblem(error, 'render_unavailable');
}

/**
 * `data:image/svg+xml;base64,...` for an SVG: an image source, so the markup is never parsed into
 * the page (an SVG loaded as an image runs no script and loads nothing).
 */
export function svgImageSrc(svg: string): string {
  const bytes = new TextEncoder().encode(svg);
  let binary = '';
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return `data:image/svg+xml;base64,${btoa(binary)}`;
}

/** A short stable hash of a diagram's source, for its cache key (the source itself never goes in a key). */
export function sourceHash(source: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let index = 0; index < source.length; index += 1) {
    const code = source.charCodeAt(index);
    h1 = Math.imul(h1 ^ code, 2654435761);
    h2 = Math.imul(h2 ^ code, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return `${(h2 >>> 0).toString(16).padStart(8, '0')}${(h1 >>> 0).toString(16).padStart(8, '0')}-${String(source.length)}`;
}
