import { ExternalLink } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { sinceWords } from '@/features/today/lib/time';
import { useNow } from '@/lib/use-now';

import { useRecords } from './lib/queries';

/**
 * What the owner posted (`EngagementRecord`s): their own final words and the link, kept for the
 * weekly scorecard. Other members' text is never part of a record.
 */
export function PostedRecords() {
  const records = useRecords();
  const now = useNow();

  if (records.isPending) {
    return (
      <div aria-busy="true" className="grid gap-2">
        <span className="sr-only">Loading what you posted</span>
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
    );
  }
  if (records.isError) {
    return (
      <ProblemAlert
        error={records.error}
        title="Couldn’t load what you posted."
        onRetry={() => void records.refetch()}
        retryLabel="Try loading what you posted again"
      />
    );
  }
  if (records.data.length === 0) {
    return (
      <div className="grid gap-1 text-sm">
        <p className="font-semibold text-ink">Nothing posted yet.</p>
        <p className="text-ink-muted">
          When you save a reply or comment with “I posted it”, your words and the link are kept here. Substantive
          comments count on the weekly scorecard.
        </p>
      </div>
    );
  }
  return (
    <div className="grid gap-3">
      <ul aria-label="What you posted, newest first" className="border-t border-line">
        {records.data.map(({ item }) => (
          <li key={item.id} className="grid gap-2 border-b border-line py-4">
            <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
              <span className="font-semibold text-ink">{item.itemKind === 'reply' ? 'Reply' : 'Comment'}</span>
              <time dateTime={item.postedAt}>{sinceWords(item.postedAt, now)}</time>
              {item.itemKind === 'comment' ? (
                <Badge variant={item.substantive ? 'success' : 'neutral'}>{item.substantive ? 'Substantive' : 'Short'}</Badge>
              ) : null}
            </p>
            <p className="line-clamp-4 whitespace-pre-line text-sm text-ink">{item.finalText}</p>
            {item.url ? (
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 w-fit items-center gap-1.5 text-sm text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink md:min-h-0"
              >
                Open on LinkedIn{' '}
                <ExternalLink aria-hidden="true" className="size-4" />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            ) : null}
          </li>
        ))}
      </ul>
      {records.hasNextPage ? (
        <Button
          variant="secondary"
          className="w-fit"
          disabled={records.isFetchingNextPage}
          onClick={() => void records.fetchNextPage()}
        >
          {records.isFetchingNextPage ? 'Loading…' : 'Show more'}
        </Button>
      ) : null}
    </div>
  );
}
