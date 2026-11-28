import type { components } from '@/lib/api/schema';

type Schemas = components['schemas'];

/** A flagship article (`GET /api/articles/{id}`). */
export type Article = Schemas['Article'];
export type ArticleStatus = Schemas['ArticleStatus'];
/** A list item of `GET /api/articles` (no Markdown). */
export type ArticleSummary = Schemas['ArticleSummary'];

/** A LinkedIn post (`GET /api/posts/{id}`): a standalone piece or a derivative of an article. */
export type Post = Schemas['Post'];
export type PostStatus = Schemas['PostStatus'];
export type PostFormat = Schemas['PostFormat'];
/** A list item of `GET /api/posts` (no text). */
export type PostSummary = Schemas['PostSummary'];
export type Lane = Schemas['Lane'];
export type Slot = Schemas['Slot'];

/** Which model served one AI step, as stored on the document it wrote. */
export type ServedByRecord = Schemas['ServedByRecord'];

export type Pillar = Schemas['Pillar-Output'];
export type FormatPack = Schemas['FormatPack'];

/**
 * `GET /api/meta/machines` in full: every status machine's edges, the edges the owner takes and
 * who writes in each state (`useMachineViews()`).
 */
export type { MachineViews as Machines } from '@/lib/query/machines';

/** The machines the Studio reads (`domain.states` names). */
export type MachineName = 'article' | 'post' | 'research_run' | 'idea';
