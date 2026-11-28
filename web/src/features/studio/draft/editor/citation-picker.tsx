import { BadgeCheck } from 'lucide-react';

import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';

import type { BriefCitation } from '../model/types';

export interface CitationPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The brief's sources; `undefined` while the brief loads or when there is none. */
  citations: readonly BriefCitation[] | undefined;
  /** Why there are no sources to show. */
  emptyReason: string;
  onPick: (citation: BriefCitation) => void;
}

/**
 * Cite a source from the research brief: a citation is `[^source-id]` in the text plus the
 * source's definition in the sources list (added when the article has none yet).
 */
export function CitationPicker({ open, onOpenChange, citations, emptyReason, onPick }: CitationPickerProps) {
  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Cite a source"
      description="Choose a source from the research brief."
      label="Sources in the brief"
    >
      <CommandInput placeholder="Search the brief’s sources" />
      <CommandList>
        <CommandEmpty>{citations && citations.length > 0 ? 'No source matches.' : emptyReason}</CommandEmpty>
        {citations && citations.length > 0 ? (
          <CommandGroup heading="Sources in the brief">
            {citations.map((citation) => (
              <CommandItem
                key={citation.id}
                value={`${citation.id} ${citation.title} ${citation.publisher ?? ''} ${citation.domain}`}
                onSelect={() => {
                  onPick(citation);
                  onOpenChange(false);
                }}
              >
                <span className="grid min-w-0 gap-0.5">
                  <span className="truncate font-medium text-ink">{citation.title}</span>
                  <span className="flex flex-wrap items-center gap-x-2 text-xs text-ink-subtle">
                    <span>{citation.publisher ?? citation.domain}</span>
                    {citation.publisher ? <span>{citation.domain}</span> : null}
                    {citation.date ? <span>{citation.date}</span> : null}
                    {citation.allowlisted ? (
                      <span className="inline-flex items-center gap-1 text-success">
                        <BadgeCheck aria-hidden="true" className="size-3" />
                        Allowed domain
                      </span>
                    ) : null}
                  </span>
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        ) : null}
      </CommandList>
    </CommandDialog>
  );
}
