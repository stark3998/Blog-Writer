import { Skeleton } from '@/components/ui/skeleton';

import { Page } from './page';

/** Shown inside the shell while a page's code loads for the first time. */
export function RouteFallback() {
  return (
    <Page>
      <p role="status" className="sr-only">
        Loading page
      </p>
      <Skeleton className="h-11 w-44 md:h-13" />
      <Skeleton className="mt-4 h-5 w-[min(32rem,100%)]" />
      <Skeleton className="mt-2 h-5 w-[min(24rem,80%)]" />
      <Skeleton className="mt-14 h-8 w-[min(20rem,90%)]" />
      <Skeleton className="mt-4 h-4 w-[min(36rem,100%)]" />
      <Skeleton className="mt-2 h-4 w-[min(30rem,95%)]" />
    </Page>
  );
}
