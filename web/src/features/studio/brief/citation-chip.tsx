import { ShieldCheck } from 'lucide-react';

import { cn } from '@/lib/cn';

import { formatPartialDate, nonEmpty } from '../piece/format';

import type { Citation } from './brief-model';

/** Only links the research recorded as https are drawn as links. */
export function safeHref(url: string | null | undefined): string | undefined {
  return typeof url === 'string' && url.startsWith('https://') ? url : undefined;
}

/** "Microsoft Learn, Mar 2025": who published it and when, as short as a chip allows. */
export function sourceLabel(citation: Pick<Citation, 'publisher' | 'domain' | 'date'>): string {
  const who = nonEmpty(citation.publisher) ?? citation.domain;
  const when = formatPartialDate(citation.date);
  return when ? `${who}, ${when}` : who;
}

/**
 * A source chip: publisher (or domain) and date, opening the page in a new tab. Its accessible
 * name carries the page title too. A trusted (allowlisted) source shows a shield, with words for
 * screen readers.
 */
export function CitationChip({ citation, className }: { citation: Citation; className?: string }) {
  const href = safeHref(citation.url);
  const label = sourceLabel(citation);
  const name = `Source: ${citation.title}, ${label}${citation.allowlisted ? ', a trusted source' : ''}`;
  const chip = cn(
    'inline-flex max-w-full items-center gap-1 rounded-sm px-1.5 py-0.5 align-baseline text-xs font-medium',
    'bg-sunken text-ink-muted ring-1 ring-inset ring-line',
    className,
  );
  const content = (
    <>
      {citation.allowlisted ? <ShieldCheck aria-hidden="true" className="size-3 shrink-0 text-success" /> : null}
      <span className="truncate">{label}</span>
    </>
  );
  if (!href) {
    return (
      <span className={chip} aria-label={name}>
        {content}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`${name} (opens in a new tab)`}
      className={cn(chip, 'transition-colors duration-150 hover:bg-wash-strong hover:text-ink pointer-coarse:py-1.5')}
    >
      {content}
    </a>
  );
}

/** The chips of one finding, number or claim, in a row. */
export function CitationChips({ citations, className }: { citations: readonly Citation[]; className?: string }) {
  if (citations.length === 0) return null;
  return (
    <span className={cn('inline-flex flex-wrap gap-1.5', className)}>
      {citations.map((citation) => (
        <CitationChip key={citation.id} citation={citation} />
      ))}
    </span>
  );
}
