import { Cpu, TriangleAlert } from 'lucide-react';
import { useId, type ReactNode } from 'react';

import { Badge, badgeVariants } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/cn';
import { PillarBadge as SharedPillarBadge } from '@/components/ui/pillar-badge';

import { formatCost, formatDateTime } from './format';
import type { PillarView } from './pillars';
import { humanize, statusMeta, type PieceFacts, type StatusTone } from './piece-status';
import type { ServedByRecord } from './types';

const TONE_BADGE = {
  neutral: 'neutral',
  progress: 'outline',
  attention: 'warn',
  success: 'success',
  danger: 'danger',
  muted: 'neutral',
} as const satisfies Record<StatusTone, 'neutral' | 'outline' | 'warn' | 'success' | 'danger'>;

/** A piece's status with its icon and glossary label (never colour alone). */
export function StatusBadge({ piece, className, id }: { piece: Pick<PieceFacts, 'kind' | 'status'>; className?: string; id?: string }) {
  const meta = statusMeta(piece);
  const Icon = meta.icon;
  return (
    <Badge id={id} variant={TONE_BADGE[meta.tone]} data-tone={meta.tone} className={className}>
      <Icon aria-hidden="true" />
      {meta.label}
    </Badge>
  );
}

/** A pillar's name on its own soft colour (the three pillars) or neutral (others). */
export function PillarBadge({ pillar, className }: { pillar: PillarView | undefined; className?: string }) {
  if (!pillar) return null;
  return <SharedPillarBadge id={pillar.id} name={pillar.name} className={className} />;
}

/** A plain fact chip (format, lane, word count). */
export function FactChip({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Badge variant="outline" className={cn('font-normal text-ink-muted', className)}>
      {children}
    </Badge>
  );
}

const REVIEW_REASONS: Readonly<Record<string, string>> = {
  fallback_model: 'A fallback model wrote part of it, not the model set for the task.',
  refusal: 'A model refused part of the work along the way.',
};

/**
 * "Review only": the piece can never auto-publish (a fallback model or a refusal in the chain,
 * contract 1.8). The badge opens a short explanation with the reasons.
 */
export function ReviewOnlyBadge({ reasons }: { reasons: readonly string[] | undefined }) {
  const headingId = useId();
  const list = reasons && reasons.length > 0 ? reasons : [];
  return (
    <Popover>
      <PopoverTrigger className={cn(badgeVariants({ variant: 'warn' }), 'cursor-pointer pointer-coarse:min-h-8')}>
        <TriangleAlert aria-hidden="true" />
        Review only
      </PopoverTrigger>
      <PopoverContent align="start" aria-labelledby={headingId} className="text-sm">
        <p id={headingId} className="font-semibold">
          You review it before it goes out
        </p>
        <p className="mt-1 text-ink-muted">It can’t be published automatically.</p>
        {list.length > 0 ? (
          <ul className="mt-2 list-disc space-y-1 pl-5 text-ink-muted">
            {list.map((reason) => (
              <li key={reason}>{REVIEW_REASONS[reason] ?? humanize(reason)}</li>
            ))}
          </ul>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

const STEP_LABELS: Readonly<Record<string, string>> = {
  plan: 'Research plan',
  search: 'Search',
  extract: 'Reading sources',
  verify: 'Fact check',
  brief: 'Brief',
  outline: 'Outline',
  draft: 'Draft',
  critique: 'Critique',
  critique_revise: 'Critique and revision',
  revise: 'Revision',
  interview: 'Interview questions',
  interview_questions: 'Interview questions',
  generate: 'Writing',
  derive: 'Writing',
  hooks: 'Hooks',
  quick_search: 'Quick search',
};

export function servedByStepLabel(step: string): string {
  return STEP_LABELS[step] ?? humanize(step);
}

/** The record that best answers "which model wrote this": the latest writing step, else the latest. */
export function primaryServedBy(records: readonly ServedByRecord[]): ServedByRecord | undefined {
  const writing = new Set(['draft', 'revise', 'critique_revise', 'generate', 'derive', 'brief']);
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record && writing.has(record.step)) return record;
  }
  return records.at(-1);
}

/**
 * Which models worked on the piece (`servedBy`): the writing model on the badge, every step with
 * its model, fallbacks, refusals and cost in the panel it opens.
 */
export function ServedByBadge({ records }: { records: readonly ServedByRecord[] | undefined }) {
  const headingId = useId();
  if (!records || records.length === 0) return null;
  const primary = primaryServedBy(records);
  if (!primary) return null;
  const total = records.reduce((sum, record) => sum + (record.costUsd ?? 0), 0);
  const totalCost = formatCost(total);
  return (
    <Popover>
      <PopoverTrigger
        aria-label={`Models: ${primary.model}`}
        className={cn(badgeVariants({ variant: 'neutral' }), 'cursor-pointer pointer-coarse:min-h-8')}
      >
        <Cpu aria-hidden="true" />
        {primary.model}
      </PopoverTrigger>
      <PopoverContent align="start" aria-labelledby={headingId} className="w-80 text-sm">
        <p id={headingId} className="font-semibold">
          Models that worked on it
        </p>
        <ul className="mt-2 divide-y divide-line">
          {records.map((record, index) => {
            const notes = [
              record.fallbackReason ? `fallback (${humanize(record.fallbackReason)})` : undefined,
              record.refused ? 'refused part of it' : undefined,
              record.repaired ? 'output repaired' : undefined,
            ].filter((note): note is string => note !== undefined);
            const cost = formatCost(record.costUsd);
            return (
              <li key={`${record.step}-${record.at}-${String(index)}`} className="py-2">
                <p className="flex justify-between gap-3">
                  <span className="font-medium text-ink">{servedByStepLabel(record.step)}</span>
                  {cost ? <span className="tabular-nums text-ink-subtle">{cost}</span> : null}
                </p>
                <p className="text-ink-muted">
                  {record.model}
                  {record.effort ? `, ${record.effort} effort` : ''}
                </p>
                {notes.length > 0 ? (
                  <p className="flex items-center gap-1.5 text-warn">
                    <TriangleAlert aria-hidden="true" className="size-3.5 shrink-0" />
                    {notes.join(', ')}
                  </p>
                ) : null}
                <p className="text-xs text-ink-subtle">{formatDateTime(record.at)}</p>
              </li>
            );
          })}
        </ul>
        {totalCost ? <p className="mt-2 border-t border-line pt-2 text-ink-muted">Total {totalCost}</p> : null}
      </PopoverContent>
    </Popover>
  );
}
