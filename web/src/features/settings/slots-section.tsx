import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiQuery } from '@/lib/query/api-query';

import { SchedulesPanel } from './components/schedules-panel';
import { BlockLoading, SectionStack, SettingsBlock } from './components/settings-block';
import { settingsKeys } from './queries';
import { CadenceForm } from './slots/cadence-form';

/**
 * Settings > Slots (`/settings/slots`): the time zone, the post slots and posts per mode, the
 * weekly calendar anchors, spacing, grace and staging, reminder offsets, crawl, scouting, digest
 * and backup times (one `CadenceSettings` document), and the schedule items that follow from it.
 * Calls `GET/PUT /api/kb/cadence`, `GET /api/schedules`, `POST /api/schedules/{id}/confirm`,
 * `/pause`, `/resume` (contract section 10). Saving the cadence reseeds the schedules.
 */
export function SlotsSection() {
  const cadence = useApiQuery({ queryKey: settingsKeys.cadence, path: '/api/kb/cadence' });

  return (
    <SectionStack>
      {cadence.isPending ? (
        <BlockLoading label="Loading the cadence" lines={6} />
      ) : cadence.isError ? (
        <ProblemAlert
          error={cadence.error}
          title="Couldn’t load the cadence."
          onRetry={() => void cadence.refetch()}
          retryLabel="Try loading the cadence again"
        />
      ) : (
        <CadenceForm
          source={cadence.data}
          latest={cadence.data}
          onReload={() => cadence.refetch().then((result) => result.data)}
        />
      )}
      <SettingsBlock
        title="Schedules"
        description="What runs on its own, and when. After downtime, a schedule past its lateness waits for you instead of running late."
      >
        <SchedulesPanel />
      </SettingsBlock>
    </SectionStack>
  );
}
