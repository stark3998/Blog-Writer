import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/cn';
import { isPlanPillar, pillarThreadClass } from '@/lib/pillars';

export interface PillarBadgeProps {
  /** The pillar id (`agents`); the three plan pillars have their own colours. */
  id: string;
  /** The pillar's name from the owner's settings; the id when it is not known. */
  name?: string;
  className?: string;
}

/**
 * A pillar's name on its soft colour; a pillar the owner added later is neutral. The colour never
 * stands alone: the name is the label.
 */
export function PillarBadge({ id, name, className }: PillarBadgeProps) {
  return (
    <Badge variant={isPlanPillar(id) ? id : 'neutral'} className={className}>
      {name ?? id}
    </Badge>
  );
}

/** A short vertical thread in the pillar's colour, before its name in lists. Decorative. */
export function PillarThread({ id, className }: { id: string | null | undefined; className?: string }) {
  return (
    <span
      aria-hidden="true"
      data-slot="pillar-thread"
      className={cn('inline-block h-4 w-1 shrink-0 rounded-full', pillarThreadClass(id), className)}
    />
  );
}
