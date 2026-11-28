import { Check, X } from 'lucide-react';
import { Link } from 'react-router';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { isProblem, problemMessage } from '@/lib/api/problems';
import { useApiInfiniteQuery, useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { LoadError, LoadingBlock } from '../components/section-intro';
import { ShowMore } from '../components/show-more';
import { useMachines } from '../hooks/use-machines';
import { formatPeriod, formatShare, plural } from '../lib/format';
import { canMove } from '../lib/machines';
import { refreshOnConflict } from '../lib/stale';
import type { DraftComparison, Entry, GuideSuggestion, VoiceDigest } from '../lib/types';
import { SUGGESTION_KIND_LABEL } from './model';

const linkClass = 'text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink';

function Comparison({ comparison }: { comparison: DraftComparison }) {
  const kind = comparison.targetKind === 'post' ? 'Post' : 'Article';
  return (
    <li className="space-y-1 py-3">
      <p className="text-sm text-ink">
        <Link to={`/studio/${encodeURIComponent(comparison.targetId)}`} className={linkClass}>
          {kind}
        </Link>
        {`: you changed ${formatShare(comparison.editRatio)} of the AI draft.`}
      </p>
      {comparison.notableEdits && comparison.notableEdits.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-ink-muted">
          {comparison.notableEdits.map((edit, index) => (
            <li key={index}>{edit}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function SuggestionRow({ digest, suggestion }: { digest: Entry<VoiceDigest>; suggestion: GuideSuggestion }) {
  const machines = useMachines();
  const decide = useApiMutation('post', '/api/voice/digests/{digest_id}/suggestions/{suggestion_id}', {
    invalidates: [queryKeys.voiceDigests.lists(), queryKeys.styleGuides.all],
    successToast: (_, variables) =>
      variables.body.decision === 'accept'
        ? 'Added to a new version of your guide. Approve it in The guide above.'
        : 'Suggestion dismissed.',
    errorToast: 'Couldn’t record your answer.',
    handles: ['guide_missing', 'guide_rules_full'],
    onError: refreshOnConflict([queryKeys.voiceDigests.lists()]),
  });
  const proposed = suggestion.status === 'proposed';
  const send = (decision: 'accept' | 'dismiss') => {
    decide.mutate({
      params: { path: { digest_id: digest.item.id, suggestion_id: suggestion.id } },
      body: { decision },
      ifMatch: digest.etag,
    });
  };
  const inline = isProblem(decide.error, ['guide_missing', 'guide_rules_full']) ? problemMessage(decide.error) : undefined;

  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <Badge variant="outline">{SUGGESTION_KIND_LABEL[suggestion.kind]}</Badge>
        <p className="min-w-0 flex-1 text-ink">{suggestion.text}</p>
        {proposed ? null : (
          <Badge variant={suggestion.status === 'accepted' ? 'success' : 'neutral'}>
            {suggestion.status === 'accepted' ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}
            {suggestion.status === 'accepted' ? 'Accepted' : 'Dismissed'}
          </Badge>
        )}
      </div>
      {suggestion.rationale ? <p className="text-sm text-ink-muted">{suggestion.rationale}</p> : null}
      {proposed ? (
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={decide.isPending || !canMove(machines, 'suggestion', 'proposed', 'accepted')}
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
            disabled={decide.isPending || !canMove(machines, 'suggestion', 'proposed', 'dismissed')}
            aria-label={`Dismiss: ${suggestion.text}`}
            onClick={() => {
              send('dismiss');
            }}
          >
            Dismiss
          </Button>
        </div>
      ) : null}
      {inline ? (
        <p role="alert" className="text-sm font-medium text-danger">
          {inline}
        </p>
      ) : null}
    </li>
  );
}

function DigestCard({ entry }: { entry: Entry<VoiceDigest> }) {
  const digest = entry.item;
  const comparisons = digest.comparisons ?? [];
  const suggestions = digest.suggestions ?? [];
  const open = suggestions.filter((suggestion) => suggestion.status === 'proposed').length;
  return (
    <article className="space-y-4 border-t border-line pt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="font-serif text-lg font-medium text-ink">{formatPeriod(digest.periodStart, digest.periodEnd)}</h4>
        <p className="text-sm text-ink-muted">
          {[
            digest.guideVersion ? `Against guide version ${String(digest.guideVersion)}` : undefined,
            open > 0 ? plural(open, 'suggestion to answer', 'suggestions to answer') : undefined,
          ]
            .filter(Boolean)
            .join(', ')}
        </p>
      </div>
      {comparisons.length > 0 ? (
        <div>
          <h5 className="text-sm font-semibold text-ink">{`What you changed (${String(comparisons.length)})`}</h5>
          <ul className="divide-y divide-line">
            {comparisons.map((comparison) => (
              <Comparison key={`${comparison.targetKind}-${comparison.targetId}`} comparison={comparison} />
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-ink-muted">Nothing was posted in this period, so there was nothing to compare.</p>
      )}
      {suggestions.length > 0 ? (
        <div>
          <h5 className="text-sm font-semibold text-ink">Suggested changes to your guide</h5>
          <ul className="divide-y divide-line">
            {suggestions.map((suggestion) => (
              <SuggestionRow key={suggestion.id} digest={entry} suggestion={suggestion} />
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

/** The digests of AI drafts against what you posted, newest period first. */
export function DigestsPanel() {
  const digests = useApiInfiniteQuery({ queryKey: queryKeys.voiceDigests.list({}), path: '/api/voice/digests', pageSize: 10 });
  if (digests.isPending) return <LoadingBlock label="Loading the digests" rows={2} />;
  if (digests.isError) {
    return (
      <LoadError error={digests.error} title="Couldn’t load the digests." retryLabel="Try loading the digests again" onRetry={() => void digests.refetch()} />
    );
  }
  if (digests.data.length === 0) {
    return (
      <p className="max-w-measure text-ink-muted">
        No digest yet. The first one comes the week after your first post goes out with an AI draft behind it.
      </p>
    );
  }
  return (
    <div className="space-y-6">
      {digests.data.map((entry) => (
        <DigestCard key={entry.item.id} entry={entry} />
      ))}
      <ShowMore
        hasNextPage={digests.hasNextPage}
        isFetchingNextPage={digests.isFetchingNextPage}
        fetchNextPage={digests.fetchNextPage}
        label="Show earlier digests"
      />
    </div>
  );
}
