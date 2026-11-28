import { Search } from 'lucide-react';
import { useEffect, useId, useRef, useState, type SubmitEvent } from 'react';
import { flushSync } from 'react-dom';
import { Link } from 'react-router';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Field, Fieldset, focusFirstInvalid } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import type { Versioned } from '@/lib/api/envelope';
import { confidentialFindings, fieldErrors, isProblem, problemMessage } from '@/lib/api/problems';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { isHttpsUrl } from '@/lib/urls';

import type { Pillar, Schemas } from '../queries';

export type Source = Schemas['Source'];
type SourceType = Schemas['SourceType'];
type DiscoveredFeed = Schemas['DiscoveredFeedOut'];

const NO_PILLAR = 'none';

interface SourceDraft {
  type: SourceType;
  title: string;
  url: string;
  siteUrl: string;
  query: string;
  pillar: string;
}

function draftOf(source: Source | undefined): SourceDraft {
  return {
    type: source?.type ?? 'feed',
    title: source?.title ?? '',
    url: source?.url ?? '',
    siteUrl: source?.siteUrl ?? '',
    query: source?.query ?? '',
    pillar: source?.pillar ?? NO_PILLAR,
  };
}

/** An https address the server accepts, ignoring spaces typed around it. */
function isHttpsAddress(value: string): boolean {
  return isHttpsUrl(value.trim());
}

function draftProblems(draft: SourceDraft): Record<string, string> {
  const problems: Record<string, string> = {};
  const title = draft.title.trim();
  if (title === '') problems.title = 'Give it a name you will recognise.';
  else if (title.length > 200) problems.title = 'At most 200 characters.';
  if (draft.type === 'feed') {
    if (draft.url.trim() === '') problems.url = 'Enter the feed’s address, or find it from the site.';
    else if (!isHttpsAddress(draft.url)) problems.url = 'Use an https:// address.';
  } else {
    const query = draft.query.trim();
    if (query.length < 3) problems.query = 'Write at least 3 characters.';
    else if (query.length > 300) problems.query = 'At most 300 characters.';
  }
  if (draft.siteUrl.trim() !== '' && !isHttpsAddress(draft.siteUrl)) problems.siteUrl = 'Use an https:// address.';
  return problems;
}

function describeSource(draft: SourceDraft): string {
  return [
    `Name: ${draft.title}`,
    draft.type === 'feed' ? `Feed: ${draft.url}` : `Query: ${draft.query}`,
    `Site: ${draft.siteUrl || 'none'}`,
    `Pillar: ${draft.pillar === NO_PILLAR ? 'any' : draft.pillar}`,
  ].join('\n');
}

function FeedFinder({ onPick }: { onPick: (feed: DiscoveredFeed, siteUrl: string) => void }) {
  const [site, setSite] = useState('');
  const [problem, setProblem] = useState<string | undefined>();
  const groupId = useId();
  const discover = useApiMutation('post', '/api/sources/discover', {
    errorToast: false,
  });
  const feeds = discover.data?.feeds;
  const find = () => {
    if (!isHttpsAddress(site)) {
      setProblem('Use an https:// address.');
      return;
    }
    setProblem(undefined);
    discover.mutate({ body: { siteUrl: site.trim() } });
  };
  return (
    <div className="grid gap-3 rounded-lg border border-line bg-sunken p-4">
      <Field
        label="Find the feed on a site"
        description="Type the site’s address; the app looks for the feeds it links to."
        error={problem}
      >
        {(control) => (
          <div className="flex flex-wrap gap-2">
            <Input
              {...control}
              type="url"
              inputMode="url"
              placeholder="https://"
              value={site}
              className="min-w-0 flex-1"
              onChange={(event) => {
                setSite(event.target.value);
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  find();
                }
              }}
            />
            <Button variant="secondary" disabled={discover.isPending} onClick={find}>
              <Search aria-hidden="true" />
              {discover.isPending ? 'Looking…' : 'Find feeds'}
            </Button>
          </div>
        )}
      </Field>
      {discover.isError ? (
        <p role="alert" className="text-sm text-danger">
          {isProblem(discover.error, 'fetch_failed')
            ? 'The site couldn’t be read. Check the address, or paste the feed’s address below.'
            : problemMessage(discover.error)}
        </p>
      ) : null}
      {feeds?.length === 0 ? (
        <p role="status" className="text-sm text-ink-muted">
          No feeds found on that page. Paste the feed’s address below if you know it.
        </p>
      ) : null}
      {feeds && feeds.length > 0 ? (
        <div>
          <p id={groupId} className="text-sm font-medium text-ink">
            {feeds.length === 1 ? 'One feed found' : `${String(feeds.length)} feeds found`}
          </p>
          <ul aria-labelledby={groupId} className="mt-2 grid gap-2">
            {feeds.map((feed) => (
              <li key={feed.url} className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 text-sm">
                  <span className="block font-medium text-ink">{feed.title ?? 'Untitled feed'}</span>
                  <span className="block break-all text-ink-muted">{feed.url}</span>
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  aria-label={`Use ${feed.title ?? feed.url}`}
                  onClick={() => {
                    onPick(feed, site.trim());
                  }}
                >
                  Use this feed
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export interface SourceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The source to edit; a new one when left out. */
  source?: Versioned<Source>;
  pillars: readonly Pillar[];
  /** Reads the source again (after a conflict); resolves with the current version. */
  onReload?: () => Promise<Versioned<Source> | undefined>;
}

/** Add or edit a feed or a weekly scouting query. */
export function SourceDialog({ open, onOpenChange, source, pillars, onReload }: SourceDialogProps) {
  const editing = source !== undefined;
  const [base, setBase] = useState(source);
  const [draft, setDraft] = useState(() => draftOf(source?.item));
  const [attempted, setAttempted] = useState(false);
  const [latest, setLatest] = useState<Versioned<Source> | undefined>(undefined);
  const formRef = useRef<HTMLFormElement>(null);
  const conflict = useConflict();
  const handled = ['validation_failed', 'duplicate_source', 'unknown_pillar', 'confidential_text', 'etag_mismatch', 'blocklist_not_configured'];
  const create = useApiMutation('post', '/api/sources', {
    invalidates: [queryKeys.sources.lists()],
    successToast: (data) => `Following “${data.item.title}”.`,
    errorToast: 'Couldn’t add the source.',
    handles: handled,
  });
  const update = useApiMutation('put', '/api/sources/{source_id}', {
    updates: (data) => queryKeys.sources.detail(data.item.id),
    invalidates: [queryKeys.sources.lists()],
    successToast: (data) => `Saved “${data.item.title}”.`,
    errorToast: 'Couldn’t save the source.',
    handles: handled,
  });
  const mutation = editing ? update : create;
  const error = mutation.error;

  const clientErrors = attempted ? draftProblems(draft) : {};
  const serverErrors = fieldErrors(error);
  const confidential = confidentialFindings(error).find((finding) => finding.field === 'query');
  const errors: Record<string, string | undefined> = {
    title: clientErrors.title ?? serverErrors.title,
    url:
      clientErrors.url ??
      serverErrors.url ??
      (draft.type === 'feed' && isProblem(error, 'duplicate_source') ? 'You already follow this feed.' : undefined),
    siteUrl: clientErrors.siteUrl ?? serverErrors.siteUrl,
    query:
      clientErrors.query ??
      serverErrors.query ??
      (confidential ? 'This query names something on your blocklist. Rephrase it.' : undefined) ??
      (draft.type === 'scout_query' && isProblem(error, 'duplicate_source') ? 'You already scout this query.' : undefined),
    pillar: serverErrors.pillar ?? (isProblem(error, 'unknown_pillar') ? 'Pick one of your pillars.' : undefined),
  };

  const edit = (change: Partial<SourceDraft>) => {
    if (mutation.isError) mutation.reset();
    setDraft((current) => ({ ...current, ...change }));
  };

  const body = () => ({
    type: draft.type,
    title: draft.title.trim(),
    url: draft.type === 'feed' ? draft.url.trim() : null,
    siteUrl: draft.siteUrl.trim() === '' ? null : draft.siteUrl.trim(),
    query: draft.type === 'scout_query' ? draft.query.trim() : null,
    pillar: draft.pillar === NO_PILLAR ? null : draft.pillar,
  });

  const done = () => {
    setAttempted(false);
    conflict.close();
    onOpenChange(false);
  };

  const send = (etag?: string | null) => {
    if (base) {
      update.mutate(
        { params: { path: { source_id: base.item.id } }, body: body(), ifMatch: etag ?? base.etag },
        {
          onSuccess: (saved) => {
            setBase(saved);
            done();
          },
          onError: (failure) => {
            if (conflict.show(failure)) void onReload?.().then(setLatest);
          },
        },
      );
      return;
    }
    create.mutate({ body: body() }, { onSuccess: done });
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const problems = draftProblems(draft);
    if (Object.keys(problems).length > 0) {
      flushSync(() => {
        setAttempted(true);
      });
      focusFirstInvalid(formRef.current);
      return;
    }
    send();
  };

  // A refusal about a field shows on the field: take the owner there once it has rendered.
  const fieldRefusal = isProblem(error, ['validation_failed', 'duplicate_source', 'unknown_pillar', 'confidential_text']);
  useEffect(() => {
    if (fieldRefusal) focusFirstInvalid(formRef.current);
  }, [fieldRefusal, error]);

  const blocklistMissing = isProblem(error, 'blocklist_not_configured');
  const shownAsField = isProblem(error, [
    'validation_failed',
    'duplicate_source',
    'unknown_pillar',
    'confidential_text',
    'etag_mismatch',
    'blocklist_not_configured',
  ]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit “${source.item.title}”` : 'Add a source'}</DialogTitle>
          <DialogDescription>
            New items are sorted into your Ideas inbox. A source never starts writing anything by itself.
          </DialogDescription>
        </DialogHeader>
        <form ref={formRef} noValidate onSubmit={submit} className="grid gap-5" aria-label="Source">
          {editing ? null : (
            <Fieldset legend="What to follow">
              {(group) => (
                <RadioGroup
                  {...group}
                  value={draft.type}
                  onValueChange={(type) => {
                    edit({ type: type === 'scout_query' ? 'scout_query' : 'feed' });
                  }}
                >
                  <div className="flex items-start gap-3">
                    <RadioGroupItem id="source-type-feed" value="feed" className="mt-0.5" />
                    <div className="grid gap-0.5">
                      <Label htmlFor="source-type-feed">A feed</Label>
                      <p className="text-sm text-ink-muted">An RSS or Atom feed, read every few hours.</p>
                    </div>
                  </div>
                  <div className="flex items-start gap-3">
                    <RadioGroupItem id="source-type-scout" value="scout_query" className="mt-0.5" />
                    <div className="grid gap-0.5">
                      <Label htmlFor="source-type-scout">A weekly scouting query</Label>
                      <p className="text-sm text-ink-muted">A web search run once a week for the pillar.</p>
                    </div>
                  </div>
                </RadioGroup>
              )}
            </Fieldset>
          )}

          {draft.type === 'feed' ? (
            <>
              <FeedFinder
                onPick={(feed, siteUrl) => {
                  edit({
                    url: feed.url,
                    siteUrl: draft.siteUrl === '' ? siteUrl : draft.siteUrl,
                    title: draft.title === '' ? (feed.title ?? '') : draft.title,
                  });
                }}
              />
              <Field label="Feed address" error={errors.url} required>
                <Input
                  type="url"
                  inputMode="url"
                  placeholder="https://"
                  value={draft.url}
                  onChange={(event) => {
                    edit({ url: event.target.value });
                  }}
                />
              </Field>
            </>
          ) : (
            <Field
              label="Search query"
              description="Searched on the pillar’s trusted sites. It is checked against your blocklist first."
              error={errors.query}
              required
            >
              <Input
                value={draft.query}
                onChange={(event) => {
                  edit({ query: event.target.value });
                }}
              />
            </Field>
          )}

          <Field label="Name" error={errors.title} required>
            <Input
              value={draft.title}
              onChange={(event) => {
                edit({ title: event.target.value });
              }}
            />
          </Field>
          {draft.type === 'feed' ? (
            <Field label="Site address" description="Optional: where the feed comes from." error={errors.siteUrl}>
              <Input
                type="url"
                inputMode="url"
                placeholder="https://"
                value={draft.siteUrl}
                onChange={(event) => {
                  edit({ siteUrl: event.target.value });
                }}
              />
            </Field>
          ) : null}
          <Field label="Pillar" description="Items from it lean to this pillar during triage." error={errors.pillar}>
            {(control) => (
              <Select
                value={draft.pillar}
                onValueChange={(pillar) => {
                  edit({ pillar });
                }}
              >
                <SelectTrigger {...control}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_PILLAR}>Any pillar</SelectItem>
                  {pillars.map((pillar) => (
                    <SelectItem key={pillar.id} value={pillar.id}>
                      {pillar.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Field>

          {blocklistMissing ? (
            <Alert variant="error">
              <AlertTitle>Save your blocklist first.</AlertTitle>
              <AlertDescription>
                <p>
                  Scouting queries are checked against it before any search. An empty list is fine:{' '}
                  <Link to="/settings/policy" className="text-ink underline underline-offset-4">
                    open Blocklist &amp; policy
                  </Link>
                  .
                </p>
              </AlertDescription>
            </Alert>
          ) : mutation.isError && !shownAsField ? (
            <ProblemAlert error={error} title={editing ? 'Couldn’t save the source.' : 'Couldn’t add the source.'} />
          ) : null}

          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? 'Saving…' : editing ? 'Save the source' : 'Add the source'}
            </Button>
          </DialogFooter>
        </form>
        <ConflictDialog
          {...conflict}
          subject="This source"
          mine={describeSource(draft)}
          theirs={latest ? describeSource(draftOf(latest.item)) : undefined}
          saving={update.isPending}
          onKeepMine={() => {
            const etag = conflict.conflict?.etag;
            if (etag) send(etag);
          }}
          onTakeTheirs={() => {
            const current = latest;
            if (current) {
              setBase(current);
              setDraft(draftOf(current.item));
            }
            conflict.close();
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
