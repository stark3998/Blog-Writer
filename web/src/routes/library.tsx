import { useParams, type LoaderFunctionArgs } from 'react-router';

import { sectionModules, SectionSuspense } from '@/app/lazy-sections';
import { findSection, LIBRARY_SECTIONS } from '@/app/nav';
import { NotFound } from '@/components/shell/not-found';
import { Page, PageHeader } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';

type LibrarySectionId = (typeof LIBRARY_SECTIONS)[number]['id'];

/** Each section is its own chunk in `features/library/` (owned by the Library feature). */
const sections = sectionModules<LibrarySectionId>({
  pillars: async () => (await import('@/features/library/pillars-section')).PillarsSection,
  voice: async () => (await import('@/features/library/voice-section')).VoiceSection,
  stories: async () => (await import('@/features/library/stories-section')).StoriesSection,
  brand: async () => (await import('@/features/library/brand-section')).BrandSection,
  formats: async () => (await import('@/features/library/formats-section')).FormatsSection,
});

/** Loads the section's code before the page (or the tab switch) commits. */
export async function loader({ params }: LoaderFunctionArgs) {
  const active = findSection(LIBRARY_SECTIONS, params.section);
  if (active) await sections.preload(active.id);
  return null;
}

/** `/library/:section?`: a thin shell; the sections live in `features/library/`. */
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
        {(item) => <SectionSuspense>{sections.render(item.id, {})}</SectionSuspense>}
      </SectionTabs>
    </Page>
  );
}
