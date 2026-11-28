import type { components } from '@/lib/api/schema';

/** The server's models the Draft tab reads, by their generated names (`schema.ts`). */
type Schemas = components['schemas'];

export type Article = Schemas['Article'];
export type ArticleStatus = Schemas['ArticleStatus'];
export type ArticleMeta = Schemas['ArticleMeta'];
export type ArticleMetaIn = Schemas['ArticleMetaIn'];
export type VersionedArticle = Schemas['Versioned_Article_'];
export type ArticleSaved = Schemas['ArticleSaved'];
export type Finding = Schemas['Finding'];
export type Severity = Schemas['Severity'];
export type GatesView = Schemas['GatesView'];
export type GateReport = Schemas['GateReport'];
export type GateResult = Schemas['GateResult'];
export type GateName = Schemas['GateName'];
export type GateVerdict = Schemas['GateVerdict'];
export type VersionSummary = Schemas['VersionSummary'];
export type ArticleVersion = Schemas['ArticleVersion'];
export type Brief = Schemas['Brief'];
export type BriefCitation = Schemas['Citation'];
export type FormatPack = Schemas['FormatPack'];
export type Pillar = Schemas['Pillar-Output'];
export type PublishStatus = Schemas['PublishStatusOut'];
export type PublishState = Schemas['PublishState'];
export type VetoView = Schemas['VetoView'];
export type MediaRef = Schemas['MediaRef'];
export type SelectionOut = Schemas['SelectionOut'];
export type StartFrom = Schemas['StartFrom'];
export type ApproveIn = Schemas['blogwriter__publish__models__ApproveIn'];
