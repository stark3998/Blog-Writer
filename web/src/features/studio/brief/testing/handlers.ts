import { http, HttpResponse, type HttpHandler } from 'msw';

import type { components } from '@/lib/api/schema';
import { apiUrl } from '@/test/msw';

import { page, versioned } from '../../piece/testing/fixtures';
import { problemResponse } from '../../piece/testing/handlers';

type Schemas = components['schemas'];

export interface ResearchData {
  runs?: readonly Schemas['ResearchRun'][];
  briefs?: readonly Schemas['Brief'][];
  quick?: readonly Schemas['QuickSearch'][];
  /** The etag every research document answers with. */
  etag?: string;
}

/**
 * The research reads: runs by article, briefs and quick searches by id, quick searches by piece.
 * Writes are per test (`server.use(http.post(...))`).
 */
export function researchHandlers({ runs = [], briefs = [], quick = [], etag = '"etag-1"' }: ResearchData = {}): HttpHandler[] {
  return [
    http.get(apiUrl('/api/research/runs'), ({ request }) => {
      const articleId = new URL(request.url).searchParams.get('articleId');
      return HttpResponse.json(page(runs.filter((run) => !articleId || run.articleId === articleId), null, etag));
    }),
    http.get(apiUrl('/api/research/briefs/:briefId'), ({ params }) => {
      const found = briefs.find((brief) => brief.id === params.briefId);
      return found ? HttpResponse.json(versioned(found, etag), { headers: { ETag: etag } }) : problemResponse(404, 'not_found');
    }),
    http.get(apiUrl('/api/research/quick'), ({ request }) => {
      const query = new URL(request.url).searchParams;
      const articleId = query.get('articleId');
      const postId = query.get('postId');
      const matching = quick.filter(
        (search) => (!articleId || search.articleId === articleId) && (!postId || search.postId === postId),
      );
      return HttpResponse.json(page(matching, null, etag));
    }),
    http.get(apiUrl('/api/research/quick/:quickId'), ({ params }) => {
      const found = quick.find((search) => search.id === params.quickId);
      return found ? HttpResponse.json(versioned(found, etag)) : problemResponse(404, 'not_found');
    }),
  ];
}
