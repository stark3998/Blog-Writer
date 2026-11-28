import { useParams, type LoaderFunctionArgs } from 'react-router';

import { sectionModules, SectionSuspense } from '@/app/lazy-sections';
import { findSection, pieceKind, studioTabsFor, type StudioTab } from '@/app/nav';
import { NotFound } from '@/components/shell/not-found';
import { Page } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';
import { PieceHeader } from '@/features/studio/piece/piece-header';
import type { PieceTabProps } from '@/features/studio/piece/piece-tab';

/** Each tab is its own chunk in `features/studio/<tab>/` and receives the piece id. */
const tabs = sectionModules<StudioTab['id'], PieceTabProps>({
  brief: async () => (await import('@/features/studio/brief/brief-tab')).BriefTab,
  interview: async () => (await import('@/features/studio/interview/interview-tab')).InterviewTab,
  draft: async () => (await import('@/features/studio/draft/draft-tab')).DraftTab,
  posts: async () => (await import('@/features/studio/posts/posts-tab')).PostsTab,
  'go-live': async () => (await import('@/features/studio/go-live/go-live-tab')).GoLiveTab,
});

/** Loads the tab's code before the page (or the tab switch) commits. */
export async function loader({ params }: LoaderFunctionArgs) {
  const pieceId = params.pieceId ?? '';
  const active = findSection(studioTabsFor(pieceId), params.tab);
  if (active && pieceId !== '') await tabs.preload(active.id);
  return null;
}

/**
 * `/studio/:pieceId/:tab?`: a thin shell. A piece is an article (`art-…`, five tabs) or a
 * standalone post (`post-…`, no Draft tab). The header and tabs live in `features/studio/`.
 */
export function Component() {
  const { pieceId = '', tab } = useParams();
  const sections = studioTabsFor(pieceId);
  const active = findSection(sections, tab);
  if (!active || pieceId === '') return <NotFound />;

  return (
    <Page>
      <PieceHeader pieceId={pieceId} kind={pieceKind(pieceId)} tabLabel={active.label} />
      <SectionTabs
        label="Piece stages"
        sections={sections}
        active={active}
        basePath={`/studio/${encodeURIComponent(pieceId)}`}
      >
        {(section) => <SectionSuspense>{tabs.render(section.id, { pieceId })}</SectionSuspense>}
      </SectionTabs>
    </Page>
  );
}
