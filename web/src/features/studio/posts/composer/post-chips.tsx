import { ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/cn';
import { PILLAR_THREAD_CLASS, type PillarId } from '@/lib/pillars';

import { FORMAT_LABELS, LANE_LABELS } from '../model/labels';
import type { Lane, Pillar, PostFormat } from '../model/types';

const chipClass = cn(
  'inline-flex h-8 items-center gap-2 rounded-full border border-line-strong bg-sheet px-3 text-sm',
  'pointer-coarse:h-11',
);

function threadClass(pillarId: string): string | undefined {
  return Object.hasOwn(PILLAR_THREAD_CLASS, pillarId) ? PILLAR_THREAD_CLASS[pillarId as PillarId] : undefined;
}

/** A pillar's colour thread, always next to its name. */
function Thread({ pillarId }: { pillarId: string }) {
  const thread = threadClass(pillarId);
  return <span aria-hidden="true" className={cn('h-3.5 w-1 shrink-0 rounded-full', thread ?? 'bg-ink-subtle')} />;
}

function ChipMenu({
  label,
  value,
  current,
  disabled,
  children,
  onSelect,
  lead,
}: {
  label: string;
  value: string;
  current: string;
  disabled: boolean;
  children: ReactNode;
  onSelect: (value: string) => void;
  lead?: ReactNode;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        disabled={disabled}
        aria-label={`${label}: ${value}. Change the ${label.toLowerCase()}`}
        className={cn(chipClass, 'hover:bg-wash disabled:pointer-events-none disabled:opacity-60')}
      >
        <span className="text-ink-subtle">{label}</span>
        {lead}
        <span className="font-medium text-ink">{value}</span>
        <ChevronDown aria-hidden="true" className="size-3.5 text-ink-subtle" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={current} onValueChange={onSelect}>
          {children}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export interface PostChipsProps {
  lane: Lane;
  pillar: string;
  format: PostFormat;
  pillars: readonly Pillar[];
  disabled: boolean;
  onPillar: (pillar: string) => void;
  onFormat: (format: PostFormat) => void;
}

const EDITABLE_FORMATS: readonly PostFormat[] = ['text', 'carousel', 'image_card'];

/**
 * The post's lane (fixed), pillar and format as chips; pillar and format open a menu. A pillar
 * always shows its name next to its colour.
 */
export function PostChips({ lane, pillar, format, pillars, disabled, onPillar, onFormat }: PostChipsProps) {
  const pillarName = pillars.find((candidate) => candidate.id === pillar)?.name ?? pillar;
  return (
    <div role="group" aria-label="Lane, pillar and format" className="flex flex-wrap items-center gap-2">
      <span className={chipClass}>
        <span className="text-ink-subtle">Lane</span>
        <span className="font-medium text-ink">{LANE_LABELS[lane]}</span>
      </span>
      <ChipMenu
        label="Pillar"
        value={pillarName}
        current={pillar}
        disabled={disabled || pillars.length === 0}
        onSelect={onPillar}
        lead={<Thread pillarId={pillar} />}
      >
        {pillars.map((candidate) => (
          <DropdownMenuRadioItem key={candidate.id} value={candidate.id}>
            <Thread pillarId={candidate.id} />
            {candidate.name}
          </DropdownMenuRadioItem>
        ))}
      </ChipMenu>
      <ChipMenu
        label="Format"
        value={FORMAT_LABELS[format]}
        current={format}
        disabled={disabled}
        onSelect={(value) => {
          if ((EDITABLE_FORMATS as readonly string[]).includes(value)) onFormat(value as PostFormat);
        }}
      >
        {EDITABLE_FORMATS.map((candidate) => (
          <DropdownMenuRadioItem key={candidate} value={candidate}>
            {FORMAT_LABELS[candidate]}
          </DropdownMenuRadioItem>
        ))}
        <DropdownMenuRadioItem value="poll" disabled>
          {`${FORMAT_LABELS.poll} (written by hand for now)`}
        </DropdownMenuRadioItem>
      </ChipMenu>
    </div>
  );
}
