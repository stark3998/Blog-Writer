import { useParams } from 'react-router';

import { CALENDAR_VIEWS, findSection } from '@/app/nav';
import { DetailList, Timeline, type DetailItem, type TimelineStep } from '@/components/shell/detail-list';
import { EmptyState } from '@/components/shell/empty-state';
import { NotFound } from '@/components/shell/not-found';
import { Page, PageHeader } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';

/** The default production calendar. A real sequence, so it is an ordered list. */
const RHYTHM: readonly TimelineStep[] = [
  { when: 'Sunday 18:00', what: 'The planner proposes next week, and you approve it.' },
  { when: 'Monday and Tuesday', what: 'Research runs in the background.' },
  { when: 'Wednesday', what: 'Your interview answers are due.' },
  { when: 'Thursday', what: 'The draft is written, critiqued and revised.' },
  { when: 'Friday 12:00', what: 'Veto notice for the article, at least 48 hours before it goes out.' },
  { when: 'Next Monday 09:00', what: 'The article publishes.' },
  { when: 'Next Tuesday to Thursday', what: 'Post slots between 10:00 and 12:00, one tap each.' },
];

const MODES: readonly DetailItem[] = [
  { id: 'normal', term: 'Normal', detail: 'Three posts and the flagship article. The default.' },
  { id: 'light', term: 'Light', detail: 'Two posts. The flagship article is optional.' },
  {
    id: 'away',
    term: 'Away',
    detail:
      'Nothing new is generated. Posts you already approved are scheduled natively through the ready-to-post kit.',
  },
];

function PlanView() {
  return (
    <div className="space-y-14">
      <EmptyState
        title="No plan for next week yet."
        yourPart="Approve the plan on Sunday, or move pieces to other slots first. About 30 minutes, including the micro-interviews."
      >
        <p>
          Each Sunday the planner proposes the week: one flagship article and three LinkedIn posts, balanced across
          your three pillars. Two posts never land on the same day.
        </p>
      </EmptyState>

      <section aria-labelledby="how-a-week-runs" className="max-w-measure">
        <h2 id="how-a-week-runs" className="font-serif text-2xl font-medium text-ink">
          How a week runs
        </h2>
        <p className="mt-2 text-ink-muted">All times are New York time.</p>
        <Timeline steps={RHYTHM} className="mt-6" />
      </section>
    </div>
  );
}

function IdeasView() {
  return (
    <EmptyState
      title="No ideas yet."
      yourPart="Pick the ideas worth a slot, and add your take with one tap. Your take is what makes a quick post yours."
    >
      <p>
        Feeds and weekly scouting land here, sorted by pillar with notes on relevance, novelty, format and angle.
        Nothing turns into content until you pick it.
      </p>
      <p>Time-sensitive news goes to a quick-take lane that publishes within 72 hours.</p>
    </EmptyState>
  );
}

function AvailabilityView() {
  return (
    <EmptyState
      title="Set how much you can give each week."
      yourPart="Switch the mode before Sunday’s plan whenever your week changes."
    >
      <p>Three modes. The planner follows the one you choose for each week.</p>
      <DetailList items={MODES} className="mt-6" />
    </EmptyState>
  );
}

export function Component() {
  const { view } = useParams();
  const active = findSection(CALENDAR_VIEWS, view);
  if (!active) return <NotFound />;

  return (
    <Page>
      <PageHeader
        title="Calendar"
        documentTitle={`${active.label} – Calendar`}
        standfirst="The plan for each week, the ideas waiting for a slot, and how much time you have."
      />
      <SectionTabs label="Calendar views" sections={CALENDAR_VIEWS} active={active} basePath="/calendar">
        {(section) => {
          switch (section.id) {
            case 'plan':
              return <PlanView />;
            case 'ideas':
              return <IdeasView />;
            case 'availability':
              return <AvailabilityView />;
          }
        }}
      </SectionTabs>
    </Page>
  );
}
