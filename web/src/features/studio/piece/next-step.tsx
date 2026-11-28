import { ArrowUpRight, RotateCw } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';

import { Alert, AlertActions, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/errors';
import { isProblem, missingFields, problemCode, problemMessage } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { cn } from '@/lib/cn';

import { START_RESEARCH_PROBLEMS, useStartResearch } from './piece-queries';
import { machineAllows, nextActionFor, ownerCanMoveArticle, type NextActionTarget, type PieceFacts } from './piece-status';
import type { Machines } from './types';

/** Where the owner fixes what stopped the pipeline from starting. */
function fixFor(error: unknown, articleId: string): { readonly to: string; readonly label: string } | undefined {
  switch (problemCode(error)) {
    case 'voice_guide_missing':
      return { to: '/library/voice', label: 'Open Voice guide' };
    case 'ai_use_not_confirmed':
    case 'blocklist_not_configured':
      return { to: '/settings/policy', label: 'Open Blocklist & policy' };
    case 'meta_incomplete':
      return { to: `/studio/${encodeURIComponent(articleId)}/draft`, label: 'Fill in the details' };
    case undefined:
    default:
      return undefined;
  }
}

const RESTORE_PROBLEMS = ['etag_mismatch', 'invalid_transition', 'transition_not_allowed_here'] as const;

const FIELD_NAMES: Readonly<Record<string, string>> = { title: 'a title', pillar: 'a pillar', format: 'a format' };

/** The sentence for a refused start, in the header's words. */
function startProblem(error: unknown): string {
  switch (problemCode(error)) {
    case 'meta_incomplete': {
      const missing = missingFields(error).map((field) => FIELD_NAMES[field] ?? field);
      const last = missing.pop();
      if (last === undefined) return problemMessage(error);
      const list = missing.length === 0 ? last : `${missing.join(', ')} and ${last}`;
      return `Research needs ${list} first. Set it in the Draft tab.`;
    }
    case 'etag_mismatch':
      return 'It changed since you opened it. Check where it stands now, then try again.';
    case 'pipeline_active':
      return 'A pipeline run is already working on it.';
    case undefined:
    default:
      return problemMessage(error);
  }
}

function ProblemNotice({
  title,
  error,
  fix,
  onRetry,
}: {
  title: string;
  error: unknown;
  fix?: { readonly to: string; readonly label: string } | undefined;
  onRetry?: (() => void) | undefined;
}) {
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <Alert variant="error" className="mt-4">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{startProblem(error)}</p>
        {requestId ? (
          <p className="text-xs">
            Request ID <code className="font-mono">{requestId}</code>
          </p>
        ) : null}
      </AlertDescription>
      {fix || onRetry ? (
        <AlertActions>
          {fix ? (
            <Button asChild size="sm" variant="secondary">
              <Link to={fix.to}>{fix.label}</Link>
            </Button>
          ) : null}
          {onRetry ? (
            <Button size="sm" variant="ghost" onClick={onRetry}>
              <RotateCw aria-hidden="true" />
              Check again
            </Button>
          ) : null}
        </AlertActions>
      ) : null}
    </Alert>
  );
}

/** Why `POST /api/articles/{id}/pipeline` refused to start research, with the way to fix it. */
export function StartResearchNotice({
  error,
  articleId,
  onRetry,
}: {
  error: unknown;
  articleId: string;
  onRetry?: (() => void) | undefined;
}) {
  if (!isProblem(error, START_RESEARCH_PROBLEMS)) return null;
  return <ProblemNotice title="Research didn’t start." error={error} fix={fixFor(error, articleId)} onRetry={onRetry} />;
}

export interface NextStepProps {
  facts: PieceFacts;
  /** The open tab: a step that only links to it shows no button. */
  currentTab: string;
  /** The piece's etag, for the actions that move it (start research, restore). */
  etag?: string | null;
  machines?: Machines | undefined;
  /** `GET /api/meta/machines` failed: the actions stay available and the routes decide. */
  machinesUnavailable?: boolean;
  /** Reloads the piece (after a refused action, so the header shows where it stands). */
  onRefresh?: () => void;
}

/**
 * The piece's next step under its title: one sentence about what happens now and the one action
 * that moves it on (a tab, starting research, restoring it, or its live page). The sentence sits
 * under an ink rule, like the "Your part" margin notes.
 */
export function NextStep({ facts, currentTab, etag, machines, machinesUnavailable = false, onRefresh }: NextStepProps) {
  const next = nextActionFor(facts);
  const start = useStartResearch(facts.id);
  const restore = useApiMutation('post', '/api/articles/{article_id}/transition', {
    updates: () => queryKeys.articles.detail(facts.id),
    invalidates: [queryKeys.articles.lists(), queryKeys.today],
    successToast: 'Restored. It’s planned again.',
    errorToast: 'Couldn’t restore it.',
    handles: RESTORE_PROBLEMS,
  });

  const renderTarget = (target: NextActionTarget, emphasis: 'primary' | 'secondary'): ReactNode => {
    const variant = emphasis === 'primary' ? 'primary' : 'secondary';
    switch (target.type) {
      case 'tab':
        if (target.tab === currentTab) return null;
        return (
          <Button key={target.label} asChild variant={variant}>
            <Link to={`/studio/${encodeURIComponent(facts.id)}/${target.tab}${target.search ?? ''}`}>{target.label}</Link>
          </Button>
        );
      case 'external':
        return (
          <Button key={target.label} asChild variant={variant}>
            <a href={target.href} target="_blank" rel="noopener noreferrer">
              {target.label}
              <ArrowUpRight aria-hidden="true" />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </Button>
        );
      case 'start-research': {
        const allowed =
          facts.kind === 'article' && machineAllows(machines, 'article', facts.status, 'researching', machinesUnavailable);
        return (
          <Button
            key={target.label}
            variant={variant}
            disabled={!allowed || etag === undefined || etag === null || start.isPending}
            onClick={() => {
              if (!etag) return;
              start.mutate({
                params: { path: { article_id: facts.id } },
                body: { startFrom: 'research' },
                ifMatch: etag,
              });
            }}
          >
            {start.isPending ? 'Starting…' : target.label}
          </Button>
        );
      }
      case 'restore': {
        const allowed = facts.kind === 'article' && ownerCanMoveArticle(machines, 'archived', 'planned', machinesUnavailable);
        return (
          <Button
            key={target.label}
            variant={variant}
            disabled={!allowed || !etag || restore.isPending}
            onClick={() => {
              if (!etag) return;
              restore.mutate({
                params: { path: { article_id: facts.id } },
                body: { to: 'planned', reason: 'Restored from the archive.' },
                ifMatch: etag,
              });
            }}
          >
            {restore.isPending ? 'Restoring…' : target.label}
          </Button>
        );
      }
    }
  };

  const primary = next.primary ? renderTarget(next.primary, 'primary') : null;
  const secondary = next.secondary ? renderTarget(next.secondary, 'secondary') : null;

  return (
    <div data-slot="next-step" className="mt-6 border-t-2 border-ink pt-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <p className={cn('min-w-0 flex-1 basis-64', next.needsOwner ? 'font-medium text-ink' : 'text-ink-muted')}>
          {next.sentence}
        </p>
        {primary || secondary ? (
          <div className="flex flex-wrap gap-2">
            {primary}
            {secondary}
          </div>
        ) : null}
      </div>
      {start.isError ? <StartResearchNotice error={start.error} articleId={facts.id} onRetry={onRefresh} /> : null}
      {restore.isError && isProblem(restore.error, RESTORE_PROBLEMS) ? (
        <ProblemNotice title="It wasn’t restored." error={restore.error} onRetry={onRefresh} />
      ) : null}
    </div>
  );
}
