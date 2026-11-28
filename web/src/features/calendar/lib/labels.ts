import type { components } from '@/lib/api/schema';

/**
 * Words the calendar shows for the server's values. Statuses follow the contract's glossary
 * (section 10): articles "Planned … Live … Needs you", posts "Writing … Ready … Needs you".
 */

type Schemas = components['schemas'];
type Tone = 'neutral' | 'success' | 'warn' | 'danger';

export interface StatusLabel {
  readonly label: string;
  readonly tone: Tone;
}

export const POST_STATUS: Record<Schemas['PostStatus'], StatusLabel> = {
  generating: { label: 'Writing', tone: 'neutral' },
  draft: { label: 'Draft', tone: 'neutral' },
  approved: { label: 'Approved', tone: 'success' },
  staged: { label: 'Ready', tone: 'success' },
  publishing: { label: 'Publishing', tone: 'neutral' },
  posted: { label: 'Posted', tone: 'success' },
  kit: { label: 'Kit', tone: 'neutral' },
  needs_attention: { label: 'Needs you', tone: 'danger' },
  missed: { label: 'Missed', tone: 'warn' },
  expired: { label: 'Expired', tone: 'neutral' },
  deleted: { label: 'Deleted', tone: 'neutral' },
  archived: { label: 'Archived', tone: 'neutral' },
};

export const ARTICLE_STATUS: Record<Schemas['ArticleStatus'], StatusLabel> = {
  planned: { label: 'Planned', tone: 'neutral' },
  researching: { label: 'Researching', tone: 'neutral' },
  interviewing: { label: 'Interview', tone: 'warn' },
  drafting: { label: 'Drafting', tone: 'neutral' },
  gating: { label: 'Checking', tone: 'neutral' },
  in_review: { label: 'In review', tone: 'warn' },
  approved: { label: 'Approved', tone: 'success' },
  publishing: { label: 'Publishing', tone: 'neutral' },
  published: { label: 'Live', tone: 'success' },
  publish_failed: { label: 'Publish failed', tone: 'danger' },
  unpublishing: { label: 'Unpublishing', tone: 'neutral' },
  unpublished: { label: 'Unpublished', tone: 'neutral' },
  needs_attention: { label: 'Needs you', tone: 'danger' },
  archived: { label: 'Archived', tone: 'neutral' },
};

export const PLAN_STATUS: Record<Schemas['PlanStatus'], StatusLabel> = {
  generating: { label: 'Being planned', tone: 'neutral' },
  proposed: { label: 'Waiting for your approval', tone: 'warn' },
  approved: { label: 'Approved', tone: 'success' },
  closed: { label: 'Closed', tone: 'neutral' },
};

export const LANE_LABEL: Record<Schemas['Lane'] | 'flagship', string> = {
  derivative: 'Derivative',
  native: 'Native',
  quick_take: 'Quick take',
  repurpose: 'Repurpose',
  flagship: 'Flagship article',
};

/** What each lane is, for a hint under a lane choice. */
export const LANE_HINT: Record<Schemas['Lane'], string> = {
  derivative: 'A re-cut of the flagship that publishes this Monday.',
  native: 'A post from your stories and takes, on another pillar than the flagship.',
  quick_take: 'Your take on a news idea, out within 72 hours.',
  repurpose: 'A new angle on an earlier piece that did well.',
};

export const FORMAT_LABEL: Record<Schemas['PostFormat'], string> = {
  text: 'Text post',
  carousel: 'Carousel',
  image_card: 'Image card',
  poll: 'Poll',
};

export const MODE_LABEL: Record<Schemas['WeekMode'], string> = {
  normal: 'Normal',
  light: 'Light',
  away: 'Away',
};

/** "3 posts and the flagship", from the cadence's posts per mode. */
export function modeSummary(
  mode: Schemas['WeekMode'],
  postsPerWeek: { readonly normal: number; readonly light: number; readonly away: number },
): string {
  const posts = (count: number) => `${String(count)} ${count === 1 ? 'post' : 'posts'}`;
  switch (mode) {
    case 'normal':
      return `${posts(postsPerWeek.normal)} and the flagship article`;
    case 'light':
      return `${posts(postsPerWeek.light)}; the flagship article is optional`;
    case 'away':
      return 'Nothing new is generated';
  }
}

export const IDEA_STATUS: Record<Schemas['IdeaStatus'], string> = {
  new: 'New',
  accepted: 'Kept',
  snoozed: 'Snoozed',
  used: 'Used',
  dismissed: 'Dismissed',
  expired: 'Expired',
};

export const IDEA_ORIGIN: Record<Schemas['IdeaOrigin'], string> = {
  feed: 'From your feeds',
  scout: 'From weekly scouting',
  quick_capture: 'Captured by you',
  owner: 'Added by you',
  repurpose: 'A repurpose candidate',
  plan: 'From the planner',
};

/** "3 posts", "1 post". */
export function countLabel(count: number, one: string, many = `${one}s`): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}

/** "62%" for 0.62. */
export function percentLabel(fraction: number): string {
  return `${String(Math.round(fraction * 100))}%`;
}
