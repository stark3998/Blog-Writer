import { useId } from 'react';

import { formatInteger } from '../lib/format';

export interface RulerEntry {
  readonly key: string;
  readonly name: string;
  /** Words; `undefined` while the typed number cannot be read. */
  readonly min: number | undefined;
  readonly max: number | undefined;
}

function rangeText(entry: RulerEntry): string {
  if (entry.min === undefined || entry.max === undefined) return 'Length not set';
  return `${formatInteger(entry.min)}–${formatInteger(entry.max)} words`;
}

/**
 * Every format's length on one ruler, so the formats compare at a glance: a typesetter's rule
 * marked in words, each format a bar from its shortest to its longest article.
 */
export function FormatRuler({ entries }: { entries: readonly RulerEntry[] }) {
  const captionId = useId();
  const longest = Math.max(1000, ...entries.map((entry) => entry.max ?? 0));
  const step = longest > 6000 ? 2000 : longest > 3000 ? 1000 : 500;
  const axis = Math.ceil(longest / step) * step;
  const ticks = Array.from({ length: axis / step + 1 }, (_, index) => index * step);
  const at = (words: number) => `${String(Math.min(100, Math.max(0, (words / axis) * 100)))}%`;

  return (
    <figure aria-labelledby={captionId} className="space-y-4">
      <figcaption id={captionId} className="text-sm font-semibold text-ink">
        Article length by format
      </figcaption>
      <ul className="space-y-4">
        {entries.map((entry) => {
          const valid = entry.min !== undefined && entry.max !== undefined && entry.max >= entry.min;
          return (
            <li key={entry.key} className="grid gap-1.5 sm:grid-cols-[12rem_minmax(0,1fr)_9rem] sm:items-center sm:gap-4">
              <span className="truncate text-sm text-ink">{entry.name.trim() || 'New format'}</span>
              <div aria-hidden="true" className="relative h-4">
                <div className="absolute inset-x-0 top-1/2 h-px bg-line-strong" />
                {ticks.map((tick) => (
                  <div key={tick} className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-line-strong" style={{ left: at(tick) }} />
                ))}
                {valid ? (
                  <div
                    className="absolute top-1/2 h-2.5 min-w-1 -translate-y-1/2 rounded-full bg-ink"
                    style={{ left: at(entry.min), width: `${String(((entry.max - entry.min) / axis) * 100)}%` }}
                  />
                ) : null}
              </div>
              <span className="text-sm tabular-nums text-ink-muted sm:text-right">{rangeText(entry)}</span>
            </li>
          );
        })}
      </ul>
      <div aria-hidden="true" className="hidden sm:grid sm:grid-cols-[12rem_minmax(0,1fr)_9rem] sm:gap-4">
        <span />
        <div className="relative h-4 text-xs tabular-nums text-ink-subtle">
          {ticks.map((tick, index) => (
            <span
              key={tick}
              className={index === 0 ? 'absolute' : index === ticks.length - 1 ? 'absolute -translate-x-full' : 'absolute -translate-x-1/2'}
              style={{ left: at(tick) }}
            >
              {formatInteger(tick)}
            </span>
          ))}
        </div>
        <span />
      </div>
    </figure>
  );
}
