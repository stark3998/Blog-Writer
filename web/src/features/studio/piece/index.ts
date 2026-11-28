/**
 * The Studio's shared piece layer (owner: Studio core). Other Studio tabs import from here only.
 *
 * - Queries on the contract keys: `useArticle(id)` / `articleQuery(id)`, `usePost(id)` /
 *   `postQuery(id)`, `usePillars()`, `useFormatPacks()`, `useStartResearch(id)`. The state
 *   machines are `useMachineViews()` from `@/lib/query/machines` (one entry for every area).
 * - Status words and rules: `ARTICLE_STATUS`, `POST_STATUS` (glossary labels, tones, icons),
 *   `articleWriter(machines, status)` (the server's who-writes rule: the editor is read-only
 *   unless it is `owner`), `machineAllows`, `ownerCanMoveArticle` (the server's owner edges),
 *   `nextActionFor`, `lastErrorSentence`.
 * - Chips: `StatusBadge`, `PillarBadge`, `ReviewOnlyBadge`, `ServedByBadge`, `FactChip`.
 * - Formatting: `formatDateTime`, `formatDate`, `formatPartialDate`, `formatSlot`, `formatCost`.
 * - Repurposing a post that went out: `useRepurpose()` (`POST /api/ideas/repurpose`, the toast's
 *   "Open it" shows the idea in the inbox), `canRepurpose(facts)`.
 */
export { formatCost, formatDate, formatDateTime, formatPartialDate, formatSlot } from './format';
export { FactChip, PillarBadge, ReviewOnlyBadge, ServedByBadge, StatusBadge } from './piece-chips';
export { articleTitle, postTitle } from './piece-header';
export {
  articleQuery,
  postQuery,
  START_RESEARCH_PROBLEMS,
  useArticle,
  useFormatPacks,
  usePillars,
  usePost,
  useStartResearch,
} from './piece-queries';
export {
  ARTICLE_STATUS,
  articleWriter,
  firstLine,
  isWorkingStatus,
  LANE_LABEL,
  lastErrorSentence,
  machineAllows,
  nextActionFor,
  ownerCanMoveArticle,
  piecePath,
  POST_FORMAT_LABEL,
  POST_STATUS,
  type ArticleWriter,
  type NextAction,
  type PieceFacts,
  type StatusMeta,
} from './piece-status';
export { formatName, pillarView, type PillarView } from './pillars';
export { canRepurpose, ideaHref, useRepurpose, type RepurposeKind } from './repurpose';
export type { PieceTabProps } from './piece-tab';
export type * from './types';
