import type { ApiClient } from '@/lib/api/client';
import { apiRequestWithResponse } from '@/lib/api/request';

/**
 * `GET /api/articles/{id}/export.md`: the canonical document (frontmatter and body) as a file.
 * It is fetched through the app's client, so a 409 `meta_incomplete` comes back as an error
 * the tab can explain, instead of a problem document saved as Markdown.
 */

/** The file name from `Content-Disposition`, or `fallback`. */
export function attachmentName(header: string | null, fallback: string): string {
  const match = header ? /filename="([^"]+)"/i.exec(header) : null;
  const name = match?.[1]?.trim();
  return name && /^[\w.() -]+$/.test(name) ? name : fallback;
}

export async function fetchMarkdownExport(
  api: ApiClient,
  articleId: string,
  fallbackName: string,
): Promise<{ readonly name: string; readonly text: string }> {
  const { data, response } = await apiRequestWithResponse(api, 'get', '/api/articles/{article_id}/export.md', {
    params: { path: { article_id: articleId } },
    parseAs: 'text', // text/markdown, typed `string`
    headers: { Accept: 'text/markdown, application/problem+json' },
  });
  return {
    name: attachmentName(response.headers.get('Content-Disposition'), fallbackName),
    text: data,
  };
}

/** Saves text as a file through a temporary link (a blob URL, revoked straight after). */
export function saveTextFile(name: string, text: string, type = 'text/markdown;charset=utf-8'): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.rel = 'noopener';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}
