import type { components } from '@/lib/api/schema';

/**
 * The post models as the server sends them (`schema.ts`, generated from the integrated server).
 * Aliases only: the composer and go-live read these shapes and never re-declare them.
 */
type Schemas = components['schemas'];

export type Post = Schemas['Post'];
export type PostSummary = Schemas['PostSummary'];
export type PostStatus = Schemas['PostStatus'];
export type PostFormat = Schemas['PostFormat'];
export type Lane = Schemas['Lane'];
export type KitState = Schemas['KitState'];
export type HookType = Schemas['HookType'];
export type HookVariant = Schemas['HookVariant'];
export type Finding = Schemas['Finding'];
export type CheckState = Schemas['CheckState'];
export type MediaRef = Schemas['MediaRef'];
export type Slot = Schemas['Slot'];
export type GoLiveView = Schemas['GoLiveView'];
export type KitView = Schemas['KitView'];
export type KitDownload = Schemas['KitDownload'];
export type KitIn = Schemas['KitIn'];
export type ConnectionView = Schemas['ConnectionView'];
export type ConnectionStatus = Schemas['ConnectionStatus'];
export type PostEditIn = Schemas['PostEditIn'];
export type Pillar = Schemas['Pillar-Output'];
export type PillarSettings = Schemas['PillarSettings'];
export type BrandKit = Schemas['BrandKit'];
export type Article = Schemas['Article'];
export type ArticleStatus = Schemas['ArticleStatus'];
export type SlotIn = Schemas['blogwriter__linkedin__posts__models__SlotIn'];
export type LintOut = Schemas['blogwriter__linkedin__posts__models__LintOut'];

/** A document with its etag (`domain.api.Versioned`). */
export interface Versioned<T> {
  readonly item: T;
  readonly etag: string | null;
}

/** The three checks approval needs on exactly the current content (`linkedin.posts.checks`). */
export type CheckName = 'lint' | 'derived_claims' | 'near_duplicate';

export const CHECK_NAMES: readonly CheckName[] = ['lint', 'derived_claims', 'near_duplicate'];

/** The stored state of one check on a post. */
export function checkStateOf(post: Post, name: CheckName): CheckState | null | undefined {
  switch (name) {
    case 'lint':
      return post.lint;
    case 'derived_claims':
      return post.derivedClaims;
    case 'near_duplicate':
      return post.nearDuplicate;
  }
}

/** The post field a finding points into (`Finding.field`: `displayText`, `slides[2]`, `carouselSpec`). */
export function findingField(finding: Finding): string | undefined {
  return finding.field ?? undefined;
}

/** The slide (0-based) a finding is about, from its field (`slides[2]` -> 2). */
export function findingSlide(finding: Finding): number | undefined {
  const match = /^slides\[(\d+)\]/.exec(findingField(finding) ?? '');
  return match?.[1] === undefined ? undefined : Number(match[1]);
}

/** True when a finding points into the post text (the composer's textarea). */
export function findingInText(finding: Finding): boolean {
  const field = findingField(finding);
  return field === undefined || field === 'displayText';
}

/** How many findings a stored check left out (`CheckState.truncated`: "and N more"). */
export function truncatedCount(state: CheckState | null | undefined): number {
  return state?.truncated ?? 0;
}

/** True for states the owner edits text in (`linkedin.posts.routes.EDITABLE`). */
export function isEditable(post: Pick<Post, 'status' | 'intent'>): boolean {
  switch (post.status) {
    case 'draft':
    case 'approved':
    case 'staged':
    case 'expired':
      return true;
    case 'needs_attention':
      // Only before any publish attempt: after one, the owner resolves it on the go-live page.
      return post.intent === null || post.intent === undefined;
    case 'generating':
    case 'publishing':
    case 'posted':
    case 'kit':
    case 'missed':
    case 'deleted':
    case 'archived':
      return false;
  }
}

/** True while the post carries an approval (any edit moves it back to Draft). */
export function isApproved(status: PostStatus): boolean {
  return status === 'approved' || status === 'staged';
}

/** Formats that need rendered media before approval (`carousel.MEDIA_FORMATS`). */
export function hasSlides(format: PostFormat): boolean {
  return format === 'carousel' || format === 'image_card';
}

/** The first media item with this role. */
export function mediaWithRole(post: Pick<Post, 'media'>, role: MediaRef['role']): MediaRef | undefined {
  return post.media?.find((item) => item.role === role);
}

/** The Studio piece a post belongs to: its article (derivatives) or the post itself. */
export function pieceOf(post: Pick<Post, 'id' | 'articleId'>): string {
  return post.articleId ?? post.id;
}

/** The go-live page of a post (where push notifications and Today land). */
export function goLivePath(post: Pick<Post, 'id' | 'articleId'>): string {
  return `/studio/${encodeURIComponent(pieceOf(post))}/go-live?post=${encodeURIComponent(post.id)}`;
}

/** The composer of a post. */
export function postsPath(post: Pick<Post, 'id' | 'articleId'>): string {
  const piece = pieceOf(post);
  const base = `/studio/${encodeURIComponent(piece)}/posts`;
  return piece === post.id ? base : `${base}?post=${encodeURIComponent(post.id)}`;
}
