import { useId } from 'react';
import { Link } from 'react-router';

import { cn } from '@/lib/cn';

import { formatDateTime, formatSlot } from '../piece/format';
import { PillarBadge, StatusBadge } from '../piece/piece-chips';
import { LANE_LABEL, piecePath, POST_FORMAT_LABEL } from '../piece/piece-status';
import { formatName, pillarView } from '../piece/pillars';
import type { FormatPack, Pillar } from '../piece/types';

import type { ListPiece } from './list-model';

export interface PieceRowProps {
  piece: ListPiece;
  pillars: readonly Pillar[] | undefined;
  packs: readonly FormatPack[] | undefined;
  /** Called when the row is pointed at or focused (to load the piece ahead of the click). */
  onIntent?: (piece: ListPiece) => void;
}

/** What kind of piece it is, in words: "Deep explainer article", "Quick take, carousel". */
function kindPhrase(piece: ListPiece, packs: readonly FormatPack[] | undefined): string {
  if (piece.kind === 'article') {
    const format = formatName(piece.format, packs);
    return format ? `${format} article` : 'Article';
  }
  const lane = piece.lane ? LANE_LABEL[piece.lane] : 'Post';
  const format = piece.postFormat ? POST_FORMAT_LABEL[piece.postFormat].toLowerCase() : undefined;
  return format ? `${lane}, ${format}` : lane;
}

/** The one time that matters most for the row: when it publishes or posts, or else its last change. */
function timeFact(piece: ListPiece): { label: string; iso: string; text: string } | undefined {
  const facts = piece.facts;
  if (facts.kind === 'article' && facts.status === 'approved' && facts.publishAt) {
    const text = formatDateTime(facts.publishAt);
    return text ? { label: 'Publishes', iso: facts.publishAt, text } : undefined;
  }
  if (facts.kind === 'post' && (facts.status === 'approved' || facts.status === 'staged') && facts.slot) {
    const text = formatSlot(facts.slot);
    return text ? { label: 'Slot', iso: facts.slot.utc, text } : undefined;
  }
  if (facts.kind === 'post' && facts.status === 'posted' && facts.postedAt) {
    const text = formatDateTime(facts.postedAt);
    return text ? { label: 'Posted', iso: facts.postedAt, text } : undefined;
  }
  const text = formatDateTime(piece.updatedAt);
  return text ? { label: 'Updated', iso: piece.updatedAt, text } : undefined;
}

/**
 * One piece in the Studio list. The whole row is the link (to the tab of its next step); its
 * name is the title, its description the facts and the status. The pillar's thread runs down the
 * row's left edge.
 */
export function PieceRow({ piece, pillars, packs, onIntent }: PieceRowProps) {
  const titleId = useId();
  const factsId = useId();
  const statusId = useId();
  const pillar = pillarView(piece.pillar, pillars);
  const time = timeFact(piece);
  const intent = onIntent
    ? () => {
        onIntent(piece);
      }
    : undefined;

  return (
    <li>
      <Link
        to={piecePath(piece.facts)}
        aria-labelledby={titleId}
        aria-describedby={`${factsId} ${statusId}`}
        onPointerEnter={intent}
        onFocus={intent}
        data-kind={piece.kind}
        className={cn(
          'grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 rounded-md px-3 py-4 transition-colors duration-150',
          'hover:bg-wash sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center',
          '-mx-3',
        )}
      >
        <span
          aria-hidden="true"
          className={cn('row-span-2 w-1 self-stretch rounded-full sm:row-span-1', pillar?.thread ?? 'bg-line-strong')}
        />
        <span className="min-w-0">
          <span
            id={titleId}
            className={cn(
              'block font-serif text-lg font-medium leading-snug',
              piece.untitled ? 'italic text-ink-muted' : 'text-ink',
            )}
          >
            {piece.title}
          </span>
          <span id={factsId} className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-muted">
            <span>{kindPhrase(piece, packs)}</span>
            {pillar ? <PillarBadge pillar={pillar} /> : null}
            {piece.reviewOnly ? <span className="text-warn">Review only</span> : null}
            {time ? (
              <span>
                {`${time.label} `}
                <time dateTime={time.iso}>{time.text}</time>
              </span>
            ) : null}
          </span>
        </span>
        <span className="col-start-2 sm:col-start-3">
          <StatusBadge id={statusId} piece={piece.facts} />
        </span>
      </Link>
    </li>
  );
}
