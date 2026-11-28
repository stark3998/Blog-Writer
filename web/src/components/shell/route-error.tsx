import { RotateCw } from 'lucide-react';
import { isRouteErrorResponse, Link, useRouteError } from 'react-router';

import { Button } from '@/components/ui/button';

import { NotFound } from './not-found';
import { Page, PageHeader } from './page';

/**
 * True when a lazy page's code failed to download. After a deploy, an open tab
 * can still point at chunk names that no longer exist; a reload fixes it.
 */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return (
    error.name === 'ChunkLoadError' ||
    /dynamically imported module|importing a module script failed|error loading dynamically imported module/i.test(
      error.message,
    )
  );
}

function reload() {
  window.location.reload();
}

function ErrorContent({ error }: { error: unknown }) {
  const chunk = isChunkLoadError(error);
  return (
    <Page>
      <PageHeader
        title={chunk ? 'This page didn’t load' : 'Something went wrong'}
        standfirst={
          chunk
            ? 'The app was probably updated since you opened it. Reload to get the latest version.'
            : 'An unexpected error stopped this page. Reload to try again.'
        }
      />
      <div className="flex flex-wrap gap-3">
        <Button onClick={reload}>
          <RotateCw aria-hidden="true" />
          Reload
        </Button>
        <Button asChild variant="secondary">
          <Link to="/today" reloadDocument>
            Go to Today
          </Link>
        </Button>
      </div>
    </Page>
  );
}

/** Errors inside a page: the shell stays, so navigation still works. */
export function PageError() {
  const error = useRouteError();
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFound />;
  return <ErrorContent error={error} />;
}

/** Errors in the shell itself: a full page with no navigation. */
export function RootError() {
  const error = useRouteError();
  return (
    <div className="min-h-dvh bg-sheet">
      <ErrorContent error={error} />
    </div>
  );
}
