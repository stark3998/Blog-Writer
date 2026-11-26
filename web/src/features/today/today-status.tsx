import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useHealth, useMe } from '@/features/system/queries';
import { cn } from '@/lib/cn';

/** A full git SHA is shortened to the usual seven characters; labels such as "dev" stay as they are. */
export function shortSha(sha: string): string {
  return /^[0-9a-f]{12,}$/i.test(sha) ? sha.slice(0, 7) : sha;
}

interface FactProps<T> {
  term: string;
  /** What the query loads, for the retry button's label ("Retry loading …"). */
  subject: string;
  result: UseQueryResult<T>;
  children: (data: T) => ReactNode;
}

/** One term and its value from a query: a skeleton while loading, a retry when it failed. */
function Fact<T>({ term, subject, result, children }: FactProps<T>) {
  let value: ReactNode;
  if (result.isSuccess) {
    value = children(result.data);
  } else if (result.isPending || result.isFetching) {
    value = (
      <>
        <Skeleton className="inline-block h-4 w-28 align-middle" />
        <span className="sr-only">Loading</span>
      </>
    );
  } else {
    value = (
      <span className="inline-flex flex-wrap items-baseline gap-x-2">
        <span className="font-normal text-danger">Couldn’t load</span>
        <Button
          variant="link"
          size="sm"
          aria-label={`Retry loading ${subject}`}
          onClick={() => {
            void result.refetch();
          }}
        >
          Retry
        </Button>
      </span>
    );
  }
  return (
    <div className="flex min-w-0 items-baseline gap-2">
      <dt className="text-ink-subtle">{term}</dt>
      <dd className="min-w-0 font-medium text-ink" aria-busy={result.isFetching && !result.isSuccess}>
        {value}
      </dd>
    </div>
  );
}

/**
 * Who is signed in and which server build answers, under Today's title. Both refetch (when stale)
 * as the owner comes back to the app, which also notices an expired session early.
 */
export function TodayStatus({ className }: { className?: string }) {
  const me = useMe({ refetchOnWindowFocus: true });
  const health = useHealth({ refetchOnWindowFocus: true });
  return (
    <dl className={cn('mt-5 flex flex-wrap gap-x-8 gap-y-2 text-sm', className)}>
      <Fact term="Signed in as" subject="your name" result={me}>
        {(data) => data.name ?? 'the owner'}
      </Fact>
      <Fact term="Server build" subject="the server build" result={health}>
        {(data) => (
          <code className="font-mono" title={data.sha}>
            {shortSha(data.sha)}
          </code>
        )}
      </Fact>
    </dl>
  );
}
