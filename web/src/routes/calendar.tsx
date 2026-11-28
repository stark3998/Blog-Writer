import { useParams, type LoaderFunctionArgs } from 'react-router';

import { sectionModules, SectionSuspense } from '@/app/lazy-sections';
import { CALENDAR_VIEWS, findSection } from '@/app/nav';
import { NotFound } from '@/components/shell/not-found';
import { Page, PageHeader } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';

type CalendarViewId = (typeof CALENDAR_VIEWS)[number]['id'];

/** Each view is its own chunk in `features/calendar/` (owned by the Calendar feature). */
const views = sectionModules<CalendarViewId>({
  plan: async () => (await import('@/features/calendar/week-view')).WeekView,
  ideas: async () => (await import('@/features/calendar/ideas-view')).IdeasView,
  availability: async () => (await import('@/features/calendar/availability-view')).AvailabilityView,
});

/** Loads the view's code before the page (or the tab switch) commits. */
export async function loader({ params }: LoaderFunctionArgs) {
  const active = findSection(CALENDAR_VIEWS, params.view);
  if (active) await views.preload(active.id);
  return null;
}

/** `/calendar/:view?`: a thin shell; the views live in `features/calendar/`. */
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
        {(section) => <SectionSuspense>{views.render(section.id, {})}</SectionSuspense>}
      </SectionTabs>
    </Page>
  );
}
