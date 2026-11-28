import { ProblemAlert } from '@/features/data/problem-alert';

import { BlockLoading, SectionStack, SettingsBlock } from './components/settings-block';
import { LinkedInPanel } from './integrations/linkedin-panel';
import { ProfileChecklistPanel } from './integrations/profile-checklist';
import { PushSetup } from './integrations/push-setup';
import { ServicesPanel } from './integrations/services-panel';
import { useSystemStatus } from './queries';

/**
 * Settings > Integrations (`/settings/integrations`): the LinkedIn connection (connect, token
 * day, banner, introspect, disconnect), push on this device, portfolio and Web IQ status, the
 * profile alignment checklist. Connect and push setup are phone-first.
 * Calls `/api/linkedin/*`, `/api/push/key`, `/api/push/subscriptions`, `/api/system/status`,
 * `/api/jobs?type=publish.article`, `/api/engagement/profile-checklist` (contract section 10). The
 * OAuth callback always lands back here (303 to `/settings/integrations`); the outcome is read from
 * the connection's `lastError`.
 */
export function IntegrationsSection() {
  const status = useSystemStatus();

  return (
    <SectionStack>
      <SettingsBlock
        id="linkedin"
        title="LinkedIn"
        description="One-tap publishing at a slot uses this connection. LinkedIn’s tokens last 60 days; the app reminds you from day 50."
      >
        <LinkedInPanel />
      </SettingsBlock>

      <SettingsBlock
        id="push"
        title="Notifications on this device"
        description="Post slots, reminders and veto notices arrive as notifications with a title and a link, nothing else."
      >
        <PushSetup service={status.data?.services.push} />
      </SettingsBlock>

      <SettingsBlock
        title="Publishing and research services"
        description="What the portfolio build and the research engine depend on. These are set up on the server."
      >
        {status.isPending ? (
          <BlockLoading label="Loading the services" />
        ) : status.isError ? (
          <ProblemAlert
            error={status.error}
            title="Couldn’t load the services."
            onRetry={() => void status.refetch()}
            retryLabel="Try loading the services again"
          />
        ) : (
          <ServicesPanel status={status.data} />
        )}
      </SettingsBlock>

      <SettingsBlock
        title="Profile alignment"
        description="A one-off pass over your LinkedIn profile so it matches the three pillars. Tick each part as you finish it on LinkedIn."
      >
        <ProfileChecklistPanel />
      </SettingsBlock>
    </SectionStack>
  );
}
