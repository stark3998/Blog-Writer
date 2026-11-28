import { useSyncExternalStore } from 'react';

import { DetailList, type DetailItem } from '@/components/shell/detail-list';
import { STANDALONE_QUERY, useMediaQuery } from '@/lib/media';

import { SchedulesPanel } from './components/schedules-panel';
import { SectionStack, SettingsBlock, SubHeading } from './components/settings-block';
import { PushSetup } from './integrations/push-setup';
import { useSystemStatus } from './queries';
import { AlertsPanel } from './system/alerts-panel';
import { BackupsPanel } from './system/backups-panel';
import { EvalsPanel } from './system/evals-panel';
import { JobsPanel } from './system/jobs-panel';
import { StatusPanel } from './system/status-panel';

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
  const status = useSystemStatus();
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
    <div className="grid gap-8">
      <DetailList items={items} className="max-w-measure" />
      <div className="grid gap-3">
        <SubHeading>Notifications</SubHeading>
        <PushSetup service={status.data?.services.push} compact />
      </div>
    </div>
  );
}

/**
 * Settings > System (`/settings/system`): status and safe mode, jobs (timeline, cancel, retry),
 * schedules (confirm, pause, resume), the test alert, the evaluation set (topics, runs, labels),
 * backups and the data export, and this device. Calls `/api/system/*`, `/api/jobs*`,
 * `/api/schedules*`, `/api/backups*`, `/api/evals/*`, `/api/push/*` (contract section 10).
 */
export function SystemSection() {
  return (
    <SectionStack>
      <SettingsBlock
        title="Status"
        description="Look here first when an alert arrives: what runs, what is stopped, and what each service reports."
      >
        <StatusPanel />
      </SettingsBlock>

      <SettingsBlock
        title="Jobs"
        description="Background work and its steps. A job that failed three times stops and waits for a look before it runs again."
      >
        <JobsPanel />
      </SettingsBlock>

      <SettingsBlock
        title="Schedules"
        description="What runs on its own. After downtime, a schedule past its lateness waits for you instead of running late."
      >
        <SchedulesPanel />
      </SettingsBlock>

      <SettingsBlock
        id="evals"
        title="Evaluation set"
        description="Twelve topics, drafted the way real articles are and scored by the judge. Your labels decide whether the judge can be trusted with auto-publish."
      >
        <EvalsPanel />
      </SettingsBlock>

      <SettingsBlock
        title="Backups and your data"
        description="A daily copy of everything the app stores, kept for 35 days. Download it any time."
      >
        <BackupsPanel />
      </SettingsBlock>

      <SettingsBlock
        title="Alerts"
        description="Alerts reach you by e-mail even when the app is down. Send one to check the path works."
      >
        <AlertsPanel />
      </SettingsBlock>

      <SettingsBlock title="This device">
        <ThisDevice />
      </SettingsBlock>
    </SectionStack>
  );
}
