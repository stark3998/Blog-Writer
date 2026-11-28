import { CircleAlert } from 'lucide-react';
import { useId } from 'react';

import { cn } from '@/lib/cn';
import { pillarThreadClass } from '@/lib/pillars';

import { formatShare } from '../lib/format';

export interface MixEntry {
  readonly key: string;
  readonly id: string;
  readonly name: string;
  /** 0-1; `undefined` while the typed share cannot be read. */
  readonly share: number | undefined;
}

/**
 * The target mix as one ribbon: each pillar's thread as wide as its share of the week's posts,
 * the rest left open. The names and numbers under it carry the information; the ribbon is the
 * picture of it (hidden from screen readers, which read the list).
 */
export function PillarMix({ entries }: { entries: readonly MixEntry[] }) {
  const captionId = useId();
  const total = entries.reduce((sum, entry) => sum + (entry.share ?? 0), 0);
  const over = total > 1 + 1e-9;
  const scale = over ? total : 1;
  const open = Math.max(0, 1 - total);

  return (
    <figure aria-labelledby={captionId} className="space-y-3">
      <figcaption id={captionId} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="text-sm font-semibold text-ink">Target share of each week’s posts</span>
        <span className={cn('flex items-center gap-1.5 text-sm', over ? 'font-medium text-danger' : 'text-ink-muted')}>
          {over ? <CircleAlert aria-hidden="true" className="size-4" /> : null}
          {over
            ? `The shares add up to ${formatShare(total)}. Lower them to 100% or less.`
            : open > 0.0005
              ? `${formatShare(open)} not assigned to a pillar`
              : 'Every post has a pillar'}
        </span>
      </figcaption>
      <div aria-hidden="true" className="flex h-3 w-full overflow-hidden rounded-full bg-wash-strong">
        {entries.map((entry) =>
          entry.share !== undefined && entry.share > 0 ? (
            <span
              key={entry.key}
              className={cn('h-full border-r-2 border-sheet last:border-r-0', pillarThreadClass(entry.id))}
              style={{ width: `${String((entry.share / scale) * 100)}%` }}
            />
          ) : null,
        )}
      </div>
      <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {entries.map((entry) => (
          <li key={entry.key} className="flex items-center gap-2">
            <span aria-hidden="true" className={cn('size-2.5 rounded-full', pillarThreadClass(entry.id))} />
            <span className="text-ink">{entry.name.trim() || 'New pillar'}</span>
            <span className="tabular-nums text-ink-muted">{entry.share === undefined ? '–' : formatShare(entry.share)}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
