import { useQueryClient } from '@tanstack/react-query';
import { CircleAlert, CircleCheck, Gauge } from 'lucide-react';
import type { SubmitEvent } from 'react';
import { Link } from 'react-router';

import { EmptyState } from '@/components/shell/empty-state';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { StatTile } from '@/components/ui/stat-tile';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useSpend } from '@/features/insights/queries';
import type { Versioned } from '@/lib/api/envelope';
import { fieldErrors, isProblem } from '@/lib/api/problems';
import { useApiMutation, useApiQuery } from '@/lib/query/api-query';

import { BlockLoading, SectionStack, SettingsBlock } from './components/settings-block';
import { SaveBar, useVersionedDraft } from './components/form-kit';
import { formatPercent, formatPrice, formatUsd } from './format';
import { settingsKeys, useSystemStatus, type Schemas } from './queries';

type ModelsView = Schemas['ModelsView'];
type RouteView = Schemas['RouteView'];
type ModelPreferences = Schemas['ModelPreferences'];
type BrakeView = Schemas['BrakeView'];

/** What each AI task is for, in the owner's words (`blogwriter.ai.types.Task`). */
const TASK_LABELS: Readonly<Record<string, string>> = {
  draft: 'Drafting articles and posts',
  revise: 'Revisions and selection edits',
  outline: 'Outlines',
  plan: 'Research plans and the weekly plan',
  interview: 'Interview questions',
  synthesis: 'Briefs, guides and suggestions',
  critique: 'Rubric critique',
  fact_check: 'Fact and confidentiality checks',
  anonymize: 'Anonymizing your raw text',
  research: 'Web research',
  quick_search: 'Quick search and scouting',
  triage: 'Feed triage',
  engagement: 'Reply and comment angles',
  embed: 'Embeddings for novelty',
  image: 'Illustrations',
};

/** The most sensitive data a task may receive (`Sensitivity`). */
const DATA_CLASS: Readonly<Record<string, string>> = {
  public: 'Public only',
  anonymized: 'Anonymized',
  private: 'Your raw text',
};

function taskLabel(task: string): string {
  return TASK_LABELS[task] ?? task.replace(/_/g, ' ');
}

// ------------------------------------------------------------------------------------------------
// Spend and the brake
// ------------------------------------------------------------------------------------------------

function brakeTone(brake: BrakeView): 'ink' | 'warn' | 'danger' {
  if (brake.engaged) return 'danger';
  const { spendTodayUsd: spent, thresholdUsd: limit } = brake;
  if (spent !== null && limit !== null && limit > 0 && spent / limit >= 0.8) return 'warn';
  return 'ink';
}

function BrakeMeter({ brake }: { brake: BrakeView }) {
  const spent = brake.spendTodayUsd;
  const limit = brake.thresholdUsd;
  const known = spent !== null && limit !== null && limit > 0;
  const reading = known ? `${formatUsd(spent)} of ${formatUsd(limit)}` : 'The ledger couldn’t be read';
  return (
    <div className="max-w-measure">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="font-semibold text-ink">Today’s spend</p>
        <p className="text-sm text-ink-muted">
          <span className="text-2xl font-semibold text-ink">{spent === null ? '–' : formatUsd(spent)}</span>
          {limit === null ? null : <> of {formatUsd(limit)} before the brake</>}
        </p>
      </div>
      <Progress
        className="mt-3"
        size="lg"
        tone={brakeTone(brake)}
        value={known ? spent : null}
        max={known ? limit : undefined}
        aria-label="Today’s AI spend against the brake"
        getValueLabel={() => reading}
      />
      {brake.engaged ? (
        <Alert variant="error" className="mt-4">
          <AlertTitle>The brake is on.</AlertTitle>
          <AlertDescription>
            <p>
              Background AI work waits until tomorrow’s budget, then carries on by itself. Your own actions still run.
            </p>
          </AlertDescription>
        </Alert>
      ) : (
        <p className="mt-3 flex items-start gap-2 text-sm text-ink-muted">
          <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-success" />
          Background work runs. It pauses by itself if today’s spend passes the brake: a multiple of your trailing
          daily median, or the daily cap, whichever is higher.
        </p>
      )}
    </div>
  );
}

function SpendBlock() {
  const status = useSystemStatus();
  const spend = useSpend(7);
  if (status.isPending) return <BlockLoading label="Loading today’s spend" />;
  if (status.isError) {
    return (
      <ProblemAlert
        error={status.error}
        title="Couldn’t load today’s spend."
        onRetry={() => void status.refetch()}
        retryLabel="Try loading today’s spend again"
      />
    );
  }
  const brake = status.data.brake;
  if (brake === null) {
    return (
      <Alert variant="info" className="max-w-measure">
        <AlertTitle>No AI models are set up, so nothing is spent.</AlertTitle>
        <AlertDescription>
          <p>Once the models are configured, today’s spend and the brake show here.</p>
        </AlertDescription>
      </Alert>
    );
  }
  const view = spend.data;
  return (
    <div className="space-y-8">
      <BrakeMeter brake={brake} />
      {spend.isError ? (
        <ProblemAlert
          error={spend.error}
          title="Couldn’t load the fallback rate."
          onRetry={() => void spend.refetch()}
          retryLabel="Try loading the fallback rate again"
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <StatTile
            label="Fallback rate, 7 days"
            value={view?.fallbackRate ?? null}
            format={(value) => formatPercent(value, 1)}
            caption="Calls served by a model other than the task’s first choice."
          />
          <StatTile
            label="Held for review, 7 days"
            value={view?.reviewOnlyRate ?? null}
            format={(value) => formatPercent(value, 1)}
            caption="Drafts and posts a fallback or a refusal made review-only."
          />
          <StatTile
            label="Trailing daily median"
            value={view?.trailingMedian ?? null}
            format={formatUsd}
            caption="What a normal day costs; the brake is set from it."
          />
        </div>
      )}
      <p className="text-sm text-ink-muted">
        Spend by day, task and model is in{' '}
        <Link to="/settings/insights" className="text-ink underline decoration-line-strong underline-offset-4">
          Insights
        </Link>
        .
      </p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------
// Routing and prices (read-only)
// ------------------------------------------------------------------------------------------------

function ChainCell({ route }: { route: RouteView }) {
  const [primary, ...fallbacks] = route.chain;
  if (!primary) return <span className="text-ink-subtle">No model</span>;
  return (
    <div className="space-y-1">
      <p className="font-medium text-ink">
        {primary.model}
        {primary.effort ? <span className="font-normal text-ink-muted"> ({primary.effort} effort)</span> : null}
      </p>
      {fallbacks.length > 0 ? (
        <p className="text-sm text-ink-muted">
          Falls back to {fallbacks.map((model) => model.model).join(', then ')}
        </p>
      ) : (
        <p className="text-sm text-ink-subtle">No fallback</p>
      )}
    </div>
  );
}

function RoutingTable({ routes }: { routes: readonly RouteView[] }) {
  return (
    <Table>
      <TableCaption>
        Each task’s first model, then its fallbacks. A draft written by a fallback, or after any refusal, is held for
        your review.
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Task</TableHead>
          <TableHead>Models</TableHead>
          <TableHead>Data it may see</TableHead>
          <TableHead>Web search</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {routes.map((route) => (
          <TableRow key={route.task}>
            <TableCell className="font-medium">{taskLabel(route.task)}</TableCell>
            <TableCell>
              <ChainCell route={route} />
            </TableCell>
            <TableCell>{DATA_CLASS[route.maxClass] ?? route.maxClass}</TableCell>
            <TableCell>{route.tools ? 'Yes' : 'No'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function PricesTable({ view }: { view: ModelsView }) {
  if (view.prices.length === 0) {
    return <p className="text-ink-muted">Prices couldn’t be read from the server’s settings.</p>;
  }
  const optional = (value: number | null) => (value === null ? '–' : formatPrice(value));
  // Image models are priced per image, by size (`imageUsd`).
  const perImage = view.prices.flatMap((price) => {
    const sizes = Object.entries(price.imageUsd ?? {});
    if (sizes.length === 0) return [];
    const list = sizes.map(([size, usd]) => `${formatPrice(usd)} at ${size.replace('x', '×')}`).join(', ');
    return [` ${price.model} is priced per image: ${list}.`];
  });
  return (
    <Table>
      <TableCaption>
        US dollars per million tokens.
        {view.webSearchUsdPer1k === null
          ? null
          : ` Web search costs ${formatPrice(view.webSearchUsdPer1k)} per thousand searches.`}
        {perImage.join('')}
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Model</TableHead>
          <TableHead numeric>Input</TableHead>
          <TableHead numeric>Cached input</TableHead>
          <TableHead numeric>Output</TableHead>
          <TableHead numeric>Cache write</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {view.prices.map((price) => (
          <TableRow key={price.model}>
            <TableCell className="font-medium">{price.model}</TableCell>
            <TableCell numeric>{formatPrice(price.input)}</TableCell>
            <TableCell numeric>{optional(price.cachedInput)}</TableCell>
            <TableCell numeric>{formatPrice(price.output)}</TableCell>
            <TableCell numeric>{optional(price.cacheWrite)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ------------------------------------------------------------------------------------------------
// Preferences
// ------------------------------------------------------------------------------------------------

interface PreferencesDraft {
  imagesEnabled: boolean;
  novelty: string;
  nearDuplicate: string;
}

function toDraft(item: ModelPreferences): PreferencesDraft {
  return {
    imagesEnabled: item.imagesEnabled,
    novelty: String(item.noveltyMaxSimilarity),
    nearDuplicate: String(item.nearDuplicateMaxSimilarity),
  };
}

/** A similarity limit: more than 0, at most 1. */
function similarityError(text: string): string | undefined {
  const value = Number(text);
  if (text.trim() === '' || !Number.isFinite(value)) return 'Enter a number between 0 and 1.';
  if (value <= 0 || value > 1) return 'Use a number above 0 and at most 1.';
  return undefined;
}

function describePreferences(item: ModelPreferences): string {
  return [
    `Illustrations: ${item.imagesEnabled ? 'on' : 'off'}`,
    `Novelty limit: ${String(item.noveltyMaxSimilarity)}`,
    `Near-duplicate limit: ${String(item.nearDuplicateMaxSimilarity)}`,
  ].join('\n');
}

function PreferencesForm({
  source,
  imageRoute,
  latest,
  onReload,
}: {
  source: Versioned<ModelPreferences>;
  imageRoute: boolean;
  /** The newest version the page has read, for the conflict dialog's comparison. */
  latest: Versioned<ModelPreferences>;
  /** Reads the document again; resolves with the current version. */
  onReload: () => Promise<Versioned<ModelPreferences> | undefined>;
}) {
  const queryClient = useQueryClient();
  const form = useVersionedDraft(source, toDraft);
  const { draft, setDraft } = form;
  const conflict = useConflict();
  const save = useApiMutation('put', '/api/kb/models', {
    successToast: 'Model preferences saved.',
    errorToast: 'Couldn’t save the model preferences.',
    handles: ['etag_mismatch', 'validation_failed', 'images_unavailable'],
    onSuccess: (data) => {
      queryClient.setQueryData<ModelsView>(settingsKeys.models, (old) => (old ? { ...old, preferences: data } : old));
    },
  });
  const serverErrors = fieldErrors(save.error);
  const errors = {
    novelty: similarityError(draft.novelty) ?? serverErrors.noveltyMaxSimilarity,
    nearDuplicate: similarityError(draft.nearDuplicate) ?? serverErrors.nearDuplicateMaxSimilarity,
  };
  const imagesRefused = isProblem(save.error, 'images_unavailable');

  const send = (etag: string | null) => {
    save.mutate(
      {
        body: {
          imagesEnabled: draft.imagesEnabled,
          noveltyMaxSimilarity: Number(draft.novelty),
          nearDuplicateMaxSimilarity: Number(draft.nearDuplicate),
        },
        ifMatch: etag,
      },
      {
        onSuccess: (data) => {
          form.reset(data);
          conflict.close();
        },
        onError: (error) => {
          if (conflict.show(error)) void onReload();
        },
      },
    );
  };

  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (errors.novelty || errors.nearDuplicate) return;
    send(form.base.etag);
  };

  return (
    <form noValidate onSubmit={submit} className="max-w-measure space-y-6" aria-label="Model preferences">
      <Field
        orientation="horizontal"
        label="Illustrations"
        description={
          imageRoute
            ? 'Lets the Draft tab make AI illustrations for an article: a hero image and up to two images for the text. Nothing is used until you pick it. Off by default.'
            : 'Needs an image model route, and none is set up on this server.'
        }
        error={imagesRefused ? 'No image model is set up, so illustrations stay off.' : undefined}
      >
        <Switch
          checked={draft.imagesEnabled}
          disabled={!imageRoute && !draft.imagesEnabled}
          onCheckedChange={(checked) => {
            setDraft((current) => ({ ...current, imagesEnabled: checked }));
          }}
        />
      </Field>
      <Field
        label="Novelty limit for articles"
        description="Gate 4 fails an article this similar to an earlier one (cosine similarity, 0 to 1). Lower is stricter."
        error={errors.novelty}
        required
      >
        <Input
          type="number"
          inputMode="decimal"
          step="0.01"
          min="0.01"
          max="1"
          value={draft.novelty}
          className="max-w-40"
          onChange={(event) => {
            setDraft((current) => ({ ...current, novelty: event.target.value }));
          }}
        />
      </Field>
      <Field
        label="Near-duplicate limit for posts"
        description="A post this similar to any post of the last 90 days is blocked (word overlap, 0 to 1). Lower is stricter."
        error={errors.nearDuplicate}
        required
      >
        <Input
          type="number"
          inputMode="decimal"
          step="0.01"
          min="0.01"
          max="1"
          value={draft.nearDuplicate}
          className="max-w-40"
          onChange={(event) => {
            setDraft((current) => ({ ...current, nearDuplicate: event.target.value }));
          }}
        />
      </Field>
      <SaveBar
        dirty={form.dirty}
        saving={save.isPending}
        paused={save.isPaused}
        saveLabel="Save preferences"
        allowClean={form.base.etag === null}
        idleNote={form.base.etag === null ? 'The defaults apply until you save.' : undefined}
        onDiscard={() => {
          form.reset(form.base);
        }}
      />
      <ConflictDialog
        {...conflict}
        subject="The model preferences"
        mine={describePreferences({
          ...form.base.item,
          imagesEnabled: draft.imagesEnabled,
          noveltyMaxSimilarity: Number(draft.novelty),
          nearDuplicateMaxSimilarity: Number(draft.nearDuplicate),
        })}
        theirs={latest.etag === form.base.etag ? undefined : describePreferences(latest.item)}
        saving={save.isPending}
        onKeepMine={() => {
          const etag = conflict.conflict?.etag;
          if (etag) send(etag);
          else {
            void onReload().then(() => {
              conflict.close();
            });
          }
        }}
        onTakeTheirs={() => {
          void onReload().then((current) => {
            if (current) form.reset(current);
            conflict.close();
          });
        }}
      />
    </form>
  );
}

// ------------------------------------------------------------------------------------------------
// The section
// ------------------------------------------------------------------------------------------------

/**
 * Settings > Models (`/settings/models`): today's spend against the brake and the fallback rate,
 * the routing and prices (read-only, from the server's environment) and the preferences the owner
 * tunes (illustrations, the novelty and near-duplicate limits). Calls `GET/PUT /api/kb/models`,
 * `GET /api/system/status` and `GET /api/analytics/spend` (contract sections 6 and 10).
 */
export function ModelsSection() {
  const models = useApiQuery({ queryKey: settingsKeys.models, path: '/api/kb/models' });

  return (
    <SectionStack>
      <SettingsBlock
        title="Spend and the brake"
        description="Spend is counted as it happens. A daily brake pauses background AI work when a day runs away; anything you start yourself is never braked."
      >
        <SpendBlock />
      </SettingsBlock>

      {models.isPending ? (
        <BlockLoading label="Loading the model routing" lines={5} />
      ) : models.isError ? (
        <ProblemAlert
          error={models.error}
          title="Couldn’t load the model routing."
          onRetry={() => void models.refetch()}
          retryLabel="Try loading the model routing again"
        />
      ) : (
        <>
          {models.data.routes.length === 0 ? (
            <EmptyState
              title="Model routing is not connected yet."
              yourPart="Ask for the models to be set up on the server (the BW_AI settings). Nothing switches models without showing it here."
            >
              <p>
                Each task gets a first-choice model and a fallback. Until the models are set up, every AI action
                answers that it can’t run yet.
              </p>
            </EmptyState>
          ) : (
            <SettingsBlock
              title="Routing"
              description="Which model handles each task, from the server’s settings. A fallback is used only when the first choice refuses or fails."
              actions={
                <Badge variant="outline">
                  <Gauge aria-hidden="true" />
                  Read-only
                </Badge>
              }
            >
              <RoutingTable routes={models.data.routes} />
            </SettingsBlock>
          )}

          <SettingsBlock title="Prices" description="The rates the spend ledger uses, from the server’s settings.">
            <PricesTable view={models.data} />
          </SettingsBlock>

          <SettingsBlock
            title="Preferences"
            description="Limits the gates use to catch repeats, and whether articles may get AI illustrations."
          >
            {models.data.preferences.etag === null ? (
              <p className="mb-6 flex max-w-measure items-start gap-2 text-sm text-ink-muted">
                <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
                These are the defaults. Saving keeps them as your own.
              </p>
            ) : null}
            <PreferencesForm
              source={models.data.preferences}
              latest={models.data.preferences}
              imageRoute={models.data.routes.some((route) => route.task === 'image')}
              onReload={() => models.refetch().then((result) => result.data?.preferences)}
            />
          </SettingsBlock>
        </>
      )}
    </SectionStack>
  );
}
