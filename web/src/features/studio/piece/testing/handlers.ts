import { http, HttpResponse, type HttpHandler } from 'msw';

import type { components } from '@/lib/api/schema';
import { apiUrl } from '@/test/msw';

import {
  articleDoc,
  FORMAT_PACKS,
  MACHINES,
  page,
  PILLAR_SETTINGS,
  postDoc,
  versioned,
} from './fixtures';
import { machinesResponse } from '@/test/machines';

type Schemas = components['schemas'];

/**
 * msw handlers for the Studio: the settings and machines every Studio screen reads, documents by
 * id, and lists. Install with `server.use(...)` in each test.
 */

/** A problem response with extension members (`current`, `findings`, `errors`, `missing`). */
export function problemResponse(
  status: number,
  code: string,
  extensions: Record<string, unknown> = {},
  requestId = 'req-studio-1',
): Response {
  return HttpResponse.json(
    { type: 'about:blank', title: 'Error', status, code, request_id: requestId, ...extensions },
    { status, headers: { 'Content-Type': 'application/problem+json', 'X-Request-Id': requestId } },
  );
}

/** `GET /api/meta/machines`, `/api/kb/pillars`, `/api/kb/formats`. */
export function settingsHandlers(): HttpHandler[] {
  return [
    http.get(apiUrl('/api/meta/machines'), () => HttpResponse.json(machinesResponse(MACHINES))),
    http.get(apiUrl('/api/kb/pillars'), () => HttpResponse.json(versioned(PILLAR_SETTINGS))),
    http.get(apiUrl('/api/kb/formats'), () => HttpResponse.json(versioned(FORMAT_PACKS))),
  ];
}

/** `GET /api/articles/{id}` answering from `articles` (404 for any other id). */
export function articleHandler(articles: readonly Schemas['Article'][], etag = '"etag-1"'): HttpHandler {
  return http.get(apiUrl('/api/articles/:articleId'), ({ params }) => {
    const found = articles.find((item) => item.id === params.articleId);
    return found
      ? HttpResponse.json(versioned(found, etag), { headers: { ETag: etag } })
      : problemResponse(404, 'not_found');
  });
}

/** `GET /api/posts/{id}` answering from `posts` (404 for any other id). */
export function postHandler(posts: readonly Schemas['Post'][], etag = '"etag-1"'): HttpHandler {
  return http.get(apiUrl('/api/posts/:postId'), ({ params }) => {
    const found = posts.find((item) => item.id === params.postId);
    return found ? HttpResponse.json(versioned(found, etag), { headers: { ETag: etag } }) : problemResponse(404, 'not_found');
  });
}

/**
 * Empty Studio answers for the shared shell tests (`src/app/routes.test.tsx`,
 * `src/components/shell/navigation.test.tsx`): any article id is an untitled planned article,
 * any `post-` id an untitled draft post, and every list is empty.
 */
export function studioDefaultHandlers(): HttpHandler[] {
  return [
    ...settingsHandlers(),
    http.get(apiUrl('/api/articles'), () => HttpResponse.json(page([]))),
    http.get(apiUrl('/api/posts'), () => HttpResponse.json(page([]))),
    http.get(apiUrl('/api/articles/:articleId'), ({ params }) =>
      HttpResponse.json(
        versioned(articleDoc({ id: String(params.articleId), meta: { title: null, tags: [] } })),
      ),
    ),
    http.get(apiUrl('/api/posts/:postId'), ({ params }) =>
      HttpResponse.json(versioned(postDoc({ id: String(params.postId), displayText: '' }))),
    ),
    http.get(apiUrl('/api/research/runs'), () => HttpResponse.json(page([]))),
    http.get(apiUrl('/api/research/quick'), () => HttpResponse.json(page([]))),
  ];
}
