import { ArrowLeft } from 'lucide-react';
import { Link, useParams } from 'react-router';

import { findSection, STUDIO_TABS } from '@/app/nav';
import { EmptyState } from '@/components/shell/empty-state';
import { NotFound } from '@/components/shell/not-found';
import { Page, PageHeader } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';

type StudioTabId = (typeof STUDIO_TABS)[number]['id'];

function TabEmptyState({ tab }: { tab: StudioTabId }) {
  switch (tab) {
    case 'brief':
      return (
        <EmptyState
          title="No brief yet."
          yourPart="Read the brief, check the sources you care about, and flag anything missing before the interview."
        >
          <p>
            Research runs in the background: it plans sub-questions, searches your pillar’s trusted sources and the
            open web, then checks dates and wants two sources for every key number.
          </p>
          <p>
            The brief that comes back has a summary, cited findings, statistics, counterpoints, open questions and
            hook ideas. The draft is written from the brief alone.
          </p>
        </EmptyState>
      );
    case 'interview':
      return (
        <EmptyState
          title="No questions yet."
          yourPart="Answer in your own words. Dictation on your phone is fine. You confirm the anonymized wording before it is saved."
        >
          <p>
            Every piece asks two questions: “What’s your take?” and “What would you tell a client on Monday?” Case
            studies and frameworks get a fuller interview of five to eight questions.
          </p>
          <p>Client names and other confidential details are removed before anything is stored.</p>
        </EmptyState>
      );
    case 'draft':
      return (
        <EmptyState
          title="No draft yet."
          yourPart="Review the draft, edit anything that doesn’t sound like you, and approve it. Auto-publish stays off until six reviewed articles pass cleanly."
        >
          <p>
            The draft is written from the brief, your stories and your interview answers, then critiqued and revised
            at most twice. Seven gates check it: quality, facts, confidentiality, originality, formatting, your
            signal and outbound links.
          </p>
        </EmptyState>
      );
    case 'posts':
      return (
        <EmptyState
          title="No posts yet."
          yourPart="Pick a hook and approve each post. Approval covers the exact text and images you saw."
        >
          <p>
            Up to two LinkedIn posts come from each article, each with three hook options and a lint check: no links,
            no bait phrases, at most three hashtags.
          </p>
          <p>Carousels use your brand kit: 8 to 12 slides at 1080 × 1350.</p>
        </EmptyState>
      );
    case 'go-live':
      return (
        <EmptyState
          title="Nothing scheduled."
          yourPart="At each slot, tap Publish, or use the ready-to-post kit to post from LinkedIn yourself."
        >
          <p>
            Every LinkedIn post needs your approval and your tap. The article goes to jatinmadan.com once its gates
            pass, and is marked published only after the live page checks out.
          </p>
        </EmptyState>
      );
  }
}

export function Component() {
  const { pieceId = '', tab } = useParams();
  const active = findSection(STUDIO_TABS, tab);
  if (!active || pieceId === '') return <NotFound />;

  return (
    <Page>
      <Link
        to="/studio"
        aria-label="Back to Studio"
        className="-ml-1 mb-6 inline-flex items-center gap-1.5 rounded-sm px-1 text-sm font-medium text-ink-muted hover:text-ink"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Studio
      </Link>
      <PageHeader
        title="Untitled piece"
        documentTitle={`${active.label} – Studio`}
        standfirst="Brief, interview, draft, posts and go-live for one piece, in the order they happen."
      />
      <SectionTabs
        label="Piece stages"
        sections={STUDIO_TABS}
        active={active}
        basePath={`/studio/${encodeURIComponent(pieceId)}`}
      >
        {(section) => <TabEmptyState tab={section.id} />}
      </SectionTabs>
    </Page>
  );
}
