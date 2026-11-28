import { Eye, Pin, RotateCcw } from 'lucide-react';
import { useId, useState } from 'react';

import { Button } from '@/components/ui/button';
import { DiffView } from '@/components/ui/diff-view';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { toastSuccess } from '@/lib/api/toast';
import { cn } from '@/lib/cn';

import { useArticleVersion, useVersions } from '../api/queries';
import { formatDateTime } from '../model/format';
import { STATUS_LABELS } from '../model/status';
import type { VersionSummary } from '../model/types';
import { ConfirmDialog } from '../review/confirm-dialog';

const SOURCE_LABELS: Readonly<Record<string, string>> = {
  autosave: 'Your edits',
  pipeline: 'Pipeline',
  restore: 'Restored',
  publish: 'Published',
  correction: 'Correction',
};

const AUTHOR_LABELS: Readonly<Record<string, string>> = {
  owner: 'you',
  pipeline: 'the pipeline',
  system: 'the app',
};

function sourceLabel(summary: VersionSummary): string {
  return SOURCE_LABELS[summary.source] ?? summary.source;
}

export interface VersionsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  articleId: string;
  /** The text in the editor now, for "compare with the current text". */
  currentMarkdown: () => string;
  /** Restoring is for the owner's states only. */
  canRestore: boolean;
  onRestore: (version: number) => Promise<unknown>;
  onPreview: (version: number) => void;
}

/**
 * Versions: the saved copies of the article (the last 50, plus the kept ones: gated, reported,
 * last published), a block-level comparison with the current text or the version before, and
 * restore, which makes a copy the current text again as a new version.
 */
export function VersionsSheet({ open, onOpenChange, articleId, currentMarkdown, canRestore, onRestore, onPreview }: VersionsSheetProps) {
  const listId = useId();
  const versions = useVersions(articleId, open);
  const entries = versions.data ?? [];
  const [selected, setSelected] = useState<number | null>(null);
  const [against, setAgainst] = useState<'current' | 'previous'>('current');
  const [current, setCurrent] = useState('');
  const [restoring, setRestoring] = useState(false);
  const chosen = selected ?? entries[0]?.item.version ?? null;
  const index = entries.findIndex((entry) => entry.item.version === chosen);
  const previous = index >= 0 ? (entries[index + 1]?.item.version ?? null) : null;
  const version = useArticleVersion(articleId, open ? chosen : null);
  const older = useArticleVersion(articleId, open && against === 'previous' ? previous : null);
  const summary = index >= 0 ? entries[index]?.item : undefined;

  const select = (value: number) => {
    setSelected(value);
    setCurrent(currentMarkdown());
  };

  const comparison = (() => {
    const text = version.data?.item.markdown;
    if (text === undefined) return null;
    if (against === 'current') return { before: text, after: current === '' ? currentMarkdown() : current, label: `Version ${String(chosen)} compared with the current text` };
    const earlier = older.data?.item.markdown;
    if (previous === null) return { before: '', after: text, label: `Version ${String(chosen)}, the oldest kept` };
    return earlier === undefined ? null : { before: earlier, after: text, label: `Version ${String(previous)} compared with version ${String(chosen)}` };
  })();

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (next) setCurrent(currentMarkdown());
        onOpenChange(next);
      }}
    >
      <SheetContent side="right" className="w-[min(60rem,96vw)] gap-0 overflow-hidden p-0">
        <SheetHeader className="border-b border-line p-6">
          <SheetTitle>Versions</SheetTitle>
          <SheetDescription>
            Saved copies of this article. Kept versions (the gated one, the reported one, the last published) are never
            pruned.
          </SheetDescription>
        </SheetHeader>
        <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[17rem_minmax(0,1fr)] md:grid-rows-1">
          <div className="max-h-[35dvh] overflow-y-auto border-b border-line md:max-h-none md:border-b-0 md:border-r">
            {versions.isPending ? (
              <div className="grid gap-2 p-4" aria-busy="true">
                <p className="sr-only">Loading versions</p>
                <Skeleton className="h-12" />
                <Skeleton className="h-12" />
                <Skeleton className="h-12" />
              </div>
            ) : versions.isError ? (
              <ProblemAlert
                className="m-4"
                error={versions.error}
                title="Couldn’t load the versions."
                onRetry={() => void versions.refetch()}
              />
            ) : entries.length === 0 ? (
              <p className="p-4 text-sm text-ink-muted">No saved versions yet. One is kept as you edit and at every pipeline step.</p>
            ) : (
              <ul id={listId} aria-label="Saved versions" className="divide-y divide-line">
                {entries.map(({ item }) => {
                  const active = item.version === chosen;
                  return (
                    <li key={item.version}>
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => {
                          select(item.version);
                        }}
                        className={cn(
                          'grid w-full gap-0.5 px-4 py-3 text-left hover:bg-wash pointer-coarse:min-h-11',
                          active && 'bg-wash-strong',
                        )}
                      >
                        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
                          {`Version ${String(item.version)}`}
                          {item.pinned ? (
                            <span className="inline-flex items-center gap-1 text-xs font-medium text-ink-muted">
                              <Pin aria-hidden="true" className="size-3" />
                              Kept
                            </span>
                          ) : null}
                        </span>
                        <span className="text-xs text-ink-muted">{`${sourceLabel(item)}, by ${AUTHOR_LABELS[item.author] ?? item.author}`}</span>
                        <span className="text-xs text-ink-subtle">{`${formatDateTime(item.createdAt)}, ${STATUS_LABELS[item.statusAtSave]}`}</span>
                        {item.note ? <span className="text-xs text-ink-subtle">{item.note}</span> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            {versions.hasNextPage ? (
              <div className="p-3">
                <Button
                  variant="secondary"
                  size="sm"
                  className="w-full"
                  disabled={versions.isFetchingNextPage}
                  onClick={() => void versions.fetchNextPage()}
                >
                  {versions.isFetchingNextPage ? 'Loading…' : 'Show older versions'}
                </Button>
              </div>
            ) : null}
          </div>
          <div className="grid min-h-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-4 overflow-hidden p-4 sm:p-6">
            {chosen === null ? (
              <p className="text-sm text-ink-muted">Choose a version to compare it.</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="font-serif text-xl font-medium text-ink">{`Version ${String(chosen)}`}</h3>
                  <SegmentedControl
                    aria-label="Compare with"
                    value={against}
                    onValueChange={(value) => {
                      setAgainst(value === 'previous' ? 'previous' : 'current');
                    }}
                  >
                    <SegmentedControlItem value="current">With the current text</SegmentedControlItem>
                    <SegmentedControlItem value="previous">With the one before</SegmentedControlItem>
                  </SegmentedControl>
                </div>
                <div className="min-h-0 overflow-y-auto rounded-md border border-line bg-sheet p-3">
                  {version.isError ? (
                    <ProblemAlert error={version.error} title="Couldn’t load this version." onRetry={() => void version.refetch()} />
                  ) : comparison ? (
                    <DiffView before={comparison.before} after={comparison.after} label={comparison.label} context={2} />
                  ) : (
                    <div className="grid gap-2" aria-busy="true">
                      <p className="sr-only">Loading the comparison</p>
                      <Skeleton className="h-6" />
                      <Skeleton className="h-24" />
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => {
                      onPreview(chosen);
                    }}
                  >
                    <Eye aria-hidden="true" />
                    Preview this version
                  </Button>
                  {canRestore ? (
                    <Button
                      onClick={() => {
                        setRestoring(true);
                      }}
                    >
                      <RotateCcw aria-hidden="true" />
                      Restore this version
                    </Button>
                  ) : null}
                </div>
              </>
            )}
          </div>
        </div>
        <ConfirmDialog
          open={restoring}
          onOpenChange={setRestoring}
          title={`Restore version ${String(chosen ?? '')}?`}
          description={
            <p>
              Its text and details become the current version{summary ? ` (from ${formatDateTime(summary.createdAt)})` : ''}. The text you have now stays in
              the versions list.
            </p>
          }
          confirmLabel="Restore"
          pendingLabel="Restoring…"
          cancelLabel="Keep the current text"
          onConfirm={async () => {
            if (chosen === null) return;
            await onRestore(chosen);
            toastSuccess(`Restored version ${String(chosen)}.`);
            onOpenChange(false);
          }}
        />
      </SheetContent>
    </Sheet>
  );
}
