import type { Editor } from '@tiptap/core';
import { useEditorState } from '@tiptap/react';

import { PillarThread } from '@/components/ui/pillar-badge';
import { isPlanPillar, PILLAR_THREAD_CLASS } from '@/lib/pillars';

import { countWords, readingMinutes } from './model/document';
import { formatNumber, plural } from './model/format';
import type { FormatPack, Pillar } from './model/types';

/** The pillar the article belongs to: its thread colour, always with its name. */
export function ManuscriptPillar({ pillarId, pillars }: { pillarId: string | null | undefined; pillars: readonly Pillar[] }) {
  if (!pillarId) return null;
  const pillar = pillars.find((candidate) => candidate.id === pillarId);
  return (
    <p className="mb-6 flex items-center gap-2 font-sans text-sm text-ink-muted">
      <PillarThread id={pillarId} />
      <span>
        <span className="sr-only">Pillar: </span>
        {pillar?.name ?? pillarId}
      </span>
    </p>
  );
}

/** The vertical thread beside the manuscript, in the pillar's colour (decorative: the name says it). */
export function threadClass(pillarId: string | null | undefined): string {
  return isPlanPillar(pillarId) ? PILLAR_THREAD_CLASS[pillarId] : 'bg-line';
}

function lengthNote(words: number, pack: FormatPack | undefined): string | undefined {
  if (!pack) return undefined;
  const range = `${formatNumber(pack.minWords)}–${formatNumber(pack.maxWords)} words`;
  if (words < pack.minWords) return `${pack.name}: ${range}, ${formatNumber(pack.minWords - words)} to go.`;
  if (words > pack.maxWords) return `${pack.name}: ${range}, ${formatNumber(words - pack.maxWords)} over.`;
  return `${pack.name}: ${range}, within range.`;
}

/** Words, reading time and the format's length, counted like the published page counts them. */
export function ManuscriptFacts({ editor, pack }: { editor: Editor; pack: FormatPack | undefined }) {
  const words = useEditorState({ editor, selector: ({ editor: current }) => countWords(current.state.doc) });
  const minutes = readingMinutes(words);
  const note = lengthNote(words, pack);
  return (
    <p className="mt-10 flex flex-wrap gap-x-6 gap-y-1 border-t border-line pt-3 font-sans text-sm text-ink-subtle">
      <span>{plural(words, 'word', 'words')}</span>
      <span>{minutes === 0 ? 'Nothing to read yet' : `${plural(minutes, 'minute', 'minutes')} to read`}</span>
      {note ? <span>{note}</span> : null}
    </p>
  );
}
