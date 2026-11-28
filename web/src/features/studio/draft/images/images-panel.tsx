import { Eye, Lock, RectangleHorizontal } from 'lucide-react';
import { useId, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ConflictDialog, useConflict } from '@/features/data/conflict-dialog';
import { ProblemAlert } from '@/features/data/problem-alert';
import { isProblem } from '@/lib/api/problems';
import { toastError, toastSuccess } from '@/lib/api/toast';
import { mediaUrl } from '@/lib/urls';

import { useImageSettings, type GenerateRequest, type IllustrationCommands, type IllustrationRun } from '../api/illustrations';
import {
  candidatesNewestFirst,
  candidateUse,
  droppedSentence,
  generateBlockedReason,
  heroOf,
  imagePrices,
  imagesAvailability,
  imagesLockedReason,
  markdownHeadings,
  type Illustration,
} from '../model/illustrations';
import type { Writer } from '../model/status';
import type { Article } from '../model/types';
import { ConfirmDialog } from '../review/confirm-dialog';
import { UnsavedChangesError } from '../review/use-article-commands';

import { actionSentence, CandidateCard, type ActionOutcome, type CardAction } from './candidate-card';
import { GenerateForm } from './generate-form';
import { RunProgress, runEndSentence } from './run-progress';

const linkClass = 'font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink';

type Pending = { readonly action: 'generate' | 'clear' } | { readonly action: CardAction; readonly id: string };

/** A refused action as a toast: the request id for server answers, the reason for unsaved edits. */
function toastRefusal(error: unknown, title: string): void {
  if (error instanceof UnsavedChangesError) toast.error(title, { description: actionSentence(error) });
  else toastError(error, { title });
}

/** An action the conflict dialog's "Keep mine" can send again, with the current etag. */
type Retry = (etag: string) => Promise<void>;

export interface ImagesPanelProps {
  article: Article;
  writer: Writer | undefined;
  run: IllustrationRun;
  commands: IllustrationCommands;
  /** Reads the article again ("Take theirs", a run that is already going). */
  onReload: () => void;
  /** "Preview as portfolio page". */
  onPreview: () => void;
}

/**
 * Draft > Images: the article's AI illustrations (opt-in, `gpt-image-2`). The hero it publishes
 * with (an illustration, or the title card), a run that makes a hero and up to two images for the
 * text, followed live, and the candidates that passed their checks: use one as the hero, insert
 * one after a heading (a new version: the gates and the approval cover it), edit its alt text or
 * discard it. Every write sends the article's etag; a stale screen opens the conflict dialog.
 */
export function ImagesPanel({ article, writer, run, commands, onReload, onPreview }: ImagesPanelProps) {
  const headingId = useId();
  const heroId = useId();
  const makeId = useId();
  const listId = useId();
  const heroHeading = useRef<HTMLHeadingElement>(null);
  const listHeading = useRef<HTMLHeadingElement>(null);
  const retry = useRef<Retry | null>(null);
  const settings = useImageSettings();
  const conflict = useConflict();
  const [pending, setPending] = useState<Pending | null>(null);
  const [resolving, setResolving] = useState(false);
  const [startProblem, setStartProblem] = useState<unknown>();
  const [discarding, setDiscarding] = useState<Illustration | null>(null);

  const ownerEdits = writer === 'owner';
  const canDiscard = article.status !== 'archived';
  const busy = pending !== null || resolving;
  const hero = heroOf(article);
  const candidates = candidatesNewestFirst(article.illustrations ?? []);
  const headings = useMemo(() => markdownHeadings(article.markdown), [article.markdown]);
  const locked = imagesLockedReason(article.status, writer);
  const availability = settings.data ? imagesAvailability(settings.data) : undefined;
  const prices = settings.data ? imagePrices(settings.data) : undefined;
  const ended = run.active ? undefined : runEndSentence(run.status, run.error, run.outcome);
  // A run that succeeded may still have dropped images that failed their checks.
  const dropped = !run.active && run.status === 'succeeded' ? droppedSentence(run.outcome) : undefined;

  /** Why a run can't start; `undefined` when it can. The status reason is shown at the top instead. */
  const generateReason = settings.isPending
    ? 'Checking whether illustrations are on…'
    : settings.isError
      ? 'Couldn’t check whether illustrations are on.'
      : availability === 'unavailable'
        ? 'No image model is set up on this server.'
        : availability === 'disabled'
          ? 'Switch illustrations on in Settings > Models first.'
          : run.active
            ? 'Illustrations are being made for this article. They show here when they are ready.'
            : generateBlockedReason(article, writer);

  /** Runs one action; a 412 opens the conflict dialog, which may send it again. */
  const perform = async (key: Pending, action: (etag?: string) => Promise<void>, done: () => void): Promise<ActionOutcome> => {
    setPending(key);
    try {
      await action();
      done();
      return { kind: 'done' };
    } catch (error) {
      if (conflict.show(error)) {
        retry.current = async (etag) => {
          await action(etag);
          done();
        };
        return { kind: 'conflict' };
      }
      return { kind: 'failed', error };
    } finally {
      setPending(null);
    }
  };

  const generate = async (request: GenerateRequest) => {
    setStartProblem(undefined);
    const outcome = await perform(
      { action: 'generate' },
      async (etag) => {
        run.expect(await commands.generate(request, etag));
      },
      () => {
        toastSuccess('Making illustrations. They show here once they pass their checks.');
      },
    );
    if (outcome.kind !== 'failed') return;
    const { error } = outcome;
    if (isProblem(error, 'images_in_progress')) {
      // Another tab started a run: follow that one.
      const jobId = error.problem?.extensions?.jobId;
      if (typeof jobId === 'string') run.expect(jobId);
      onReload();
      toast('Illustrations are already being made for this article. Follow them here.');
      return;
    }
    if (isProblem(error, ['images_disabled', 'images_unavailable'])) void settings.refetch();
    setStartProblem(error);
  };

  const chooseHero = async (illustration: Illustration) => {
    const outcome = await perform(
      { action: 'hero', id: illustration.id },
      (etag) => commands.chooseHero(illustration.id, etag),
      () => {
        toastSuccess('Hero image set. The published page opens with it.');
        heroHeading.current?.focus();
      },
    );
    if (outcome.kind === 'failed') toastRefusal(outcome.error, 'Couldn’t set the hero image.');
  };

  const clearHero = async () => {
    const outcome = await perform(
      { action: 'clear' },
      (etag) => commands.clearHero(etag),
      () => {
        toastSuccess('The title card is the hero again.');
        heroHeading.current?.focus();
      },
    );
    if (outcome.kind === 'failed') toastRefusal(outcome.error, 'Couldn’t remove the hero image.');
  };

  const keepMine = () => {
    const etag = conflict.conflict?.etag;
    const again = retry.current;
    if (etag === undefined || again === null) {
      retry.current = null;
      onReload();
      conflict.close();
      return;
    }
    setResolving(true);
    again(etag).then(
      () => {
        retry.current = null;
        conflict.close();
      },
      (error: unknown) => {
        if (conflict.show(error)) return;
        retry.current = null;
        conflict.close();
        toastRefusal(error, 'Couldn’t apply your change.');
      },
    ).finally(() => {
      setResolving(false);
    });
  };

  return (
    <section aria-labelledby={headingId} className="grid gap-8">
      <div className="grid gap-2">
        <h2 id={headingId} className="font-serif text-xl font-medium text-ink">
          Images
        </h2>
        <p className="text-sm text-ink-muted">
          AI illustrations for this article: a landscape hero and up to two square images for the text. Nothing is
          used until you choose it, and your approval covers the text and its images.
        </p>
        {locked ? (
          <p className="flex items-start gap-2 text-sm text-ink">
            <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ink-subtle" />
            {locked}
          </p>
        ) : null}
      </div>

      <section aria-labelledby={heroId} className="grid gap-3">
        <h3 id={heroId} ref={heroHeading} tabIndex={-1} className="font-semibold text-ink outline-none">
          Hero image
        </h3>
        {hero.image ? (
          <>
            <img
              src={mediaUrl(hero.image.name)}
              alt={hero.image.altText ?? article.meta?.title ?? 'The hero image'}
              width={hero.image.width ?? undefined}
              height={hero.image.height ?? undefined}
              decoding="async"
              draggable={false}
              className="h-auto w-full rounded-md border border-line bg-sunken"
            />
            <p className="text-sm text-ink-muted">
              The published page opens with this image. Link previews keep the title card.
            </p>
          </>
        ) : (
          <>
            {hero.card ? (
              <img
                src={mediaUrl(hero.card.name)}
                alt={`The title card: ${hero.card.altText ?? article.meta?.title ?? 'the article’s title'}`}
                width={hero.card.width ?? undefined}
                height={hero.card.height ?? undefined}
                decoding="async"
                draggable={false}
                className="h-auto w-full rounded-md border border-line bg-sunken"
              />
            ) : null}
            <p className="text-sm text-ink-muted">
              The title card: your title on the brand colours. Use an illustration below to open the page with an
              image instead.
            </p>
          </>
        )}
        <div className="flex flex-wrap gap-2">
          {hero.image && ownerEdits ? (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => void clearHero()}>
              <RectangleHorizontal aria-hidden="true" />
              {pending?.action === 'clear' ? 'Switching…' : 'Use the title card instead'}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={onPreview}>
            <Eye aria-hidden="true" />
            Preview as portfolio page
          </Button>
        </div>
      </section>

      <section aria-labelledby={makeId} className="grid gap-4">
        <h3 id={makeId} className="font-semibold text-ink">
          Make illustrations
        </h3>
        {settings.isPending ? (
          <div aria-busy="true" className="grid gap-2">
            <p className="sr-only">Checking whether illustrations are on</p>
            <Skeleton className="h-16 w-full" />
          </div>
        ) : settings.isError ? (
          <ProblemAlert
            error={settings.error}
            title="Couldn’t check whether illustrations are on."
            onRetry={() => void settings.refetch()}
          />
        ) : availability === 'unavailable' ? (
          <Alert variant="info">
            <AlertTitle>No image model on this server</AlertTitle>
            <AlertDescription>
              <p>
                Illustrations need an image model, and none is set up here yet. The candidates below can still be used.
              </p>
            </AlertDescription>
          </Alert>
        ) : availability === 'disabled' ? (
          <Alert variant="info">
            <AlertTitle>Illustrations are off</AlertTitle>
            <AlertDescription>
              <p>Switch them on to make a hero and images for the text. Each image costs a few cents to a quarter.</p>
              <p>
                <Link to="/settings/models" className={linkClass}>
                  Open Settings &gt; Models
                </Link>
              </p>
            </AlertDescription>
          </Alert>
        ) : null}
        {run.active && run.jobId ? (
          <RunProgress jobId={run.jobId} />
        ) : ended ? (
          <Alert variant="warn">
            <AlertTitle>No new illustrations</AlertTitle>
            <AlertDescription>
              <p>{ended}</p>
            </AlertDescription>
          </Alert>
        ) : dropped ? (
          <p className="text-sm text-ink-muted">{`The last run: ${dropped}`}</p>
        ) : null}
        <GenerateForm
          disabled={generateReason !== undefined || locked !== undefined}
          reason={locked === undefined ? generateReason : undefined}
          pending={pending?.action === 'generate'}
          prices={prices}
          problem={startProblem}
          onGenerate={(request) => void generate(request)}
          onEdit={() => {
            setStartProblem(undefined);
          }}
        />
      </section>

      <section aria-labelledby={listId} className="grid gap-4">
        <h3 id={listId} ref={listHeading} tabIndex={-1} className="font-semibold text-ink outline-none">
          {candidates.length === 0 ? 'Candidates' : `Candidates (${String(candidates.length)})`}
        </h3>
        {candidates.length === 0 ? (
          <p className="text-sm text-ink-muted">
            None yet. Only images that pass their checks (the idea shown, no text, logos or real people) appear here.
          </p>
        ) : (
          <ul aria-labelledby={listId} className="grid gap-8 sm:grid-cols-2 lg:grid-cols-1">
            {candidates.map((illustration) => {
              const id = illustration.id;
              const mine = pending !== null && 'id' in pending && pending.id === id ? pending.action : null;
              return (
                <li key={id}>
                  <CandidateCard
                    illustration={illustration}
                    use={candidateUse(article, illustration)}
                    headings={headings}
                    canEdit={ownerEdits}
                    canDiscard={canDiscard}
                    busy={busy}
                    pending={mine}
                    onHero={() => void chooseHero(illustration)}
                    onInsert={(heading) =>
                      perform(
                        { action: 'insert', id },
                        (etag) => commands.insert(id, heading.source, etag),
                        () => {
                          toastSuccess(`Inserted after “${heading.label}”. The text changed: run the gates again before you approve.`);
                        },
                      )
                    }
                    onAlt={(altText) =>
                      perform(
                        { action: 'alt', id },
                        (etag) => commands.editAlt(id, altText, etag),
                        () => {
                          toastSuccess('Alt text saved.');
                        },
                      )
                    }
                    onDiscard={() => {
                      setDiscarding(illustration);
                    }}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <ConflictDialog
        {...conflict}
        onOpenChange={(open) => {
          if (!open) retry.current = null;
          conflict.onOpenChange(open);
        }}
        subject="This article"
        saving={resolving}
        onKeepMine={keepMine}
        onTakeTheirs={() => {
          retry.current = null;
          onReload();
          conflict.close();
        }}
      />
      <ConfirmDialog
        open={discarding !== null}
        onOpenChange={(open) => {
          if (!open) setDiscarding(null);
        }}
        title="Discard this illustration?"
        description={<p>It leaves this article’s candidates for good. A new one needs another run.</p>}
        confirmLabel="Discard"
        pendingLabel="Discarding…"
        cancelLabel="Keep it"
        variant="danger"
        returnFocus={listHeading}
        onConfirm={async () => {
          const target = discarding;
          if (!target) return;
          const outcome = await perform(
            { action: 'discard', id: target.id },
            (etag) => commands.discard(target.id, etag),
            () => {
              toastSuccess('Illustration discarded.');
            },
          );
          // Shown in the dialog, which stays open.
          if (outcome.kind === 'failed') throw outcome.error;
        }}
      />
    </section>
  );
}
