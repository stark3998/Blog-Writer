import { CircleCheck, CircleX } from 'lucide-react';
import { useId } from 'react';

import { cn } from '@/lib/cn';
import { pillarThreadClass } from '@/lib/pillars';

import { countLabel, percentLabel } from '../lib/labels';
import { meetsOnPillarRule, pillarBalance, type PlannedSlot } from '../lib/plan';
import type { PillarView } from '../lib/settings';

interface PillarBalanceProps {
  slots: readonly PlannedSlot[];
  pillars: readonly PillarView[];
  /** The lane rules' minimum on-pillar share (0.8). */
  minShare: number;
}

/**
 * The week's posts per pillar as one bar woven from the pillar threads, with the 80% mark:
 * whatever sits right of the mark may be off pillar, nothing more. The legend names every pillar
 * with its count, so the colours never carry the meaning alone.
 */
export function PillarBalanceSection({ slots, pillars, minShare }: PillarBalanceProps) {
  const headingId = useId();
  const balance = pillarBalance(slots, pillars.map((pillar) => pillar.id));
  const meets = meetsOnPillarRule(balance, minShare);
  const minimum = percentLabel(minShare);
  const segments = [
    ...pillars.map((pillar) => ({ id: pillar.id, name: pillar.name, count: balance.counts.get(pillar.id) ?? 0, target: pillar.targetShare })),
    ...(balance.other > 0 ? [{ id: 'other', name: 'Off pillar', count: balance.other, target: undefined }] : []),
  ];

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h3 id={headingId} className="font-semibold text-ink">
        Pillar balance
      </h3>
      {balance.total === 0 ? (
        <p className="text-ink-muted">No posts planned yet.</p>
      ) : (
        <>
          <p className={cn('flex items-start gap-2 text-sm', meets ? 'text-ink' : 'text-danger')}>
            {meets ? (
              <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-success" />
            ) : (
              <CircleX aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            )}
            <span>
              {`${String(balance.onPillar)} of ${countLabel(balance.total, 'post')} on a pillar (${percentLabel(balance.share ?? 0)}). `}
              {meets ? `At least ${minimum} must be.` : `Below the ${minimum} rule: approval is blocked until more posts are on a pillar.`}
            </span>
          </p>
          <div className="relative pt-1" aria-hidden="true">
            <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full bg-wash-strong">
              {segments
                .filter((segment) => segment.count > 0)
                .map((segment) => (
                  <span
                    key={segment.id}
                    className={cn('h-full', pillarThreadClass(segment.id === 'other' ? null : segment.id))}
                    style={{ width: `${String((segment.count / balance.total) * 100)}%` }}
                  />
                ))}
            </div>
            <span
              className="absolute -bottom-1 top-0 w-0.5 rounded-full bg-ink"
              style={{ left: `${String(minShare * 100)}%` }}
            />
          </div>
          <ul className="grid gap-1.5 text-sm sm:grid-cols-2">
            {segments.map((segment) => (
              <li key={segment.id} className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className={cn('h-3 w-1 shrink-0 rounded-full', pillarThreadClass(segment.id === 'other' ? null : segment.id))}
                />
                <span className="min-w-0 flex-1 truncate text-ink">{segment.name}</span>
                <span className="shrink-0 tabular-nums text-ink-muted">
                  {countLabel(segment.count, 'post')}
                  {segment.target !== undefined ? <span className="sr-only">{`, target share ${percentLabel(segment.target)}`}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
