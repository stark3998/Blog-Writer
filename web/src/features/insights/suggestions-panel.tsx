import { Check, ChevronDown, Lightbulb, X } from 'lucide-react';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { Versioned } from '@/lib/api/envelope';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { LiveJob } from '../settings/components/live-job';
import { BlockLoading } from '../settings/components/settings-block';
import { formatDay } from '../settings/format';
import { pillarNames, usePillars } from '../settings/queries';
import { dimensionLabel, valueLabel } from './labels';
import { insightsKeys, type InsightSuggestion } from './queries';

const SUGGEST_STEPS: Readonly<Record<string, string>> = {
  compute: 'Compare your rates',
  suggest: 'Write the suggestions',
  save: 'Save them',
};

function valueOf(suggestion: InsightSuggestion): string | undefined {
  return suggestion.value ?? undefined;
}

function Subject({ suggestion, names }: { suggestion: InsightSuggestion; names: ReadonlyMap<string, string> }) {
  const value = valueOf(suggestion);
  return (
    <Badge variant="outline">
      {value
        ? `${dimensionLabel(suggestion.dimension)}: ${valueLabel(suggestion.dimension, value, names)}`
        : dimensionLabel(suggestion.dimension)}
    </Badge>
  );
}

function ProposedRow({ entry, names }: { entry: Versioned<InsightSuggestion>; names: ReadonlyMap<string, string> }) {
  const suggestion = entry.item;
  const decide = useApiMutation('post', '/api/analytics/suggestions/{suggestion_id}/decide', {
    updates: (data) => queryKeys.suggestions.detail(data.item.id),
    invalidates: [queryKeys.suggestions.lists()],
    successToast: (data) =>
      data.item.status === 'accepted'
        ? 'Accepted. Make the change yourself where it belongs: nothing adjusts itself.'
        : 'Dismissed.',
    errorToast: 'Couldn’t record your decision.',
  });
  const send = (decision: 'accept' | 'dismiss') => {
    decide.mutate({ params: { path: { suggestion_id: suggestion.id } }, body: { decision }, ifMatch: entry.etag });
  };
  return (
    <li className="grid gap-3 border-b border-line py-5">
      <div className="flex flex-wrap items-center gap-2">
        <Subject suggestion={suggestion} names={names} />
        <span className="text-xs text-ink-subtle">{`Week of ${formatDay(suggestion.weekStart)}`}</span>
      </div>
      <p className="max-w-measure font-serif text-lg text-ink">{suggestion.text}</p>
      {suggestion.evidence ? <p className="max-w-measure text-sm text-ink-muted">{suggestion.evidence}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={decide.isPending}
          aria-label={`Accept: ${suggestion.text}`}
          onClick={() => {
            send('accept');
          }}
        >
          <Check aria-hidden="true" />
          Accept
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={decide.isPending}
          aria-label={`Dismiss: ${suggestion.text}`}
          onClick={() => {
            send('dismiss');
          }}
        >
          <X aria-hidden="true" />
          Dismiss
        </Button>
      </div>
    </li>
  );
}

/**
 * Suggestions from the weekly comparison of rates (`analytics.suggest`): the owner accepts or
 * dismisses each. Accepting records the decision only; the planner never adjusts itself.
 */
export function SuggestionsPanel() {
  const pillars = usePillars();
  const names = pillarNames(pillars.data?.item.pillars);
  const [jobId, setJobId] = useState<string | undefined>();
  const [history, setHistory] = useState(false);
  const suggestions = useApiInfiniteQuery({
    queryKey: insightsKeys.suggestions('all'),
    path: '/api/analytics/suggestions',
    pageSize: 30,
  });
  const run = useApiMutation('post', '/api/analytics/suggestions/run', {
    successToast: 'Looking for suggestions now.',
    errorToast: 'Couldn’t start the suggestions.',
  });
  const list = suggestions.data ?? [];
  const proposed = list.filter((entry) => entry.item.status === 'proposed');
  const decided = list.filter((entry) => entry.item.status !== 'proposed');

  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="secondary"
          disabled={run.isPending}
          onClick={() => {
            run.mutate(undefined, {
              onSuccess: (accepted) => {
                setJobId(accepted.jobId);
              },
            });
          }}
        >
          <Lightbulb aria-hidden="true" />
          {run.isPending ? 'Starting…' : 'Look for suggestions now'}
        </Button>
        <p className="text-sm text-ink-muted">They also arrive every Monday morning.</p>
      </div>
      {jobId ? (
        <div className="flex max-w-measure items-start justify-between gap-4 rounded-lg border border-line p-4">
          <LiveJob jobId={jobId} title="Looking for suggestions" labels={SUGGEST_STEPS} />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Hide the suggestions progress"
            onClick={() => {
              setJobId(undefined);
            }}
          >
            <X aria-hidden="true" />
          </Button>
        </div>
      ) : null}
      {suggestions.isPending ? (
        <BlockLoading label="Loading the suggestions" />
      ) : suggestions.isError ? (
        <ProblemAlert
          error={suggestions.error}
          title="Couldn’t load the suggestions."
          onRetry={() => void suggestions.refetch()}
          retryLabel="Try loading the suggestions again"
        />
      ) : (
        <>
          {proposed.length === 0 ? (
            <p className="max-w-measure text-ink-muted">
              Nothing to decide. Suggestions need at least a few measured posts, so they start once snapshots come in.
            </p>
          ) : (
            <ul aria-label="Suggestions to decide" className="max-w-3xl border-t border-line">
              {proposed.map((entry) => (
                <ProposedRow key={entry.item.id} entry={entry} names={names} />
              ))}
            </ul>
          )}
          {decided.length > 0 ? (
            <Collapsible open={history} onOpenChange={setHistory}>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="justify-self-start">
                  <ChevronDown aria-hidden="true" className={history ? 'rotate-180' : undefined} />
                  {history ? 'Hide your earlier decisions' : `Show your ${String(decided.length)} earlier decisions`}
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul aria-label="Earlier decisions" className="mt-3 max-w-3xl border-t border-line">
                  {decided.map((entry) => (
                    <li key={entry.item.id} className="grid gap-1 border-b border-line py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Subject suggestion={entry.item} names={names} />
                        <Badge variant={entry.item.status === 'accepted' ? 'success' : 'neutral'}>
                          {entry.item.status === 'accepted' ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}
                          {entry.item.status === 'accepted' ? 'Accepted' : 'Dismissed'}
                        </Badge>
                      </div>
                      <p className="text-ink-muted">{entry.item.text}</p>
                    </li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          ) : null}
        </>
      )}
    </div>
  );
}
