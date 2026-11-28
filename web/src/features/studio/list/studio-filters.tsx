import { Search } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { SegmentedControl, SegmentedControlItem } from '@/components/ui/segmented-control';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/cn';

import { pillarView } from '../piece/pillars';
import type { Pillar } from '../piece/types';

import { KIND_OPTIONS, SHOW_OPTIONS, type KindFilter, type ShowFilter } from './list-model';

export interface StudioFiltersProps {
  show: ShowFilter;
  kind: KindFilter;
  pillar: string | undefined;
  search: string;
  pillars: readonly Pillar[] | undefined;
  onShowChange: (show: ShowFilter) => void;
  onKindChange: (kind: KindFilter) => void;
  onPillarChange: (pillar: string | undefined) => void;
  onSearchChange: (search: string) => void;
}

/**
 * The list's filters: a title search, which pieces (in progress, live, archived, all), articles or
 * posts, and a pillar (a toggle: press it again for every pillar). They live in the URL, so a
 * filtered list can be bookmarked and Back restores it.
 */
export function StudioFilters({
  show,
  kind,
  pillar,
  search,
  pillars,
  onShowChange,
  onKindChange,
  onPillarChange,
  onSearchChange,
}: StudioFiltersProps) {
  const pillarList = pillars ?? [];
  return (
    <div role="search" aria-label="Filter pieces" className="grid gap-3">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative lg:w-72">
          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-subtle" />
          <Input
            type="search"
            aria-label="Search pieces by title"
            placeholder="Search by title"
            value={search}
            className="pl-9"
            onChange={(event) => {
              onSearchChange(event.target.value);
            }}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl
            aria-label="Which pieces"
            value={show}
            onValueChange={(value) => {
              const next = SHOW_OPTIONS.find((option) => option.id === value);
              if (next) onShowChange(next.id);
            }}
          >
            {SHOW_OPTIONS.map((option) => (
              <SegmentedControlItem key={option.id} value={option.id}>
                {option.label}
              </SegmentedControlItem>
            ))}
          </SegmentedControl>
          <SegmentedControl
            aria-label="Kind of piece"
            value={kind}
            onValueChange={(value) => {
              const next = KIND_OPTIONS.find((option) => option.id === value);
              if (next) onKindChange(next.id);
            }}
          >
            {KIND_OPTIONS.map((option) => (
              <SegmentedControlItem key={option.id} value={option.id}>
                {option.label}
              </SegmentedControlItem>
            ))}
          </SegmentedControl>
        </div>
      </div>
      {pillarList.length > 0 ? (
        <ToggleGroup
          type="single"
          aria-label="Pillar"
          variant="outline"
          size="sm"
          value={pillar ?? ''}
          onValueChange={(value) => {
            onPillarChange(value === '' ? undefined : value);
          }}
        >
          {pillarList.map((item) => {
            const view = pillarView(item.id, pillarList);
            return (
              <ToggleGroupItem key={item.id} value={item.id}>
                <span aria-hidden="true" className={cn('size-2 rounded-full', view?.thread)} />
                {item.name}
              </ToggleGroupItem>
            );
          })}
        </ToggleGroup>
      ) : null}
    </div>
  );
}
