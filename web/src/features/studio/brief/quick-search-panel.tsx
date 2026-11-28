import { ChevronDown, Copy, Search } from 'lucide-react';
import { useId, useState } from 'react';
import { Link } from 'react-router';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field } from '@/components/ui/field';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { toastSuccess } from '@/lib/api/toast';
import { cn } from '@/lib/cn';

import { formatDateTime } from '../piece/format';
import { useOnline } from '@/lib/use-online';

import { answerParts, problemsByField, TOPIC_MAX, TOPIC_MIN, type QuickSearch } from './brief-model';
import { useQuickSearches, useRunQuickSearch, type QuickScope } from './brief-queries';
import { CitationChip, safeHref, sourceLabel } from './citation-chip';

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toastSuccess('Copied with its source.');
  } catch {
    toast.error('Couldn’t copy it.', { description: 'Select the text and copy it yourself.' });
  }
}

/** The answer with its footnote numbers, each a link to its source. */
function Answer({ search }: { search: QuickSearch }) {
  const citations = search.citations ?? [];
  const parts = answerParts(search.answer, citations);
  if (parts.length === 0) {
    return <p className="text-ink-muted">No answer came back; the sources below are what the search found.</p>;
  }
  return (
    <p className="text-ink">
      {parts.map((part, index) => {
        if (part.type === 'text') return <span key={index}>{part.text}</span>;
        const href = safeHref(part.citation.url);
        const label = `Source ${String(part.number)}: ${part.citation.title}`;
        return (
          <sup key={index} className="ml-0.5 font-medium">
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`${label} (opens in a new tab)`}
                className="rounded-sm px-0.5 text-ink underline decoration-line-strong underline-offset-2 hover:decoration-ink"
              >
                {part.number}
              </a>
            ) : (
              <span aria-label={label}>{part.number}</span>
            )}
          </sup>
        );
      })}
    </p>
  );
}

/** A quick search's answer, its claims and its numbered sources. */
export function QuickResult({ search }: { search: QuickSearch }) {
  const citations = search.citations ?? [];
  const claims = search.claims ?? [];
  const byId = new Map(citations.map((citation) => [citation.id, citation]));
  return (
    <div className="grid gap-4 pt-3">
      <Answer search={search} />
      {claims.length > 0 ? (
        <div>
          <p className="text-sm font-semibold text-ink">What the sources say</p>
          <ul className="mt-2 grid gap-3">
            {claims.map((claim) => {
              const citation = byId.get(claim.citationId);
              return (
                <li key={claim.id} className="text-sm">
                  <p className="text-ink">{claim.text}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    {citation ? <CitationChip citation={citation} /> : null}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs pointer-coarse:h-9"
                      aria-label={`Copy “${claim.text.slice(0, 60)}” with its source`}
                      onClick={() => {
                        void copyText(citation ? `${claim.text} (${citation.title}, ${citation.url})` : claim.text);
                      }}
                    >
                      <Copy aria-hidden="true" />
                      Copy
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      {citations.length > 0 ? (
        <div>
          <p className="text-sm font-semibold text-ink">Sources</p>
          <ol className="mt-2 grid list-decimal gap-2 pl-5 text-sm marker:text-ink-subtle">
            {citations.map((citation) => {
              const href = safeHref(citation.url);
              return (
                <li key={citation.id}>
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
                    >
                      {citation.title}
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  ) : (
                    <span className="text-ink">{citation.title}</span>
                  )}
                  <span className="block text-ink-muted">
                    {sourceLabel(citation)}
                    {citation.allowlisted ? (
                      <Badge variant="success" className="ml-2">
                        Trusted source
                      </Badge>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

/** One earlier quick search: the question opens its answer. */
function PastSearch({ search, defaultOpen }: { search: QuickSearch; defaultOpen: boolean }) {
  const when = formatDateTime(search.createdAt);
  return (
    <li className="py-3">
      <Collapsible defaultOpen={defaultOpen}>
        <h3 className="text-base">
          <CollapsibleTrigger className="group flex w-full items-start justify-between gap-3 rounded-sm text-left font-semibold text-ink">
            <span>{search.query}</span>
            <ChevronDown
              aria-hidden="true"
              className="mt-1 size-4 shrink-0 text-ink-subtle transition-transform group-data-[state=open]:rotate-180"
            />
          </CollapsibleTrigger>
        </h3>
        {when ? (
          <p className="text-xs text-ink-subtle">
            <time dateTime={search.createdAt}>{when}</time>
            {`, ${String((search.citations ?? []).length)} ${(search.citations ?? []).length === 1 ? 'source' : 'sources'}`}
          </p>
        ) : null}
        <CollapsibleContent>
          <QuickResult search={search} />
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

export interface QuickSearchPanelProps {
  /** The piece the searches belong to. */
  scope: QuickScope;
  /** Searches the pillar's trusted sites (the server's allowlist). */
  pillar?: string | null | undefined;
  className?: string;
}

/**
 * Quick search, beside the brief: one question, answered in about a minute with its sources
 * (`POST /api/research/quick`, stored with the piece so a quick take can cite it). Earlier
 * searches stay below, newest first. The question passes the blocklist first.
 */
export function QuickSearchPanel({ scope, pillar, className }: QuickSearchPanelProps) {
  const headingId = useId();
  const online = useOnline();
  const searches = useQuickSearches(scope);
  const run = useRunQuickSearch();
  const [query, setQuery] = useState('');
  const [problem, setProblem] = useState<string | undefined>(undefined);

  const server = isProblem(run.error, ['validation_failed', 'confidential_text']) ? problemsByField(run.error) : {};
  const fieldError = problem ?? (run.isError ? server.query : undefined);
  const shownError =
    run.isError && isProblem(run.error, ['blocklist_not_configured', 'ai_unavailable', 'timeout']) ? run.error : undefined;
  const items = (searches.data ?? []).map((entry) => entry.item);

  const submit = () => {
    const text = query.trim();
    if (text.length < TOPIC_MIN) {
      setProblem('Ask a question of at least a few words.');
      return;
    }
    if (text.length > TOPIC_MAX) {
      setProblem(`Keep it to ${String(TOPIC_MAX)} characters.`);
      return;
    }
    setProblem(undefined);
    run.mutate(
      { body: { query: text, ...scope, ...(pillar ? { pillar } : {}) } },
      {
        onSuccess: () => {
          setQuery('');
        },
      },
    );
  };

  return (
    <section aria-labelledby={headingId} className={cn('grid gap-4', className)}>
      <div>
        <h2 id={headingId} className="font-serif text-2xl font-medium text-ink">
          Quick search
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          One question, answered in about a minute from your pillar’s trusted sites, with its sources.
        </p>
      </div>
      <form
        noValidate
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Field label="Your question" error={fieldError}>
          <Textarea
            rows={2}
            className="min-h-16"
            value={query}
            maxLength={TOPIC_MAX}
            placeholder="What changed in the latest NIST guidance on…"
            onChange={(event) => {
              setQuery(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                submit();
              }
            }}
          />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={run.isPending || !online}>
            <Search aria-hidden="true" />
            {run.isPending ? 'Searching…' : 'Search'}
          </Button>
          {!online ? <span className="text-sm text-ink-muted">You’re offline. Search needs a connection.</span> : null}
        </div>
      </form>

      {run.isPending ? (
        <div role="status" className="grid gap-2">
          <p className="text-sm text-ink-muted">Searching. This can take up to a minute and a half.</p>
          <Progress aria-label="Searching" size="sm" />
        </div>
      ) : null}

      {shownError ? (
        <div className="grid gap-2">
          <ProblemAlert error={shownError} title="The search didn’t finish." />
          {isProblem(shownError, 'blocklist_not_configured') ? (
            <Button asChild size="sm" variant="secondary" className="justify-self-start">
              <Link to="/settings/policy">Open Blocklist & policy</Link>
            </Button>
          ) : null}
        </div>
      ) : null}

      {searches.isPending ? (
        <div className="grid gap-2">
          <p role="status" className="sr-only">
            Loading earlier searches
          </p>
          <Skeleton className="h-5 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : searches.isError ? (
        <ProblemAlert
          error={searches.error}
          title="Couldn’t load your earlier searches."
          onRetry={() => {
            void searches.refetch();
          }}
          retryLabel="Try loading the earlier searches again"
        />
      ) : items.length === 0 ? (
        <Alert>
          <AlertTitle>No searches yet.</AlertTitle>
          <AlertDescription>
            <p>Answers stay here with their sources, so a quick take can cite them.</p>
          </AlertDescription>
        </Alert>
      ) : (
        <ul className="divide-y divide-line border-y border-line">
          {items.map((search, index) => (
            <PastSearch key={search.id} search={search} defaultOpen={index === 0} />
          ))}
        </ul>
      )}
      {searches.hasNextPage ? (
        <div>
          <Button
            size="sm"
            variant="ghost"
            disabled={searches.isFetchingNextPage}
            onClick={() => {
              void searches.fetchNextPage();
            }}
          >
            {searches.isFetchingNextPage ? 'Loading more…' : 'Show older searches'}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
