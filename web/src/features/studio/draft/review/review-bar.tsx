import {
  Archive,
  ArchiveRestore,
  Download,
  Ellipsis,
  Eye,
  History,
  Play,
  RotateCcw,
  Send,
  Sparkles,
  Square,
  Undo2,
} from 'lucide-react';
import { useState, type ReactNode, type RefObject } from 'react';
import { Link } from 'react-router';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Field } from '@/components/ui/field';
import { Textarea } from '@/components/ui/textarea';
import { toastError, toastSuccess } from '@/lib/api/toast';

import { formatDateTime } from '../model/format';
import type { ArticleActions } from '../model/status';
import type { Article } from '../model/types';

import { ConfirmDialog } from './confirm-dialog';
import { StatusBadge } from './status-badge';
import type { ArticleCommands } from './use-article-commands';

type Confirmation = 'cancel' | 'pullBack' | 'publishNow' | 'correct' | 'unpublish' | 'archive';

export interface ReviewBarProps {
  article: Article;
  actions: ArticleActions;
  commands: ArticleCommands;
  /** The save state, shown beside the status where the owner writes. */
  saveStatus: ReactNode;
  onApprove: () => void;
  onRevise: () => void;
  onStartPipeline: () => void;
  onVersions: () => void;
  onPreview: () => void;
  onExport: () => void;
  /** Where focus goes after an action removed the button that started it. */
  returnFocus?: RefObject<HTMLElement | null>;
}

/**
 * The article's status and what can be done with it now (contract section 2: the buttons are
 * the edges the state machine and the routes allow). One main action, the rest in "More".
 */
export function ReviewBar({
  article,
  actions,
  commands,
  saveStatus,
  onApprove,
  onRevise,
  onStartPipeline,
  onVersions,
  onPreview,
  onExport,
  returnFocus,
}: ReviewBarProps) {
  const [confirm, setConfirm] = useState<Confirmation | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const live = article.publish?.live ?? false;
  const status = article.status;

  const direct = async (run: () => Promise<unknown>, done: string, failed: string) => {
    setBusy(true);
    try {
      await run();
      toastSuccess(done);
    } catch (error) {
      toastError(error, { title: failed });
    } finally {
      setBusy(false);
    }
  };

  const confirmation = (() => {
    switch (confirm) {
      case null:
        return null;
      case 'cancel':
        return {
          title: 'Stop the pipeline run?',
          description: <p>The run stops where it is and the article waits for you. You can restart it from research, the interview or the draft.</p>,
          confirmLabel: 'Stop the run',
          pendingLabel: 'Stopping…',
          cancelLabel: 'Let it run',
          variant: 'danger' as const,
          run: async () => {
            await commands.cancelPipeline();
            toastSuccess('Run stopped. The article waits for you.');
          },
        };
      case 'pullBack':
        return {
          title: 'Pull it back to review?',
          description: <p>The scheduled publish is cancelled. Approve it again when it’s ready.</p>,
          confirmLabel: 'Pull it back',
          pendingLabel: 'Pulling it back…',
          cancelLabel: 'Keep it scheduled',
          variant: 'primary' as const,
          run: async () => {
            await commands.transition('in_review');
            toastSuccess('Back in review.');
          },
        };
      case 'publishNow':
        return {
          title: live ? 'Publish the correction now?' : 'Publish it now?',
          description: (
            <p>
              {status === 'publish_failed'
                ? 'Publishing runs again with the approved text.'
                : 'It goes to your portfolio straight away instead of at the next slot.'}
            </p>
          ),
          confirmLabel: status === 'publish_failed' ? 'Retry publishing' : 'Publish now',
          pendingLabel: 'Publishing…',
          cancelLabel: 'Not now',
          variant: 'primary' as const,
          run: async () => {
            await commands.publishNow(live && status === 'in_review' ? { correctionNote: note.trim() } : {});
            toastSuccess('Publishing now.');
          },
        };
      case 'correct':
        return {
          title: 'Start a correction?',
          description: <p>It goes back to review so you can edit it. The live page stays up until you publish the correction.</p>,
          confirmLabel: 'Start a correction',
          pendingLabel: 'Starting…',
          cancelLabel: 'Cancel',
          variant: 'primary' as const,
          run: async () => {
            await commands.transition('in_review');
            toastSuccess('Correction started. Edit it, then approve it again.');
          },
        };
      case 'unpublish':
        return {
          title: 'Unpublish this article?',
          description: <p>The page comes down from your portfolio. Its address stays reserved for it.</p>,
          confirmLabel: status === 'publish_failed' ? 'Retry unpublishing' : 'Unpublish',
          pendingLabel: 'Unpublishing…',
          cancelLabel: 'Keep it live',
          variant: 'danger' as const,
          run: async () => {
            await commands.unpublish();
            toastSuccess('Unpublishing.');
          },
        };
      case 'archive':
        return {
          title: 'Archive this article?',
          description: <p>It leaves the Studio list. You can restore it to planned later.</p>,
          confirmLabel: 'Archive',
          pendingLabel: 'Archiving…',
          cancelLabel: 'Keep it',
          variant: 'danger' as const,
          run: async () => {
            await commands.transition('archived');
            toastSuccess('Archived.');
          },
        };
    }
  })();

  const needsNote = confirm === 'publishNow' && live && status === 'in_review';
  const ask = (kind: Confirmation) => {
    setNote('');
    setConfirm(kind);
  };

  const main: ReactNode = (() => {
    switch (status) {
      case 'planned':
        return actions.startPipeline ? (
          <Button key="start-pipeline" onClick={onStartPipeline}>
            <Play aria-hidden="true" />
            Start the pipeline
          </Button>
        ) : null;
      case 'researching':
      case 'drafting':
      case 'gating':
        return actions.cancelPipeline ? (
          <Button
            key="stop-run"
            variant="secondary"
            onClick={() => {
              ask('cancel');
            }}
          >
            <Square aria-hidden="true" />
            Stop the run
          </Button>
        ) : null;
      case 'interviewing':
        return (
          <>
            {actions.cancelPipeline ? (
              <Button
                key="stop-run-interview"
                variant="secondary"
                onClick={() => {
                  ask('cancel');
                }}
              >
                <Square aria-hidden="true" />
                Stop the run
              </Button>
            ) : null}
            <Button key="answer-interview" asChild>
              <Link to={`/studio/${encodeURIComponent(article.id)}/interview`}>Answer the interview</Link>
            </Button>
          </>
        );
      case 'in_review':
        return (
          <>
            {actions.revise ? (
              <Button key="revise" variant="secondary" onClick={onRevise}>
                <Sparkles aria-hidden="true" />
                Request a revision
              </Button>
            ) : null}
            {actions.approve ? (
              <Button key="approve" onClick={onApprove}>
                <Send aria-hidden="true" />
                Approve…
              </Button>
            ) : null}
          </>
        );
      case 'needs_attention':
        return (
          <>
            {actions.backToReview ? (
              <Button
                key="back-to-review"
                variant="secondary"
                disabled={busy}
                onClick={() => void direct(() => commands.transition('in_review'), 'Back in review.', 'Couldn’t move it back to review.')}
              >
                <Undo2 aria-hidden="true" />
                Back to review
              </Button>
            ) : null}
            {actions.startPipeline ? (
              <Button key="restart-pipeline" onClick={onStartPipeline}>
                <RotateCcw aria-hidden="true" />
                Restart the pipeline
              </Button>
            ) : null}
          </>
        );
      case 'approved':
        return (
          <>
            {actions.pullBack ? (
              <Button
                key="pull-back"
                variant="secondary"
                onClick={() => {
                  ask('pullBack');
                }}
              >
                <Undo2 aria-hidden="true" />
                Pull back to review
              </Button>
            ) : null}
            {actions.publishNow ? (
              <Button
                key="publish-now"
                onClick={() => {
                  ask('publishNow');
                }}
              >
                <Send aria-hidden="true" />
                Publish now
              </Button>
            ) : null}
          </>
        );
      case 'published':
        return actions.correct ? (
          <Button
            key="correct"
            variant="secondary"
            onClick={() => {
              ask('correct');
            }}
          >
            Start a correction
          </Button>
        ) : null;
      case 'publish_failed':
        return (
          <>
            {actions.backToReview ? (
              <Button
                key="back-to-review-failed"
                variant="secondary"
                disabled={busy}
                onClick={() =>
                  void direct(
                    () => commands.transition('in_review', 'Back to review after a failed publish.'),
                    'Back in review. Change what you need, then approve it again.',
                    'Couldn’t move it back to review.',
                  )
                }
              >
                <Undo2 aria-hidden="true" />
                Back to review
              </Button>
            ) : null}
            {actions.unpublish ? (
              <Button
                key="retry-unpublish"
                variant={actions.publishNow ? 'secondary' : 'primary'}
                onClick={() => {
                  ask('unpublish');
                }}
              >
                Retry unpublishing
              </Button>
            ) : null}
            {actions.publishNow ? (
              <Button
                key="retry-publish"
                onClick={() => {
                  ask('publishNow');
                }}
              >
                Retry publishing
              </Button>
            ) : null}
          </>
        );
      case 'unpublished':
        return actions.backToReview ? (
          <Button
            key="back-to-review-unpublished"
            disabled={busy}
            onClick={() => void direct(() => commands.transition('in_review'), 'Back in review.', 'Couldn’t move it back to review.')}
          >
            <Undo2 aria-hidden="true" />
            Back to review
          </Button>
        ) : null;
      case 'archived':
        return actions.unarchive ? (
          <Button
            key="unarchive"
            disabled={busy}
            onClick={() => void direct(() => commands.transition('planned'), 'Restored to planned.', 'Couldn’t restore it.')}
          >
            <ArchiveRestore aria-hidden="true" />
            Restore
          </Button>
        ) : null;
      case 'publishing':
      case 'unpublishing':
        return null;
    }
  })();

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
        <StatusBadge status={status} />
        {saveStatus}
        <span className="text-sm text-ink-subtle">{`Version ${String(article.version)}, ${formatDateTime(article.updatedAt)}`}</span>
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {main}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="More actions">
              <Ellipsis aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={onPreview}>
              <Eye aria-hidden="true" />
              Preview as portfolio page
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onVersions}>
              <History aria-hidden="true" />
              Versions
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onExport}>
              <Download aria-hidden="true" />
              Export Markdown
            </DropdownMenuItem>
            {status === 'in_review' && (actions.publishNow || actions.startPipeline) ? <DropdownMenuSeparator /> : null}
            {status === 'in_review' && actions.publishNow ? (
              <DropdownMenuItem
                onSelect={() => {
                  ask('publishNow');
                }}
              >
                <Send aria-hidden="true" />
                Publish now
              </DropdownMenuItem>
            ) : null}
            {status === 'in_review' && actions.startPipeline ? (
              <DropdownMenuItem onSelect={onStartPipeline}>
                <RotateCcw aria-hidden="true" />
                Restart the pipeline
              </DropdownMenuItem>
            ) : null}
            {status === 'published' && actions.unpublish ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="danger"
                  onSelect={() => {
                    ask('unpublish');
                  }}
                >
                  Unpublish…
                </DropdownMenuItem>
              </>
            ) : null}
            {actions.archive ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="danger"
                  onSelect={() => {
                    ask('archive');
                  }}
                >
                  <Archive aria-hidden="true" />
                  Archive…
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {confirmation ? (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) setConfirm(null);
          }}
          title={confirmation.title}
          description={confirmation.description}
          confirmLabel={confirmation.confirmLabel}
          pendingLabel={confirmation.pendingLabel}
          cancelLabel={confirmation.cancelLabel}
          variant={confirmation.variant}
          ready={!needsNote || note.trim() !== ''}
          onConfirm={confirmation.run}
          returnFocus={returnFocus}
        >
          {needsNote ? (
            <Field label="Correction note" description="Shown on the page with today’s date." required>
              <Textarea
                rows={3}
                maxLength={1000}
                value={note}
                onChange={(event) => {
                  setNote(event.target.value);
                }}
              />
            </Field>
          ) : null}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
