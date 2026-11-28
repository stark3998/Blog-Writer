import { ChevronRight, MessageSquarePlus, MessageSquareReply } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { keptWords } from '@/features/today/lib/time';
import { useNow } from '@/lib/use-now';

import { useOpenItems, type ItemEntry } from './lib/queries';
import { excerpt } from './lib/text';

interface OpenItemsProps {
  onOpen: (entry: ItemEntry) => void;
  /** Ids of items with a draft in progress. */
  drafted: ReadonlySet<string>;
}

/** Items waiting for a reply (newest first), each a whole-row button that opens it. */
export function OpenItems({ onOpen, drafted }: OpenItemsProps) {
  const items = useOpenItems();
  const now = useNow();

  if (items.isPending) {
    return (
      <div aria-busy="true" className="grid gap-2">
        <span className="sr-only">Loading what is waiting for a reply</span>
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
    );
  }
  if (items.isError) {
    return (
      <ProblemAlert
        error={items.error}
        title="Couldn’t load what is waiting for a reply."
        onRetry={() => void items.refetch()}
        retryLabel="Try loading the replies again"
      />
    );
  }
  if (items.data.length === 0) {
    return <p className="text-sm text-ink-muted">Nothing waiting. Paste a comment or share a screenshot to start one.</p>;
  }
  return (
    <div className="grid gap-3">
      <ul className="grid border-t border-line">
        {items.data.map((entry) => {
          const { item } = entry;
          const Icon = item.itemKind === 'reply' ? MessageSquareReply : MessageSquarePlus;
          const angles = item.angles?.length ?? 0;
          return (
            <li key={item.id} className="border-b border-line">
              <button
                type="button"
                onClick={() => {
                  onOpen(entry);
                }}
                className="flex min-h-11 w-full items-start gap-3 py-3 text-left transition-colors duration-150 hover:bg-wash focus-visible:outline-offset-[-2px]"
              >
                <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
                <span className="grid min-w-0 flex-1 gap-1">
                  <span className="text-sm text-ink">{excerpt(item.pastedText, 120)}</span>
                  <span className="flex flex-wrap gap-x-3 text-xs text-ink-subtle">
                    <span>{item.itemKind === 'reply' ? 'Reply on your post' : 'Comment'}</span>
                    <span>{keptWords(item.expiresAt, now)}</span>
                    {drafted.has(item.id) ? <span>Draft started</span> : angles > 0 ? <span>{angles === 1 ? '1 angle' : `${String(angles)} angles`}</span> : null}
                  </span>
                </span>
                <ChevronRight aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
              </button>
            </li>
          );
        })}
      </ul>
      {items.hasNextPage ? (
        <Button
          variant="secondary"
          className="w-fit"
          disabled={items.isFetchingNextPage}
          onClick={() => void items.fetchNextPage()}
        >
          {items.isFetchingNextPage ? 'Loading…' : 'Show more'}
        </Button>
      ) : null}
    </div>
  );
}
