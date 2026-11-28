import type {
  Confidentiality,
  GuideStatus,
  OwnerSourceType,
  PovOrigin,
  ReviewStatus,
  StoryOrigin,
  VoiceSourceType,
} from './types';

/** The words the Library uses for the server's states and kinds (contract section 10 glossary). */

export const REVIEW_STATUS_LABEL: Readonly<Record<ReviewStatus, string>> = {
  proposed: 'To review',
  approved: 'Approved',
  rejected: 'Rejected',
  archived: 'Archived',
};

export const CONFIDENTIALITY_LABEL: Readonly<Record<Confidentiality, string>> = {
  anonymized: 'Anonymized',
  public: 'Public',
};

export const CONFIDENTIALITY_HINT: Readonly<Record<Confidentiality, string>> = {
  anonymized: 'Names, clients and exact figures are removed or rounded.',
  public: 'Everything in it is already public, so it can be quoted as it is.',
};

export const STORY_ORIGIN_LABEL: Readonly<Record<StoryOrigin, string>> = {
  interview: 'From an interview',
  seed: 'Proposed from your resume or portfolio',
  quick_capture: 'Quick capture',
  owner: 'Written by you',
};

export const POV_ORIGIN_LABEL: Readonly<Record<PovOrigin, string>> = {
  interview: 'From an interview',
  quick_capture: 'Quick capture',
  idea_take: 'Your take on an idea',
  seed: 'Proposed from your resume or portfolio',
  owner: 'Written by you',
};

/** A seeded entry names where it came from. */
export function seedOriginLabel(source: 'cv' | 'portfolio' | null | undefined): string | undefined {
  if (source === 'cv') return 'Proposed from your resume';
  if (source === 'portfolio') return 'Proposed from your portfolio';
  return undefined;
}

export const SOURCE_TYPE_LABEL: Readonly<Record<VoiceSourceType, string>> = {
  linkedin_post: 'LinkedIn post',
  linkedin_comment: 'LinkedIn comment',
  portfolio_post: 'Portfolio post',
  portfolio_about: 'Portfolio About page',
  interview_answer: 'Interview answer',
  final_post: 'Final posted text',
};

/** The sources the owner adds by hand, most useful first. */
export const OWNER_SOURCE_TYPES: readonly OwnerSourceType[] = [
  'linkedin_post',
  'linkedin_comment',
  'portfolio_post',
  'portfolio_about',
];

export const GUIDE_STATUS_LABEL: Readonly<Record<GuideStatus, string>> = {
  proposed: 'Waiting for approval',
  active: 'In use',
  superseded: 'Earlier version',
  rejected: 'Rejected',
};

export const OWNER_INPUT_LABEL: Readonly<Record<'full_interview' | 'micro_interview', string>> = {
  full_interview: 'Full interview',
  micro_interview: 'Micro-interview',
};

/** What a pillar reference in a `pillar_in_use` problem is, in the plural. */
export const REFERENCE_KIND_LABEL: Readonly<Record<string, string>> = {
  idea: 'ideas',
  article: 'articles',
  post: 'posts',
  story: 'stories',
  pov: 'positions',
  source: 'feeds',
  series: 'series',
};
