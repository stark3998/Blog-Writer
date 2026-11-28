import type { ComponentProps } from 'react';

import { cn } from '@/lib/cn';
import { pillarThreadClass } from '@/lib/pillars';

import type { PillarView } from '../lib/settings';

/**
 * A pillar is always its colour thread **and** its name (never colour alone). The three plan
 * pillars have their own tokens; a pillar the owner added later gets a neutral thread.
 */

const TEXT: Readonly<Record<string, string>> = {
  agents: 'text-agents',
  identity: 'text-identity',
  governance: 'text-governance',
};

const SOFT: Readonly<Record<string, string>> = {
  agents: 'bg-agents-soft',
  identity: 'bg-identity-soft',
  governance: 'bg-governance-soft',
};

export function pillarTextClass(pillarId: string | null | undefined): string {
  return (pillarId ? TEXT[pillarId] : undefined) ?? 'text-ink-muted';
}

export function pillarSoftClass(pillarId: string | null | undefined): string {
  return (pillarId ? SOFT[pillarId] : undefined) ?? 'bg-sunken';
}

/** The pillar's name from the owner's list; "No pillar" when there is none. */
export function pillarName(pillars: readonly PillarView[], pillarId: string | null | undefined): string {
  if (!pillarId) return 'No pillar';
  return pillars.find((pillar) => pillar.id === pillarId)?.name ?? pillarId;
}

interface PillarTagProps extends ComponentProps<'span'> {
  pillarId: string | null | undefined;
  pillars: readonly PillarView[];
}

/** The pillar's thread and name, as one inline label. */
export function PillarTag({ pillarId, pillars, className, ...props }: PillarTagProps) {
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1.5', className)} {...props}>
      <span aria-hidden="true" className={cn('h-3 w-1 shrink-0 rounded-full', pillarThreadClass(pillarId))} />
      <span className="truncate">{pillarName(pillars, pillarId)}</span>
    </span>
  );
}
