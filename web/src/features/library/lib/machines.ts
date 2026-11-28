import type { ReviewDecision, ReviewStatus } from './types';

/**
 * The status machines the Library acts on, as `GET /api/meta/machines` serves them
 * (`{<machine>: {<state>: [targets]}}`). Buttons are enabled from the server's table (contract
 * section 10); this copy of contract section 2 is only the fallback while it loads or when it
 * cannot be read, and the server refuses a wrong edge anyway (409 `invalid_transition`).
 */
export type MachineTable = Readonly<Record<string, Readonly<Record<string, readonly string[]>>>>;

export const FALLBACK_MACHINES: MachineTable = {
  review: {
    proposed: ['approved', 'rejected'],
    approved: ['archived'],
    rejected: ['proposed'],
    archived: ['approved'],
  },
  voice_sample: { active: ['excluded'], excluded: ['active'] },
  style_guide: { proposed: ['active', 'rejected'], active: ['superseded'], superseded: ['active'], rejected: [] },
  suggestion: { proposed: ['accepted', 'dismissed'], accepted: [], dismissed: [] },
};

/** Whether `machine` has the edge `from` -> `to`. */
export function canMove(table: MachineTable | undefined, machine: string, from: string, to: string): boolean {
  const states = table?.[machine] ?? FALLBACK_MACHINES[machine];
  return states?.[from]?.includes(to) ?? false;
}

/** The review status a decision leads to (`stories.routes._review_target`). */
export function reviewTarget(status: ReviewStatus, decision: ReviewDecision): ReviewStatus {
  switch (decision) {
    case 'approve':
      return 'approved';
    case 'reject':
      return 'rejected';
    case 'archive':
      return 'archived';
    case 'restore':
      return status === 'archived' ? 'approved' : 'proposed';
  }
}

/** The review decisions an entry in `status` can take, in the order the buttons show them. */
export function reviewDecisions(table: MachineTable | undefined, status: ReviewStatus): ReviewDecision[] {
  const candidates: readonly ReviewDecision[] = ['approve', 'reject', 'archive', 'restore'];
  return candidates.filter((decision) => {
    // "Restore" is how an archived or rejected entry comes back; "approve" covers proposed ones.
    if (decision === 'restore' && status === 'proposed') return false;
    if (decision === 'approve' && status !== 'proposed') return false;
    return canMove(table, 'review', status, reviewTarget(status, decision));
  });
}
