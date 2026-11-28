import type { VariantProps } from 'class-variance-authority';

import type { badgeVariants } from '@/components/ui/badge';
import { useApiQuery } from '@/lib/query/api-query';
import { KB_SINGLETONS, queryKeys } from '@/lib/query/keys';
import { PILLARS } from '@/lib/pillars';

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>['variant']>;

export interface PillarOption {
  readonly id: string;
  readonly name: string;
}

const THREADS: Readonly<Record<string, BadgeVariant>> = {
  agents: 'agents',
  identity: 'identity',
  governance: 'governance',
};

/** The badge colour of a pillar: its thread for the three default pillars, neutral otherwise. */
export function pillarVariant(id: string): BadgeVariant {
  return THREADS[id] ?? 'neutral';
}

/**
 * The owner's pillars (`GET /api/kb/pillars`, shared with Library > Pillars), for the watchlist's
 * pillar choice and names. Until they load, the plan's three pillars stand in.
 */
export function usePillarOptions(): readonly PillarOption[] {
  const pillars = useApiQuery({ queryKey: queryKeys.kb.detail(KB_SINGLETONS.pillars), path: '/api/kb/pillars' });
  const saved = pillars.data?.item.pillars;
  if (saved && saved.length > 0) return saved.map((pillar) => ({ id: pillar.id, name: pillar.name }));
  return PILLARS.map((pillar) => ({ id: pillar.id, name: pillar.name }));
}

/** A pillar's name, or its id when it is not (or no longer) a pillar. */
export function pillarName(options: readonly PillarOption[], id: string): string {
  return options.find((option) => option.id === id)?.name ?? id;
}
