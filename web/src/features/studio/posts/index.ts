/**
 * What other areas may use from Posts & go-live (import from here only): the glossary labels
 * and badges for post and kit states, the LinkedIn feed card with its fold, and the links to a
 * post's composer and go-live page.
 */
export { FeedCard, type FeedAuthor, type FeedCardProps, type FeedMedia, type FeedWidth } from './components/feed-card';
export { KitStateBadge, PostStatusBadge } from './components/status-badge';
export {
  FORMAT_LABELS,
  HOOK_TYPE_LABELS,
  KIT_STATE_LABELS,
  LANE_LABELS,
  POST_STATUS_LABELS,
  blockerSentence,
  postErrorSentence,
} from './model/labels';
export { foldIndex, MAX_PAYLOAD_UNITS, MOBILE_FOLD } from './model/text';
export { goLivePath, postsPath } from './model/types';
