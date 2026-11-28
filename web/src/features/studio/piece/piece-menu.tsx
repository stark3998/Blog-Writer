import { Archive, Ellipsis, FileText, Repeat2 } from 'lucide-react';
import { useState } from 'react';

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { isEtagMismatch } from '@/lib/api/envelope';
import { toastError } from '@/lib/api/toast';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import { machineAllows, ownerCanMoveArticle, type PieceFacts } from './piece-status';
import { canRepurpose, useRepurpose } from './repurpose';
import type { Machines } from './types';

export interface PieceMenuProps {
  facts: PieceFacts;
  etag: string | null | undefined;
  machines: Machines | undefined;
  /** `GET /api/meta/machines` failed: Archive stays available and the route decides. */
  machinesUnavailable?: boolean;
  /** Reloads the piece after a refused action. */
  onRefresh: () => void;
}

/** True when the owner can archive the piece from its header. */
export function canArchive(facts: PieceFacts, machines: Machines | undefined, machinesUnavailable = false): boolean {
  return facts.kind === 'article'
    ? ownerCanMoveArticle(machines, facts.status, 'archived', machinesUnavailable)
    : machineAllows(machines, 'post', facts.status, 'archived', machinesUnavailable);
}

/**
 * The header's "More actions" menu. Archive, behind a question (an archived piece leaves the
 * Studio list; an article can be restored), shown only when the state machine allows it. For a
 * post that went out, two ideas from it (`useRepurpose`): a new angle as a post (`recut`) or an
 * article built from it (`flagship`). "Open it" shows the idea in the inbox (no take sheet), where
 * it offers its own start.
 */
export function PieceMenu({ facts, etag, machines, machinesUnavailable = false, onRefresh }: PieceMenuProps) {
  const [confirming, setConfirming] = useState(false);
  const isArticle = facts.kind === 'article';
  const onError = (error: unknown) => {
    setConfirming(false);
    if (isEtagMismatch(error)) {
      toastError(error, { title: 'It changed since you opened it. Check it, then archive it again.' });
      onRefresh();
    }
  };
  const archiveArticle = useApiMutation('post', '/api/articles/{article_id}/transition', {
    updates: () => queryKeys.articles.detail(facts.id),
    invalidates: [queryKeys.articles.lists(), queryKeys.today],
    successToast: 'Article archived.',
    errorToast: 'Couldn’t archive the article.',
    handles: ['etag_mismatch'],
  });
  const archivePost = useApiMutation('post', '/api/posts/{post_id}/archive', {
    updates: () => queryKeys.posts.detail(facts.id),
    invalidates: [queryKeys.posts.lists(), queryKeys.today],
    successToast: 'Post archived.',
    errorToast: 'Couldn’t archive the post.',
    handles: ['etag_mismatch'],
  });
  const repurpose = useRepurpose();
  const pending = archiveArticle.isPending || archivePost.isPending;

  const archivable = canArchive(facts, machines, machinesUnavailable) && Boolean(etag);
  const repurposable = canRepurpose(facts);
  if (!archivable && !repurposable) return null;

  const archive = () => {
    if (!etag) return;
    const done = { onSuccess: () => { setConfirming(false); }, onError };
    if (isArticle) {
      archiveArticle.mutate(
        { params: { path: { article_id: facts.id } }, body: { to: 'archived', reason: 'Archived by the owner.' }, ifMatch: etag },
        done,
      );
    } else {
      archivePost.mutate({ params: { path: { post_id: facts.id } }, ifMatch: etag }, done);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="More actions for this piece">
            <Ellipsis aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {repurposable ? (
            <>
              <DropdownMenuItem
                disabled={repurpose.pending}
                onSelect={() => {
                  repurpose.repurpose(facts.id, 'recut');
                }}
              >
                <Repeat2 aria-hidden="true" />
                Repurpose as a new post
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={repurpose.pending}
                onSelect={() => {
                  repurpose.repurpose(facts.id, 'flagship');
                }}
              >
                <FileText aria-hidden="true" />
                Turn it into an article idea
              </DropdownMenuItem>
            </>
          ) : null}
          {repurposable && archivable ? <DropdownMenuSeparator /> : null}
          {archivable ? (
            <DropdownMenuItem
              onSelect={() => {
                setConfirming(true);
              }}
            >
              <Archive aria-hidden="true" />
              Archive…
            </DropdownMenuItem>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{isArticle ? 'Archive this article?' : 'Archive this post?'}</AlertDialogTitle>
            <AlertDialogDescription>
              {isArticle
                ? 'It leaves the Studio list and nothing more happens to it. You can restore it later from Archived.'
                : 'It leaves the Studio list and won’t be posted. Its slot is freed.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <Button variant="danger" disabled={pending} onClick={archive}>
              {pending ? 'Archiving…' : 'Archive'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
