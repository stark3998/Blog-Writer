/**
 * What other areas may use from Settings (web wave rule: import from the owner's `index.ts`).
 *
 * - `PushSetup`: turning notifications on for this device (Today can offer it once).
 * - `settingsKeys`: the Settings area's keys (the shared ones point at `@/lib/query/keys`).
 *   `GET /api/system/status` is `useSystemStatus` in `@/lib/query/system-status`, one cache entry
 *   for Today's notices and Settings.
 * - `connectionErrorSentence`: what a LinkedIn connection's `lastError` code means.
 */
export { PushSetup, type PushSetupProps } from './integrations/push-setup';
export { connectionErrorSentence } from './integrations/linkedin-panel';
export { settingsKeys } from './queries';
