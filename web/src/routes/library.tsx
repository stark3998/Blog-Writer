import { useParams } from 'react-router';

import { findSection, LIBRARY_SECTIONS } from '@/app/nav';
import { DetailList, type DetailItem } from '@/components/shell/detail-list';
import { EmptyState } from '@/components/shell/empty-state';
import { NotFound } from '@/components/shell/not-found';
import { Page, PageHeader } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';
import { PILLAR_THREAD_CLASS, PILLARS } from '@/lib/pillars';

type LibrarySectionId = (typeof LIBRARY_SECTIONS)[number]['id'];

const PILLAR_ITEMS: readonly DetailItem[] = PILLARS.map((pillar) => ({
  id: pillar.id,
  term: pillar.name,
  thread: PILLAR_THREAD_CLASS[pillar.id],
  detail: pillar.topics,
}));

/** Default article formats. Lengths in words. */
const FORMAT_ITEMS: readonly DetailItem[] = [
  {
    id: 'case-study',
    term: 'Case study',
    meta: '1,500–2,500',
    detail: 'Context, constraints, decisions and trade-offs, metrics, and what you would change. Full interview.',
  },
  {
    id: 'framework',
    term: 'Framework or checklist',
    meta: '1,200–2,000',
    detail: 'A framework you originated, with a diagram, maturity levels and a checklist table. Full interview.',
  },
  {
    id: 'explainer',
    term: 'Deep explainer',
    meta: '1,800–3,000',
    detail: 'A flow diagram, threat model, configuration or code, and pitfalls. Micro-interview.',
  },
  {
    id: 'news',
    term: 'News analysis',
    meta: '900–1,500',
    detail: 'What changed, with citations, why it matters for each role, and what to do on Monday. Micro-interview.',
  },
];

function SectionContent({ section }: { section: LibrarySectionId }) {
  switch (section) {
    case 'pillars':
      return (
        <EmptyState
          title="Three pillars."
          yourPart="Keep each pillar’s audience, vocabulary and sources current. Planning and research follow these settings."
        >
          <p>
            Each pillar will hold its audience, vocabulary, up to three hashtags, feeds and trusted research sources.
            At least 80% of each week’s posts stay on-pillar.
          </p>
          <DetailList items={PILLAR_ITEMS} className="mt-6" />
        </EmptyState>
      );
    case 'voice':
      return (
        <EmptyState
          title="No voice guide yet."
          yourPart="Provide 5 to 10 LinkedIn posts you wrote yourself, and approve each change to the guide."
        >
          <p>
            The guide is built from your portfolio writing and your own past posts. Your unassisted words carry the
            most weight. Passages that read as AI-written inform topics only and are never used as examples.
          </p>
          <p>Each week it compares drafts with what you actually posted and suggests updates.</p>
        </EmptyState>
      );
    case 'stories':
      return (
        <EmptyState
          title="No stories or positions yet."
          yourPart="Approve each entry before a draft can use it. You decide what is anonymized and what is public."
        >
          <p>
            A story records a problem, what you did, the trade-offs, rounded metrics and what you learned. A position
            is your view on a pillar topic, in your own words.
          </p>
          <p>Only anonymized or public wording is stored. Raw interview answers never are.</p>
        </EmptyState>
      );
    case 'brand':
      return (
        <EmptyState
          title="No brand kit yet."
          yourPart="Upload your colours, fonts, headshot and name mark once. Every slide template uses them."
        >
          <p>
            Carousels and image cards are built from your kit, with readable type and AA contrast on every slide.
            They never carry employer branding.
          </p>
        </EmptyState>
      );
    case 'formats':
      return (
        <EmptyState
          title="Four article formats."
          yourPart="Adjust a format when it stops working for you. Frameworks must be yours: invented ones are blocked."
        >
          <p>Each format sets the length, the parts an article must contain and how much of your input it needs.</p>
          <DetailList items={FORMAT_ITEMS} className="mt-6" />
          <p className="text-sm">Lengths are in words.</p>
        </EmptyState>
      );
  }
}

export function Component() {
  const { section } = useParams();
  const active = findSection(LIBRARY_SECTIONS, section);
  if (!active) return <NotFound />;

  return (
    <Page>
      <PageHeader
        title="Library"
        documentTitle={`${active.label} – Library`}
        standfirst="What the writing is built from: your pillars, your voice, your stories and positions, your brand and the formats."
      />
      <SectionTabs label="Library sections" sections={LIBRARY_SECTIONS} active={active} basePath="/library">
        {(item) => <SectionContent section={item.id} />}
      </SectionTabs>
    </Page>
  );
}
