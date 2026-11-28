import { SectionStack, SettingsBlock } from '../settings/components/settings-block';
import { ExperimentsPanel } from './experiments-panel';
import { ImportsPanel } from './imports-panel';
import { RatesPanel } from './rates-panel';
import { SnapshotForm } from './snapshot-form';
import { SpendPanel } from './spend-panel';
import { SuggestionsPanel } from './suggestions-panel';

/**
 * Settings > Insights (`/settings/insights`; its own area once about 8 weeks of data exist):
 * the snapshot form (phone-first), rates per 1K impressions against the trailing median by
 * dimension, suggestions the owner decides, pre-registered experiments, the spend and fallback
 * rate, and the export import (beta). Calls `/api/analytics/*`, `POST /api/posts/{id}/snapshots`
 * (contract section 10). Self-contained, so moving it to its own area is a route change only.
 */
export function InsightsSection() {
  return (
    <SectionStack>
      <SettingsBlock
        id="snapshot"
        title="Log a snapshot"
        description="Thirty seconds, twice per post: the numbers from LinkedIn’s post analytics at 48 hours and at 7 days."
      >
        <div className="max-w-measure">
          <SnapshotForm />
        </div>
      </SettingsBlock>

      <SettingsBlock
        title="How your posts do"
        description="Every rate is per 1,000 impressions and compared with your own trailing median, never with anyone else’s numbers."
      >
        <RatesPanel />
      </SettingsBlock>

      <SettingsBlock
        title="Suggestions"
        description="What the numbers suggest trying. You decide each one; nothing changes by itself."
      >
        <SuggestionsPanel />
      </SettingsBlock>

      <SettingsBlock
        title="Experiments"
        description="Written down before they run, one at a time, so the result means something."
      >
        <ExperimentsPanel />
      </SettingsBlock>

      <SettingsBlock
        id="spend"
        title="Spend"
        description="What the AI work costs, day by day, and how often a fallback model stepped in."
      >
        <SpendPanel />
      </SettingsBlock>

      <SettingsBlock title="LinkedIn’s export" description="A bonus: import the analytics export instead of typing numbers.">
        <ImportsPanel />
      </SettingsBlock>
    </SectionStack>
  );
}
