import { useNavigate } from 'react-router';
import { toast } from 'sonner';

import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';

import type { PieceFacts } from './piece-status';

/** The two ideas a post that went out can become (`POST /api/ideas/repurpose`). */
export type RepurposeKind = 'recut' | 'flagship';

const REPURPOSE_DONE: Readonly<Record<RepurposeKind, string>> = {
  recut: 'A new angle on this post is in your ideas.',
  flagship: 'An article idea from this post is in your ideas.',
};

/** True for a post that went out: it can become a new idea (`POST /api/ideas/repurpose`). */
export function canRepurpose(facts: Pick<PieceFacts, 'kind' | 'status'>): boolean {
  return facts.kind === 'post' && facts.status === 'posted';
}

/**
 * The ideas inbox showing one idea first. `?idea=` without `take`: only Today's take link carries
 * `take`, and only that link opens the take sheet. The idea offers its own start there (an
 * article for a flagship candidate, a `repurpose`-lane post for a re-cut).
 */
export function ideaHref(ideaId: string): string {
  return `/calendar/ideas?${new URLSearchParams({ idea: ideaId }).toString()}`;
}

/**
 * A post that went out becomes a new idea: a new angle as a post (`recut`) or an article built
 * from it (`flagship`). Asking twice answers the same idea. The toast's "Open it" shows the idea
 * in the inbox (`ideaHref`). Used by the piece header (a native post) and the Posts tab (an
 * article's derivative posts, which live under the article's header).
 */
export function useRepurpose(): { pending: boolean; repurpose: (postId: string, kind: RepurposeKind) => void } {
  const navigate = useNavigate();
  const mutation = useApiMutation('post', '/api/ideas/repurpose', {
    invalidates: [queryKeys.ideas.lists()],
    errorToast: 'Couldn’t add the idea.',
    onSuccess: (idea, variables) => {
      toast.success(REPURPOSE_DONE[variables.body.kind], {
        action: {
          label: 'Open it',
          onClick: () => {
            void navigate(ideaHref(idea.item.id));
          },
        },
      });
    },
  });
  return {
    pending: mutation.isPending,
    repurpose: (postId, kind) => {
      mutation.mutate({ body: { postId, kind } });
    },
  };
}
