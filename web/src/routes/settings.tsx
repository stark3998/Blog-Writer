import { useParams, type LoaderFunctionArgs } from 'react-router';

import { sectionModules, SectionSuspense } from '@/app/lazy-sections';
import { findSection, SETTINGS_SECTIONS } from '@/app/nav';
import { NotFound } from '@/components/shell/not-found';
import { Page, PageHeader } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';

type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]['id'];

/**
 * Each section is its own chunk in `features/settings/`; Insights lives in `features/insights/`
 * because it becomes its own area once there are about eight weeks of data.
 */
const sections = sectionModules<SettingsSectionId>({
  models: async () => (await import('@/features/settings/models-section')).ModelsSection,
  slots: async () => (await import('@/features/settings/slots-section')).SlotsSection,
  sources: async () => (await import('@/features/settings/sources-section')).SourcesSection,
  integrations: async () => (await import('@/features/settings/integrations-section')).IntegrationsSection,
  policy: async () => (await import('@/features/settings/policy-section')).PolicySection,
  insights: async () => (await import('@/features/insights/insights-section')).InsightsSection,
  system: async () => (await import('@/features/settings/system-section')).SystemSection,
});

/** Loads the section's code before the page (or the tab switch) commits. */
export async function loader({ params }: LoaderFunctionArgs) {
  const active = findSection(SETTINGS_SECTIONS, params.section);
  if (active) await sections.preload(active.id);
  return null;
}

/** `/settings/:section?`: a thin shell; the sections live in `features/settings/` and `features/insights/`. */
export function Component() {
  const { section } = useParams();
  const active = findSection(SETTINGS_SECTIONS, section);
  if (!active) return <NotFound />;

  return (
    <Page>
      <PageHeader
        title="Settings"
        documentTitle={`${active.label} – Settings`}
        standfirst="How the engine runs: models, slots, sources, integrations and policy."
      />
      <SectionTabs label="Settings sections" sections={SETTINGS_SECTIONS} active={active} basePath="/settings">
        {(item) => <SectionSuspense>{sections.render(item.id, {})}</SectionSuspense>}
      </SectionTabs>
    </Page>
  );
}
