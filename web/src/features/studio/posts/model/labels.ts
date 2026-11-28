import type { CheckName, HookType, KitState, Lane, PostFormat, PostStatus } from './types';

/**
 * The words the web shows for post states and settings. Status labels follow the contract's
 * glossary (section 10): Writing, Draft, Approved, Ready, Publishing, Posted, Kit, Needs you,
 * Missed, Expired, Deleted, Archived; kit: Prepared, Draft saved in LinkedIn, Scheduled in
 * LinkedIn, Posted.
 */

export const POST_STATUS_LABELS: Readonly<Record<PostStatus, string>> = {
  generating: 'Writing',
  draft: 'Draft',
  approved: 'Approved',
  staged: 'Ready',
  publishing: 'Publishing',
  posted: 'Posted',
  kit: 'Kit',
  needs_attention: 'Needs you',
  missed: 'Missed',
  expired: 'Expired',
  deleted: 'Deleted',
  archived: 'Archived',
};

export const KIT_STATE_LABELS: Readonly<Record<KitState, string>> = {
  prepared: 'Prepared',
  native_draft_saved: 'Draft saved in LinkedIn',
  natively_scheduled: 'Scheduled in LinkedIn',
  posted: 'Posted',
};

/** The kit's states in order (`post.kit.state`, contract section 2). */
export const KIT_STATES: readonly KitState[] = ['prepared', 'native_draft_saved', 'natively_scheduled', 'posted'];

/** The hook taxonomy (plan: "LinkedIn Studio / Hooks"). */
export const HOOK_TYPE_LABELS: Readonly<Record<HookType, string>> = {
  result_metric: 'Result or metric',
  mistake_lesson: 'Mistake and lesson',
  decision_tradeoff: 'Decision and trade-off',
  counter_evidence: 'Cited counter-evidence',
  checklist_promise: 'Checklist promise',
  story_in_motion: 'Story in motion',
  question: 'Genuine question',
};

export const LANE_LABELS: Readonly<Record<Lane, string>> = {
  derivative: 'From the article',
  native: 'Native',
  quick_take: 'Quick take',
  repurpose: 'Repurposed',
};

export const FORMAT_LABELS: Readonly<Record<PostFormat, string>> = {
  text: 'Text',
  carousel: 'Carousel',
  image_card: 'Image card',
  poll: 'Poll',
};

export const CHECK_LABELS: Readonly<Record<CheckName, string>> = {
  lint: 'Lint',
  derived_claims: 'Claims match the sources',
  near_duplicate: 'Not a near-duplicate',
};

/** What each check looks at, for the checks panel. */
export const CHECK_DESCRIPTIONS: Readonly<Record<CheckName, string>> = {
  lint: 'Length, links, hashtags, bait phrases and AI tells, on the text and every slide.',
  derived_claims: 'Every number and claim matches the article, the brief or a story exactly.',
  near_duplicate: 'Not too close to anything you posted in the last 90 days.',
};

/** Review-only reasons (`domain.common.review_only_reasons`). */
export function reviewOnlyReason(code: string): string {
  switch (code) {
    case 'fallback_model':
      return 'A fallback model wrote part of it.';
    case 'refusal':
      return 'A model refused part of the request, so something may be missing.';
    default:
      return 'The writer flagged it for a closer read.';
  }
}

/**
 * Why the go-live tap would be refused now (`GoLiveView.reasons`, `routes.publish_blockers`).
 * Each sentence says what to do about it.
 */
export function blockerSentence(code: string): string {
  switch (code) {
    case 'not_approved':
      return 'The post isn’t approved. Approve it in Posts & carousel first.';
    case 'approval_stale':
      return 'The post changed after it was approved. Review it and approve it again.';
    case 'post_expired':
      return 'This time-sensitive post has expired.';
    case 'media_missing':
      return 'The slides or image aren’t rendered yet. Render them in Posts & carousel.';
    case 'media_not_uploaded':
      return 'The media hasn’t reached LinkedIn yet. Publishing uploads it first.';
    case 'too_many_images':
      return 'LinkedIn takes at most 20 images in one post.';
    case 'document_title_missing':
      return 'The carousel needs a document title.';
    case 'poll_invalid':
      return 'LinkedIn can’t take this poll as it is.';
    case 'vault_unavailable':
      return 'The server can’t read the LinkedIn sign-in (the token vault isn’t set up). Use the kit.';
    case 'reconnect_required':
      return 'LinkedIn needs you to sign in again. Reconnect in Settings > Integrations, or use the kit.';
    default:
      return 'Something stops this post from going to LinkedIn right now.';
  }
}

/**
 * What a `lastError` (or a publish intent's or staging's error code) means. Codes come from
 * the publisher (`linkedin_<status>`, `timeout`, `network_error`, `urn_missing`, …), staging
 * (`reconnect_required`, `media_missing`, …) and the writer.
 */
export function postErrorSentence(code: string): string {
  const status = /^linkedin_(\d{3})$/.exec(code)?.[1];
  if (status !== undefined) {
    switch (status) {
      case '401':
        return 'LinkedIn signed you out, so it refused the post. Reconnect in Settings > Integrations, or use the kit.';
      case '403':
        return 'LinkedIn refused the post: the app isn’t allowed to post for you.';
      case '422':
      case '400':
        return 'LinkedIn refused the post’s content. Check the text and media, then tap Publish again.';
      case '429':
        return 'LinkedIn is limiting posts right now. Wait a few minutes before you tap Publish again.';
      default:
        return status.startsWith('5')
          ? `LinkedIn had a problem (HTTP ${status}). It may or may not have posted.`
          : `LinkedIn refused the post (HTTP ${status}).`;
    }
  }
  switch (code) {
    case 'timeout':
      return 'LinkedIn didn’t answer within 30 seconds, so the app can’t tell whether the post went out.';
    case 'network_error':
    case 'client_error':
      return 'The connection to LinkedIn broke during the attempt, so the app can’t tell whether the post went out.';
    case 'urn_missing':
      return 'LinkedIn answered without the post’s id, so the app can’t tell whether the post went out.';
    case 'publish_unknown':
      return 'The publish attempt ended without an answer from LinkedIn.';
    case 'publish_rejected':
      return 'LinkedIn refused the post.';
    case 'reconnect_required':
    case 'unauthorized':
      return 'LinkedIn needs you to sign in again. Reconnect in Settings > Integrations, or use the kit.';
    case 'linkedin_unavailable':
      return 'LinkedIn isn’t set up on the server, so the post couldn’t be prepared. Use the kit.';
    case 'media_missing':
      return 'The slides or image weren’t rendered in time. Render them in Posts & carousel.';
    case 'storage_unavailable':
      return 'File storage isn’t reachable, so the media couldn’t be prepared.';
    case 'render_unavailable':
      return 'Rendering isn’t available on the server, so the slides couldn’t be prepared.';
    case 'upload_failed':
    case 'media_upload_failed':
      return 'The media didn’t reach LinkedIn. Try again in a minute, or use the kit.';
    case 'blocklist_hit':
      return 'The text or slides match the blocklist. Rephrase them in Posts & carousel.';
    case 'blocklist_not_configured':
      return 'Save the blocklist once in Settings > Blocklist & policy first.';
    case 'ai_use_not_confirmed':
      return 'Confirm your employer’s AI-use policy in Settings > Blocklist & policy first.';
    case 'ai_unavailable':
      return 'The AI models aren’t set up, so the post couldn’t be written.';
    case 'no_hooks':
      return 'The writer produced no usable hook. Rewrite it or write the hook yourself.';
    default:
      return 'Something went wrong with this post.';
  }
}

/** One plain line for a post's slide count against the plan's 8-12 rule. */
export function slideCountNote(count: number): string {
  if (count < 8) return `${count} ${count === 1 ? 'slide' : 'slides'}: carousels use 8 to 12.`;
  if (count > 12) return `${count} slides: carousels use 8 to 12.`;
  return `${count} slides.`;
}
