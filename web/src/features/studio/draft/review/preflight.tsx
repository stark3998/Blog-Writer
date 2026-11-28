import { CircleCheck, CircleX } from 'lucide-react';

import { cn } from '@/lib/cn';

import { GATES } from '../model/gates';
import { missingForPublish } from '../model/meta';
import type { Article, GatesView } from '../model/types';

/** What approving needs, checked before asking the server (which decides). */
export interface PreflightItem {
  readonly id: 'gates' | 'details' | 'clearance';
  readonly ok: boolean;
  readonly text: string;
}

export function clearanceValid(article: Article): boolean {
  const clearance = article.clearance;
  if (!clearance?.required) return true;
  return Boolean(clearance.contentSha256) && clearance.contentSha256 === article.contentSha256;
}

export function preflight(article: Article, gates: GatesView | undefined): PreflightItem[] {
  const items: PreflightItem[] = [];
  if (!gates?.report) {
    items.push({ id: 'gates', ok: false, text: 'The gates haven’t run on this version. Run them first.' });
  } else if (gates.stale) {
    items.push({ id: 'gates', ok: false, text: 'The text changed since the gates ran. Run them again.' });
  } else if (!gates.manualPublishReady) {
    const names = gates.blocking.map((gate) => GATES[gate].name).join(', ');
    items.push({ id: 'gates', ok: false, text: `${names} ${gates.blocking.length === 1 ? 'blocks' : 'block'} approval.` });
  } else {
    items.push({ id: 'gates', ok: true, text: 'Every gate passed or was overridden.' });
  }
  const missing = missingForPublish(article.meta);
  items.push(
    missing.length === 0
      ? { id: 'details', ok: true, text: 'The article details are complete.' }
      : { id: 'details', ok: false, text: `Missing details: ${missing.join(', ')}.` },
  );
  if (article.clearance?.required) {
    items.push(
      clearanceValid(article)
        ? { id: 'clearance', ok: true, text: 'You cleared this version for publication.' }
        : { id: 'clearance', ok: false, text: 'This version needs your clearance (case study or anonymized story).' },
    );
  }
  return items;
}

export function Preflight({ items, className }: { items: readonly PreflightItem[]; className?: string }) {
  return (
    <ul aria-label="Before approving" className={cn('grid gap-2 text-sm', className)}>
      {items.map((item) => (
        <li key={item.id} className="flex items-start gap-2">
          {item.ok ? (
            <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-success" />
          ) : (
            <CircleX aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
          )}
          <span className={item.ok ? 'text-ink-muted' : 'text-ink'}>
            <span className="sr-only">{item.ok ? 'Done: ' : 'Needed: '}</span>
            {item.text}
          </span>
        </li>
      ))}
    </ul>
  );
}
