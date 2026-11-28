import { Link } from 'react-router';

import { Button } from '@/components/ui/button';

import { Page, PageHeader } from './page';

export function NotFound() {
  return (
    <Page>
      <PageHeader
        title="Page not found"
        standfirst="There is nothing at this address. The link may be wrong, or the page may have moved."
      />
      <Button asChild variant="secondary">
        <Link to="/today">Go to Today</Link>
      </Button>
    </Page>
  );
}
