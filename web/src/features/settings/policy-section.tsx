import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiQuery } from '@/lib/query/api-query';

import { BlockLoading, SectionStack, SettingsBlock } from './components/settings-block';
import { AutoPublishPanel, PausePanel } from './policy/autonomy-panel';
import { BlocklistEditor } from './policy/blocklist-editor';
import { EmployerPolicyForm } from './policy/employer-policy-form';
import { OwnerInputsPanel } from './policy/owner-inputs';
import { settingsKeys } from './queries';

function BlocklistBlock() {
  const blocklist = useApiQuery({ queryKey: settingsKeys.blocklist, path: '/api/kb/blocklist' });
  if (blocklist.isPending) return <BlockLoading label="Loading the blocklist" />;
  if (blocklist.isError) {
    return (
      <ProblemAlert
        error={blocklist.error}
        title="Couldn’t load the blocklist."
        onRetry={() => void blocklist.refetch()}
        retryLabel="Try loading the blocklist again"
      />
    );
  }
  return (
    <BlocklistEditor
      source={blocklist.data}
      latest={blocklist.data}
      onReload={() => blocklist.refetch().then((result) => result.data)}
    />
  );
}

function PolicyBlock() {
  const policy = useApiQuery({ queryKey: settingsKeys.policy, path: '/api/kb/policy' });
  if (policy.isPending) return <BlockLoading label="Loading the employer policy" />;
  if (policy.isError) {
    return (
      <ProblemAlert
        error={policy.error}
        title="Couldn’t load the employer policy."
        onRetry={() => void policy.refetch()}
        retryLabel="Try loading the employer policy again"
      />
    );
  }
  return (
    <EmployerPolicyForm
      source={policy.data}
      latest={policy.data}
      onReload={() => policy.refetch().then((result) => result.data)}
    />
  );
}

/**
 * Settings > Blocklist & policy (`/settings/policy`): the pause switch (phone-first), what the app
 * still needs from the owner, the blocklist, the employer policy (disclaimers, forbidden topics,
 * AI-use confirmation) and article auto-publish (switch, calibration streak, judge rate).
 * Calls `/api/kb/blocklist`, `/api/kb/policy`, `/api/kb/autonomy` (+ `/pause`, `/resume`),
 * `/api/kb/inputs` (contract section 10).
 */
export function PolicySection() {
  return (
    <SectionStack>
      <SettingsBlock
        title="Pause"
        description="One switch for the days you need the app to stand still."
      >
        <PausePanel />
      </SettingsBlock>

      <SettingsBlock
        title="What the app still needs from you"
        description="The inputs only you can give. Until each is in place, the parts that depend on it wait or fail safe."
      >
        <OwnerInputsPanel />
      </SettingsBlock>

      <SettingsBlock
        id="blocklist"
        title="Blocklist"
        description="Client and internal project names. They are checked everywhere: interview answers, research queries, drafts and posts. A match blocks the step and points at the term without repeating it."
      >
        <BlocklistBlock />
      </SettingsBlock>

      <SettingsBlock
        id="employer-policy"
        title="Employer policy"
        description="What your employer’s social media and AI-use policy asks for: disclaimers, topics to avoid, and the confirmation that lets anonymized material reach the models."
      >
        <PolicyBlock />
      </SettingsBlock>

      <SettingsBlock
        title="Auto-publish"
        description="Articles may publish on their own only after a clean run of reviews you approved unchanged. LinkedIn posts always need your tap."
      >
        <AutoPublishPanel />
      </SettingsBlock>
    </SectionStack>
  );
}
