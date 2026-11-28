import type { components } from '@/lib/api/schema';

/**
 * The server's models the Library works with, named once (`schema.ts` is generated from the
 * integrated server: these are the real shapes).
 */
type Schemas = components['schemas'];

export type Finding = Schemas['Finding'];
export type MediaRef = Schemas['MediaRef'];
export type JobAccepted = Schemas['JobAccepted'];

// Pillars, lanes, formats, brand (kb singletons)
export type PillarSettings = Schemas['PillarSettings'];
export type Pillar = Schemas['Pillar-Output'];
export type PillarInput = Schemas['blogwriter__domain__settings__Pillar'];
export type LaneSettings = Schemas['LaneSettings'];
export type LanesIn = Schemas['LanesIn'];
export type SeriesDef = Schemas['SeriesDef'];
export type FormatPacks = Schemas['FormatPacks'];
export type FormatPack = Schemas['FormatPack'];
export type BrandKit = Schemas['BrandKit'];
export type BrandIn = Schemas['BrandIn'];
export type BrandColors = Schemas['BrandColors'];
export type EmployerPolicy = Schemas['EmployerPolicy'];
export type Source = Schemas['Source'];
export type PostSummary = Schemas['PostSummary'];
export type OwnerInputs = Schemas['OwnerInputs'];
export type OwnerInput = Schemas['OwnerInput'];

// Stories and POVs
export type StoryEntry = Schemas['StoryEntry'];
export type StoryIn = Schemas['StoryIn'];
export type StoryMetric = Schemas['StoryMetric'];
export type MetricIn = Schemas['MetricIn'];
export type PovEntry = Schemas['PovEntry'];
export type PovIn = Schemas['PovIn'];
export type ReviewStatus = Schemas['ReviewStatus'];
export type ReviewDecision = Schemas['ReviewDecision'];
export type Confidentiality = Schemas['Confidentiality'];
export type ClassifierCheck = Schemas['ClassifierCheck'];
export type StoryOrigin = Schemas['StoryOrigin'];
export type PovOrigin = Schemas['PovOrigin'];
export type RedactionProposal = Schemas['RedactionProposal'];
export type ProposalReason = Schemas['ProposalReason'];
export type IntakePurpose = Schemas['IntakePurpose'];
export type CheckOut = Schemas['CheckOut'];

// Voice
export type VoiceSample = Schemas['VoiceSample'];
export type VoiceSourceType = Schemas['VoiceSourceType'];
export type OwnerSourceType = Schemas['OwnerSourceType'];
export type SampleIn = Schemas['SampleIn'];
export type SampleUpdateIn = Schemas['SampleUpdateIn'];
export type SampleStatus = Schemas['SampleStatus'];
export type StyleGuide = Schemas['StyleGuide'];
export type GuideRules = Schemas['GuideRules'];
export type GuideRulesIn = Schemas['GuideRulesIn'];
export type GuideView = Schemas['GuideView'];
export type GuideVersionSummary = Schemas['GuideVersionSummary'];
export type GuideStatus = Schemas['GuideStatus'];
export type VoiceDigest = Schemas['VoiceDigest'];
export type GuideSuggestion = Schemas['GuideSuggestion'];
export type DraftComparison = Schemas['DraftComparison'];

/** One document and the etag to send back with a change to it (contract 1.4). */
export interface Entry<T> {
  readonly item: T;
  readonly etag: string | null;
}
