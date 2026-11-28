import { Button } from '@/components/ui/button';

export interface ShowMoreProps {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
  /** "Show more stories". */
  label: string;
}

/** Loads the next page of a list (contract 1.5: lists are paged with a cursor). */
export function ShowMore({ hasNextPage, isFetchingNextPage, fetchNextPage, label }: ShowMoreProps) {
  if (!hasNextPage) return null;
  return (
    <div className="pt-4">
      <Button
        variant="secondary"
        disabled={isFetchingNextPage}
        onClick={() => {
          void fetchNextPage();
        }}
      >
        {isFetchingNextPage ? 'Loading…' : label}
      </Button>
    </div>
  );
}
