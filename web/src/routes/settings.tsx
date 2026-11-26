import { useSyncExternalStore } from 'react';
import { useParams } from 'react-router';

import { findSection, SETTINGS_SECTIONS } from '@/app/nav';
import { DetailList, type DetailItem } from '@/components/shell/detail-list';
import { EmptyState } from '@/components/shell/empty-state';
import { NotFound } from '@/components/shell/not-found';
import { Page, PageHeader } from '@/components/shell/page';
import { SectionTabs } from '@/components/shell/section-tabs';
import { Badge } from '@/components/ui/badge';
import { STANDALONE_QUERY, useMediaQuery } from '@/lib/media';

type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]['id'];

const SLOT_ITEMS: readonly DetailItem[] = [
  { id: 'main', term: 'Tuesday to Thursday', meta: '10:00–12:00', detail: 'The default post slots.' },
  { id: 'monday', term: 'Monday', meta: '12:00', detail: 'Optional. Used only in weeks with more than three posts.' },
  { id: 'friday', term: 'Friday', meta: '15:00', detail: 'Optional. Used only in weeks with more than three posts.' },
];

function subscribeToServiceWorker(onChange: () => void): () => void {
  if (!('serviceWorker' in navigator)) return () => undefined;
  navigator.serviceWorker.addEventListener('controllerchange', onChange);
  return () => {
    navigator.serviceWorker.removeEventListener('controllerchange', onChange);
  };
}

function serviceWorkerActive(): boolean {
  return 'serviceWorker' in navigator && navigator.serviceWorker.controller !== null;
}

/** Facts about this browser, useful when checking the phone setup. */
function ThisDevice() {
  const installed = useMediaQuery(STANDALONE_QUERY);
  const workerActive = useSyncExternalStore(subscribeToServiceWorker, serviceWorkerActive, () => false);
  const items: readonly DetailItem[] = [
    {
      id: 'build',
      term: 'App version',
      detail: `${__APP_VERSION__} (build ${__BUILD_SHA__})`,
    },
    {
      id: 'installed',
      term: 'Installed as an app',
      detail: installed ? 'Yes' : 'No. Add it to your home screen to get notifications on your phone.',
    },
    {
      id: 'service-worker',
      term: 'Service worker',
      detail: workerActive ? 'Active' : 'Not active',
    },
  ];
  return (
    <section aria-labelledby="this-device" className="max-w-measure">
      <h2 id="this-device" className="font-serif text-2xl font-medium text-ink">
        This device
      </h2>
      <DetailList items={items} className="mt-6" />
    </section>
  );
}

function SectionContent({ section }: { section: SettingsSectionId }) {
  switch (section) {
    case 'models':
      return (
        <EmptyState
          title="Model routing is not connected yet."
          yourPart="Check which model handled each step and what it cost. Nothing switches models without showing it here."
        >
          <p>
            Each task has a primary model and a fallback. A draft written by a fallback model, or after any refusal,
            is always held for your review.
          </p>
          <p>Spend is tracked as it happens. A daily brake pauses background work if spending runs away.</p>
        </EmptyState>
      );
    case 'slots':
      return (
        <EmptyState
          title="Post slots."
          yourPart="Adjust the slots when your week changes. The planner keeps the spacing rules either way."
        >
          <p>The defaults, in New York time. Two posts never land on the same day.</p>
          <DetailList items={SLOT_ITEMS} className="mt-6" />
        </EmptyState>
      );
    case 'sources':
      return (
        <EmptyState title="No sources yet." yourPart="Add the feeds worth watching for each pillar.">
          <p>
            RSS feeds and weekly scouting for each pillar. New items are sorted into the Ideas list. Feed items never
            generate content without your action.
          </p>
        </EmptyState>
      );
    case 'integrations':
      return (
        <EmptyState
          title="Nothing connected yet."
          yourPart="Connect LinkedIn once, and reconnect when the app reminds you before the token expires."
        >
          <p>
            LinkedIn connects for one-tap publishing at a slot. The connection lasts about 60 days, and reminders start
            ten days before it ends.
          </p>
          <p>The portfolio connection publishes articles to jatinmadan.com.</p>
        </EmptyState>
      );
    case 'policy':
      return (
        <div className="space-y-10">
          <EmptyState
            title="No blocklist yet."
            yourPart="Provide the client and internal project names to block, and your employer’s social media policy."
          >
            <p>
              Names on the blocklist are checked everywhere: interview answers, research queries, drafts and posts. The
              employer policy adds disclaimer text and forbidden topics.
            </p>
          </EmptyState>
          <div className="max-w-measure">
            <p className="flex flex-wrap items-center gap-x-3 gap-y-2 font-semibold text-ink">
              Article auto-publish
              <Badge variant="neutral">Off</Badge>
            </p>
            <p className="mt-2 text-ink-muted">
              It switches on only after six reviewed articles in a row pass every gate without substantive edits, and
              turns itself off after any correction. LinkedIn posts always need your tap.
            </p>
          </div>
        </div>
      );
    case 'insights':
      return (
        <EmptyState
          title="No insights yet."
          yourPart="Log a 30-second snapshot at 48 hours and at 7 days, and approve or dismiss each suggestion."
        >
          <p>
            Rates per 1,000 impressions are compared with your own trailing median, by pillar, format, hook, lane and
            go-live mode. Insights only suggest changes. The planner never adjusts itself.
          </p>
          <p>Insights moves to its own area once there are about eight weeks of data.</p>
        </EmptyState>
      );
    case 'system':
      return (
        <div className="space-y-14">
          <EmptyState
            title="No jobs have run yet."
            yourPart="Label the evaluation set when asked, and look here first when an alert arrives."
          >
            <p>
              Background jobs and their history, the evaluation set you label, and a full data export of everything
              the app stores.
            </p>
          </EmptyState>
          <ThisDevice />
        </div>
      );
  }
}

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
        {(item) => <SectionContent section={item.id} />}
      </SectionTabs>
    </Page>
  );
}
