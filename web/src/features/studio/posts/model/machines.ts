import type { KitState, PostStatus } from './types';

/**
 * Which status changes exist. Action buttons are enabled from the server's machines
 * (`GET /api/meta/machines`, contract section 10); until that answers (or if it cannot), the
 * contract's own table (section 2) stands in, and the server refuses anything else anyway
 * (409 `invalid_transition`).
 */
export type Machines = Readonly<Record<string, Readonly<Record<string, readonly string[]>>>>;

export const CONTRACT_POST_MACHINE: Readonly<Record<PostStatus, readonly PostStatus[]>> = {
  draft: ['generating', 'approved', 'archived', 'expired'],
  generating: ['draft', 'needs_attention', 'archived'],
  approved: ['draft', 'staged', 'publishing', 'kit', 'missed', 'expired', 'archived'],
  staged: ['draft', 'approved', 'publishing', 'kit', 'missed', 'expired'],
  publishing: ['posted', 'approved', 'needs_attention'],
  needs_attention: ['posted', 'approved', 'kit', 'draft', 'archived'],
  kit: ['posted', 'approved', 'missed', 'expired', 'archived'],
  missed: ['approved', 'kit', 'expired', 'archived'],
  posted: ['deleted'],
  expired: ['draft', 'archived'],
  archived: ['draft'],
  deleted: [],
};

export const CONTRACT_KIT_MACHINE: Readonly<Record<KitState, readonly KitState[]>> = {
  prepared: ['native_draft_saved', 'natively_scheduled', 'posted'],
  native_draft_saved: ['natively_scheduled', 'posted'],
  natively_scheduled: ['native_draft_saved', 'posted'],
  posted: [],
};

const FALLBACK: Machines = { post: CONTRACT_POST_MACHINE, kit: CONTRACT_KIT_MACHINE };

/** True when `machine` has the edge `from -> to` (the server's table when given, else the contract's). */
export function canMove(machines: Machines | undefined, machine: 'post' | 'kit', from: string, to: string): boolean {
  const table = machines?.[machine] ?? FALLBACK[machine];
  return table?.[from]?.includes(to) ?? false;
}
