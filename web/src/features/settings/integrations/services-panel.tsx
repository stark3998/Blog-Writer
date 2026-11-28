import type { ReactNode } from 'react';

import { LastJob } from '../components/last-job';
import { SubHeading } from '../components/settings-block';
import { ServiceBadge, serviceSentence } from '../components/status-badge';
import { formatDateTime } from '../format';
import type { ServiceView, SystemStatus } from '../queries';

/** Words for the publish job's steps (`publish.article`). */
const PUBLISH_STEPS: Readonly<Record<string, string>> = {
  check: 'Checks before publishing',
  render: 'Render the page and assets',
  upsert: 'Store the published document',
  dispatch: 'Ask the portfolio to build',
  wait_build: 'Wait for the build',
  verify: 'Check the live page',
  finalize: 'Mark it live',
};

function ServiceRow({ title, view, children }: { title: string; view: ServiceView; children?: ReactNode }) {
  return (
    <div className="grid gap-2 border-b border-line py-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SubHeading>{title}</SubHeading>
        <ServiceBadge view={view} />
      </div>
      <p className="text-ink-muted">{serviceSentence(view)}</p>
      {view.expiresAt ? <p className="text-sm text-ink-muted">Credential expires {formatDateTime(view.expiresAt)}.</p> : null}
      {children}
    </div>
  );
}

/** The portfolio and Web IQ: what publishing and research rely on, from `GET /api/system/status`. */
export function ServicesPanel({ status }: { status: SystemStatus }) {
  const { portfolio, webiq, fetcher } = status.services;
  return (
    <div className="max-w-measure border-t border-line">
      <ServiceRow title="Portfolio publishing" view={portfolio}>
        <p className="text-sm text-ink-muted">
          Approved articles are written to the portfolio’s data, then its build runs and the live page is checked for the
          new version before the article counts as live.
        </p>
        {status.expiry.patExpiresAt ? (
          <p className="text-sm text-ink-muted">The build token expires {formatDateTime(status.expiry.patExpiresAt)}.</p>
        ) : null}
        <LastJob
          type="publish.article"
          label="Last publish"
          steps={PUBLISH_STEPS}
          empty="Nothing has been published from the app yet."
        />
      </ServiceRow>
      <ServiceRow title="Web IQ" view={webiq}>
        <p className="text-sm text-ink-muted">
          Research reads pages through Web IQ.{' '}
          {fetcher.state === 'on'
            ? 'When it is off or failing, pages are fetched directly instead.'
            : 'The direct fetcher is not available either, so research uses search citations only.'}
        </p>
      </ServiceRow>
    </div>
  );
}
