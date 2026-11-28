import { CircleX, Info, TriangleAlert } from 'lucide-react';
import { useId } from 'react';

import { cn } from '@/lib/cn';

import { findingSlot, sortedSlots, type Finding, type WeekPlan } from '../lib/plan';
import { slotLabel } from '../lib/time';

/** "Wed 2 Dec, 10:00: Two posts on the same day." when the finding points at a slot. */
export function findingText(finding: Finding, plan: WeekPlan): string {
  const slot = findingSlot(finding, sortedSlots(plan));
  return slot ? `${slotLabel(slot.slot.localDate, slot.slot.localTime)}: ${finding.message}` : finding.message;
}

interface FindingListProps {
  findings: readonly Finding[];
  plan: WeekPlan;
  className?: string;
}

/** Findings as a plain list, each with its icon and words (blocks first). */
export function FindingList({ findings, plan, className }: FindingListProps) {
  const ordered = [...findings].sort((a, b) => rank(a) - rank(b));
  return (
    <ul className={cn('space-y-1.5 text-sm', className)}>
      {ordered.map((finding, index) => {
        const Icon = finding.severity === 'block' ? CircleX : finding.severity === 'warn' ? TriangleAlert : Info;
        const tone = finding.severity === 'block' ? 'text-danger' : finding.severity === 'warn' ? 'text-warn' : 'text-ink-subtle';
        return (
          <li key={`${finding.code}-${finding.ref ?? String(index)}`} className="flex items-start gap-2">
            <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
            <span className="text-ink">
              <span className="sr-only">{finding.severity === 'block' ? 'Blocks approval: ' : 'Worth a look: '}</span>
              {findingText(finding, plan)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function rank(finding: Finding): number {
  return finding.severity === 'block' ? 0 : finding.severity === 'warn' ? 1 : 2;
}

interface PlanFindingsProps {
  plan: WeekPlan;
}

/** The plan's warnings that do not block the approval ("worth a look"). Blocks show by the approve button. */
export function AdvisorySection({ plan }: PlanFindingsProps) {
  const headingId = useId();
  const advisory = (plan.warnings ?? []).filter((finding) => finding.severity !== 'block');
  if (advisory.length === 0) return null;
  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <h3 id={headingId} className="font-semibold text-ink">
        Worth a look
      </h3>
      <FindingList findings={advisory} plan={plan} />
    </section>
  );
}
