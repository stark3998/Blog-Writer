import { RotateCw } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import type { components } from '@/lib/api/schema';

import { kindMeta, safeHref, type TodayItem } from './lib/items';
import { momentWords, spanWords, timeOf } from './lib/time';
import { useNow } from '@/lib/use-now';

type SystemStatus = components['schemas']['SystemStatus'];
type AutonomyView = components['schemas']['AutonomyView'];

/** LinkedIn tokens last 60 days (contract W-A9); the banner starts on day 50. */
const TOKEN_DAYS = 60;
/** The portfolio token warning starts this many days before it expires. */
const PAT_WARNING_DAYS = 14;
const DAY = 24 * 60 * 60 * 1000;

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

interface Notice {
  readonly id: string;
  readonly tone: 'error' | 'warn' | 'info';
  readonly title: string;
  readonly body: ReactNode;
  readonly action: { readonly label: string; readonly href: string };
}

function spend(brake: NonNullable<SystemStatus['brake']>): string {
  const { spendTodayUsd: today, thresholdUsd: limit } = brake;
  if (today === null || limit === null) return 'Today’s AI spend passed the daily limit';
  return `Today’s AI spend (${usd.format(today)}) passed the daily limit of ${usd.format(limit)}`;
}

/** Everything that changes how the engine behaves today, as notices with one action each. */
export function noticesFor({
  status,
  autonomy,
  items,
  now,
}: {
  status: SystemStatus | undefined;
  autonomy: AutonomyView | undefined;
  items: readonly TodayItem[];
  now: number;
}): Notice[] {
  const notices: Notice[] = [];
  if (status?.safeMode) {
    notices.push({
      id: 'safe-mode',
      tone: 'error',
      title: 'Safe mode is on',
      body: (
        <p>
          The app restarted several times in a row, so background jobs are stopped until you check them. Nothing is
          lost, and pages still work.
        </p>
      ),
      action: { label: 'Check the jobs', href: '/settings/system' },
    });
  }
  for (const item of items) {
    const day = status?.expiry.linkedinTokenDay;
    notices.push({
      id: item.id,
      tone: item.priority === 'urgent' ? 'error' : 'warn',
      title: item.title,
      body: (
        <p>
          {typeof day === 'number' && day < TOKEN_DAYS
            ? `LinkedIn access lasts ${TOKEN_DAYS} days and this connection is on day ${day}. `
            : 'LinkedIn access has run out. '}
          Until you reconnect, posts go out through the ready-to-post kit.
        </p>
      ),
      action: { label: kindMeta(item.kind).action, href: safeHref(item.href) },
    });
  }
  if (status?.brake?.engaged) {
    notices.push({
      id: 'brake',
      tone: 'warn',
      title: 'The spending brake is on',
      body: (
        <p>
          {spend(status.brake)}, so background AI work waits until tomorrow. What you start yourself still runs.
        </p>
      ),
      action: { label: 'See the spending', href: '/settings/insights' },
    });
  }
  if (autonomy?.settings.item.paused) {
    notices.push({
      id: 'paused',
      tone: 'info',
      title: 'Scheduled work is paused',
      body: (
        <p>
          Feeds, digests, the weekly plan and auto-publishing wait until you resume. What you start yourself still
          runs.
        </p>
      ),
      action: { label: 'Open the pause switch', href: '/settings/policy' },
    });
  }
  const pat = timeOf(status?.expiry.patExpiresAt);
  if (pat !== undefined && pat - now < PAT_WARNING_DAYS * DAY) {
    const expired = pat <= now;
    notices.push({
      id: 'portfolio-token',
      tone: expired ? 'error' : 'warn',
      title: expired ? 'The portfolio token has expired' : `The portfolio token expires in ${spanWords(pat - now)}`,
      body: (
        <p>
          {expired
            ? 'Articles can’t publish to your site until you replace it.'
            : `It runs out ${momentWords(pat, now)}. Articles can’t publish to your site after that.`}
        </p>
      ),
      action: { label: 'Check the integrations', href: '/settings/integrations' },
    });
  }
  return notices;
}

interface TodayNoticesProps {
  status: SystemStatus | undefined;
  statusFailed: boolean;
  onRetryStatus: () => void;
  autonomy: AutonomyView | undefined;
  /** The inbox's `reconnect_linkedin` items. */
  items: readonly TodayItem[];
}

/**
 * Safe mode, LinkedIn reconnects, the spending brake, the pause switch and the portfolio token:
 * standing notices above the inbox (not live regions: they are there when the page opens).
 */
export function TodayNotices({ status, statusFailed, onRetryStatus, autonomy, items }: TodayNoticesProps) {
  const now = useNow();
  const notices = noticesFor({ status, autonomy, items, now });
  if (notices.length === 0 && !statusFailed) return null;
  return (
    <section aria-labelledby="today-notices" className="mb-10 grid max-w-measure gap-3">
      <h2 id="today-notices" className="sr-only">
        Notices
      </h2>
      {notices.map((notice) => (
        <Alert key={notice.id} variant={notice.tone} role={undefined}>
          <AlertTitle>{notice.title}</AlertTitle>
          <AlertDescription>{notice.body}</AlertDescription>
          <AlertActions>
            <Button asChild size="sm" variant="secondary">
              <Link to={notice.action.href}>{notice.action.label}</Link>
            </Button>
          </AlertActions>
        </Alert>
      ))}
      {statusFailed ? (
        <p className="flex flex-wrap items-center gap-x-3 text-sm text-ink-muted">
          Couldn’t check safe mode and the spending brake.
          <Button variant="link" size="sm" onClick={onRetryStatus}>
            <RotateCw aria-hidden="true" />
            Check again
          </Button>
        </p>
      ) : null}
    </section>
  );
}
