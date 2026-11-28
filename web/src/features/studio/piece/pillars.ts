import { PILLAR_THREAD_CLASS, PILLARS, type PillarId } from '@/lib/pillars';

import { humanize } from './piece-status';
import type { FormatPack, Pillar } from './types';

/** How a pillar is drawn: its name always, and its thread colour when it is one of the three. */
export interface PillarView {
  readonly id: string;
  readonly name: string;
  /** A `Badge` variant: the pillar's own for the three, `neutral` for others. */
  readonly badge: PillarId | 'neutral';
  /** A static class for the pillar's thread (a short coloured rule). */
  readonly thread: string;
}

const KNOWN_PILLARS: ReadonlySet<string> = new Set(PILLARS.map((pillar) => pillar.id));

export function isKnownPillar(id: string): id is PillarId {
  return KNOWN_PILLARS.has(id);
}

/**
 * The pillar `id` as the Studio shows it, named from the saved pillar settings when they are
 * loaded, else from the plan's three pillars, else from the id itself.
 */
export function pillarView(id: string | null | undefined, pillars?: readonly Pillar[]): PillarView | undefined {
  if (!id) return undefined;
  const saved = pillars?.find((pillar) => pillar.id === id)?.name;
  const known = isKnownPillar(id);
  const name = saved ?? PILLARS.find((pillar) => pillar.id === id)?.name ?? humanize(id);
  return {
    id,
    name,
    badge: known ? id : 'neutral',
    thread: known ? PILLAR_THREAD_CLASS[id] : 'bg-ink-subtle',
  };
}

/** The name of an article format pack ("Deep explainer"), from the saved packs or the id. */
export function formatName(id: string | null | undefined, packs?: readonly FormatPack[]): string | undefined {
  if (!id) return undefined;
  return packs?.find((pack) => pack.id === id)?.name ?? humanize(id);
}
