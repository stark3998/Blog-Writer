/**
 * What other areas may use from Insights (web wave rule: import from the owner's `index.ts`).
 *
 * - `SnapshotForm`: the 30-second +48 h / +7 day form. Today opens it for a reminder's link
 *   (`/today?snapshot=<postId>`): `<SnapshotForm postId={id} onDone={close} />`.
 */
export { SnapshotForm, type SnapshotFormProps } from './snapshot-form';
