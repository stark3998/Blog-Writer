import { ChevronRight, ExternalLink, MessageSquareQuote, Pencil, Trash2 } from 'lucide-react';
import { useId, useRef, useState } from 'react';
import { Link } from 'react-router';

import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { cn } from '@/lib/cn';

import { PillarTag } from '../components/pillar';
import { attributeSelector } from '../lib/dom';
import { StatusBadge } from '../components/status-badge';
import type { PillarView } from '../lib/settings';
import { dayLabel, longDayLabel } from '../lib/time';

import { optionNote, OutsidePostCard, SlotCardBody, type SlotActions } from './slot-card';
import { postHref, type SlotModel } from './slot-model';
import type { DayModel } from './week-board';

interface WeekListProps {
  days: readonly DayModel[];
  zone: string;
  pillars: readonly PillarView[];
  actions: SlotActions;
  flagged: ReadonlySet<string>;
}

/**
 * The week as a list of days (phones, one hand). Each slot is one whole-row target that opens a
 * sheet from the bottom with its actions: "Move to" each day (taken and passed days say why
 * they can't take it), edit, open the post, write the take, remove.
 */
export function WeekList({ days, zone, pillars, actions, flagged }: WeekListProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // After a move the row sits in another day: focus it there when the sheet closes. For anything
  // else the sheet gives focus back to the row that opened it.
  const movedId = useRef<string | null>(null);
  const selected = days.flatMap((day) => day.slots).find((model) => model.slot.id === selectedId);
  return (
    <>
      <ol aria-label="Days of the week" className="border-t border-line">
        {days.map((day) => (
          <DayRow
            key={day.date}
            day={day}
            zone={zone}
            pillars={pillars}
            flagged={flagged}
            onSelect={(model) => {
              movedId.current = null;
              setSelectedId(model.slot.id);
            }}
          />
        ))}
      </ol>
      <SlotSheet
        model={selected}
        pillars={pillars}
        actions={actions}
        onClose={() => {
          setSelectedId(null);
        }}
        onMoved={(id) => {
          movedId.current = id;
        }}
        onCloseAutoFocus={(event) => {
          const id = movedId.current;
          if (!id) return;
          event.preventDefault();
          requestAnimationFrame(() => {
            document.querySelector<HTMLElement>(attributeSelector('data-slot-row', id))?.focus();
          });
        }}
      />
    </>
  );
}

interface DayRowProps {
  day: DayModel;
  zone: string;
  pillars: readonly PillarView[];
  flagged: ReadonlySet<string>;
  onSelect: (model: SlotModel) => void;
}

function DayRow({ day, zone, pillars, flagged, onSelect }: DayRowProps) {
  const headingId = useId();
  const empty = day.slots.length === 0 && day.outside.length === 0;
  return (
    <li aria-labelledby={headingId} className={cn('border-b border-line py-3', day.isPast && 'text-ink-muted')}>
      <h3 id={headingId} className="flex items-baseline justify-between gap-2 text-sm font-semibold text-ink">
        {longDayLabel(day.date)}
        {day.isToday ? <span className="text-xs font-medium">Today</span> : null}
      </h3>
      {day.milestones.length > 0 ? (
        <ul className="mt-1 space-y-0.5">
          {day.milestones.map((milestone) => (
            <li key={milestone.id} className="text-xs text-ink-muted">
              <span className="tabular-nums">{milestone.time}</span> {milestone.label}
            </li>
          ))}
        </ul>
      ) : null}
      {day.slots.length > 0 || day.outside.length > 0 ? (
        <ul className="mt-2 space-y-2">
          {day.slots.map((model) => (
            <li key={model.slot.id}>
              <button
                type="button"
                data-slot-row={model.slot.id}
                onClick={() => {
                  onSelect(model);
                }}
                className={cn(
                  'flex min-h-11 w-full items-center gap-2 rounded-md border bg-sheet p-3 text-left',
                  'transition-colors duration-150 hover:bg-wash',
                  flagged.has(model.slot.id) ? 'border-warn' : 'border-line',
                )}
              >
                <span className="min-w-0 flex-1">
                  <SlotCardBody model={model} pillars={pillars} />
                </span>
                <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
              </button>
            </li>
          ))}
          {day.outside.map((post) => (
            <li key={post.id}>
              <OutsidePostCard post={post} zone={zone} pillars={pillars} />
            </li>
          ))}
        </ul>
      ) : null}
      {empty ? <p className="mt-1 text-sm text-ink-subtle">No post</p> : null}
    </li>
  );
}

interface SlotSheetProps {
  model: SlotModel | undefined;
  pillars: readonly PillarView[];
  actions: SlotActions;
  onClose: () => void;
  onMoved: (slotId: string) => void;
  onCloseAutoFocus: (event: Event) => void;
}

function SlotSheet({ model: shown, pillars, actions, onClose, onMoved, onCloseAutoFocus }: SlotSheetProps) {
  const options = shown?.movable ? actions.options(shown).filter((option) => !option.current) : [];
  const moveHeadingId = useId();

  return (
    <Sheet
      open={shown !== undefined}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent
        side="bottom"
        className="overflow-y-auto"
        onCloseAutoFocus={onCloseAutoFocus}
      >
        {shown ? (
          <>
            <SheetHeader>
              <SheetTitle>{shown.title}</SheetTitle>
              <SheetDescription>{`${shown.label} ${shown.zoneName}. ${shown.kindLabel}.`}</SheetDescription>
            </SheetHeader>
            <div className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
              <PillarTag pillarId={shown.slot.pillar} pillars={pillars} />
              <StatusBadge status={shown.state} busy={shown.busy} />
            </div>
            {shown.findings.length > 0 ? (
              <ul className="space-y-1 text-sm text-warn">
                {shown.findings.map((finding) => (
                  <li key={`${finding.code}-${finding.ref ?? ''}`}>{finding.message}</li>
                ))}
              </ul>
            ) : null}

            {shown.movable ? (
              <section aria-labelledby={moveHeadingId}>
                <h3 id={moveHeadingId} className="text-sm font-semibold text-ink">
                  Move to
                </h3>
                {options.length === 0 ? (
                  <p className="mt-1 text-sm text-ink-muted">No other day this week is free.</p>
                ) : (
                  <ul className="mt-2 grid gap-2">
                    {options.map((option) => {
                      const note = optionNote(option);
                      const blocked = option.block !== undefined;
                      return (
                        <li key={option.date}>
                          <button
                            type="button"
                            aria-disabled={blocked || undefined}
                            onClick={() => {
                              if (blocked) return;
                              onMoved(shown.slot.id);
                              onClose();
                              actions.onMove(shown, option);
                            }}
                            className={cn(
                              'flex min-h-11 w-full flex-col items-start justify-center rounded-md border border-line px-3 py-2 text-left',
                              blocked ? 'cursor-not-allowed bg-sunken text-ink-subtle' : 'bg-sheet hover:bg-wash',
                            )}
                          >
                            <span className={cn('font-medium', blocked ? 'text-ink-muted' : 'text-ink')}>
                              {`${dayLabel(option.date)}, ${option.time}`}
                            </span>
                            {note ? (
                              <span className={cn('text-xs', blocked ? 'text-ink-subtle' : 'text-warn')}>{note}</span>
                            ) : null}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            ) : (
              <p className="text-sm text-ink-muted">This slot can’t move now: its post is live or on its way out.</p>
            )}

            <div className="grid gap-2">
              {shown.post ? (
                <Button asChild variant="secondary">
                  <Link to={postHref(shown.post)}>
                    <ExternalLink aria-hidden="true" />
                    Open the post
                  </Link>
                </Button>
              ) : null}
              {shown.needsTake ? (
                <Button asChild>
                  <Link to={actions.takeHref(shown)}>
                    <MessageSquareQuote aria-hidden="true" />
                    Write your take
                  </Link>
                </Button>
              ) : null}
              {shown.movable || shown.contentEditable ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    onClose();
                    actions.onEdit(shown);
                  }}
                >
                  <Pencil aria-hidden="true" />
                  Edit the slot
                </Button>
              ) : null}
              {shown.removable ? (
                <Button
                  variant="ghost"
                  className="text-danger"
                  onClick={() => {
                    onClose();
                    actions.onRemove(shown);
                  }}
                >
                  <Trash2 aria-hidden="true" />
                  Remove the slot
                </Button>
              ) : null}
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
