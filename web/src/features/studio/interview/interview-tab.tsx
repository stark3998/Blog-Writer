import { useEffect, useState } from 'react';

import { pieceKind } from '@/app/nav';
import { EmptyState } from '@/components/shell/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useLiveEvents } from '@/features/jobs/use-job';
import { PROBLEM_MESSAGES } from '@/lib/api/problems';
import type { components } from '@/lib/api/schema';
import { useAppData } from '@/lib/app-data';
import { useApiInfiniteQuery, useApiQuery } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import type { PieceTabProps } from '../piece/piece-tab';

import { draftStoreFor } from './draft-store';
import { EarlierInterviews, InterviewRecord } from './interview-record';
import { InterviewSession } from './interview-session';
import { isOpenInterview, newerEntry, pickInterviews, type InterviewEntry } from './interview-model';

type ArticleStatus = components['schemas']['ArticleStatus'];

/** The article statuses in which the pipeline is at work on the questions. */
const BEFORE_QUESTIONS: ReadonlySet<ArticleStatus> = new Set(['researching', 'interviewing']);

/**
 * Studio > Interview (`/studio/:id/interview`, phone-first; push notifications open it): a micro
 * (two questions) or full (five to eight) interview, one question per screen, with dictation,
 * the redaction proposal to confirm, and skip. Calls `GET /api/interviews?articleId=`,
 * `POST /api/stories/intake/redact`, `/check`, `POST /api/interviews/{id}/answers`, `/skip`
 * (contract section 10). Raw answers stay in memory only, never in storage or the query cache
 * (contract 1.9, rule 9).
 */
export function InterviewTab({ pieceId }: PieceTabProps) {
  const kind = pieceKind(pieceId);
  const article = useApiQuery(
    {
      queryKey: queryKeys.articles.detail(pieceId),
      path: '/api/articles/{article_id}',
      init: { params: { path: { article_id: pieceId } } },
    },
    { enabled: kind === 'article' },
  );
  const post = useApiQuery(
    {
      queryKey: queryKeys.posts.detail(pieceId),
      path: '/api/posts/{post_id}',
      init: { params: { path: { post_id: pieceId } } },
    },
    { enabled: kind === 'post' },
  );
  const filters = kind === 'post' ? { postId: pieceId } : { articleId: pieceId };
  const interviews = useApiInfiniteQuery({
    queryKey: queryKeys.interviews.list(filters),
    path: '/api/interviews',
    init: { params: { query: filters } },
    pageSize: 20,
  });
  const [saved, setSaved] = useState<{ entry: InterviewEntry; outcome: 'answered' | 'skipped' } | null>(null);
  const appData = useAppData();

  const articleItem = kind === 'article' ? article.data?.item : undefined;
  const pillar = (kind === 'post' ? post.data?.item.pillar : articleItem?.meta?.pillar) ?? null;
  const status = articleItem?.status;
  const entries = interviews.data ?? [];
  const { current, earlier } = pickInterviews(entries, articleItem?.interviewId);
  const shown = current ? newerEntry(current, saved?.entry) : undefined;
  const closedId = shown && !isOpenInterview(shown.item.status) ? shown.item.id : undefined;
  // A closed interview takes no more answers: drafts kept for it in memory have nothing left to do.
  useEffect(() => {
    if (closedId !== undefined) draftStoreFor(appData).clear(closedId);
  }, [appData, closedId]);
  // While research runs or the questions are being written, follow the event stream so the
  // interview appears the moment the pipeline creates it.
  useLiveEvents(interviews.isSuccess && !current && status !== undefined && BEFORE_QUESTIONS.has(status));

  const body = () => {
    if (interviews.isPending) return <InterviewSkeleton />;
    if (interviews.isError) {
      return (
        <ProblemAlert
          error={interviews.error}
          title="Couldn’t load the interview."
          onRetry={() => void interviews.refetch()}
          retryLabel="Try loading the interview again"
        />
      );
    }
    if (!shown || shown.item.questions.length === 0) {
      return <NoInterview kind={kind} status={status} lastError={articleItem?.lastError ?? null} />;
    }
    const justSaved = saved?.entry.item.id === shown.item.id ? saved.outcome : null;
    return (
      <>
        {isOpenInterview(shown.item.status) ? (
          <InterviewSession
            key={shown.item.id}
            entry={shown}
            pillar={pillar}
            pieceKey={kind === 'post' ? queryKeys.posts.detail(pieceId) : queryKeys.articles.detail(pieceId)}
            onSaved={(entry, outcome) => {
              setSaved({ entry, outcome });
            }}
            onReload={() => void interviews.refetch()}
          />
        ) : (
          <InterviewRecord
            entry={shown}
            articleId={kind === 'article' ? pieceId : undefined}
            justSaved={justSaved}
          />
        )}
        <EarlierInterviews entries={earlier} />
      </>
    );
  };

  return (
    <div data-slot="studio-tab" data-piece-id={pieceId} className="grid gap-12">
      {body()}
    </div>
  );
}

function InterviewSkeleton() {
  return (
    <div role="status" className="grid max-w-measure gap-4">
      <span className="sr-only">Loading the interview…</span>
      <Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-1.5 w-full" />
      <Skeleton className="mt-6 h-9 w-5/6" />
      <Skeleton className="h-44 w-full" />
    </div>
  );
}

const YOUR_PART =
  'Answer in your own words. Dictation on your phone is fine. You confirm the anonymized wording before it is saved.';

function NoInterview({
  kind,
  status,
  lastError,
}: {
  kind: 'article' | 'post';
  status: ArticleStatus | undefined;
  lastError: string | null;
}) {
  let lead: string;
  if (kind === 'post') {
    lead = 'Interviews come with articles. A standalone post draws on your positions in Library > Stories & POV.';
  } else if (status === 'researching') {
    lead = 'Research is running. Your questions arrive when it’s done, with a notification.';
  } else if (status === 'interviewing') {
    lead = 'Your questions are being written. They appear here in a moment.';
  } else if (status === 'needs_attention') {
    // The job's error code says why (`ai_use_not_confirmed`, `blocklist_not_configured`, …).
    const reason = lastError ? PROBLEM_MESSAGES[lastError] : undefined;
    lead = `The pipeline stopped before your questions were written.${reason ? ` ${reason}` : ''}`;
  } else if (status === undefined || status === 'planned') {
    lead =
      'Every piece asks two questions: “What’s your take?” and “What would you tell a client on Monday?” ' +
      'Case studies and frameworks get a fuller interview of five to eight questions.';
  } else {
    lead = 'This piece went ahead without an interview, so its draft draws on your story bank and positions.';
  }
  return (
    <EmptyState
      title="No questions yet."
      yourPart={YOUR_PART}
    >
      <p>{lead}</p>
      <p>Client names and other confidential details are removed before anything is stored.</p>
    </EmptyState>
  );
}
