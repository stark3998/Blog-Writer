import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { Ban, GripVertical, TriangleAlert } from 'lucide-react';
import { useId, useState } from 'react';

import { cn } from '@/lib/cn';

import { blockMessage, type DayOption, type Milestone, type PostSummary } from '../lib/plan';
import type { PillarView } from '../lib/settings';
import { dayLabel, shortDate, weekdayShort } from '../lib/time';

import { OutsidePostCard, SlotCardBody, SlotMenu, type SlotActions } from './slot-card';
import type { SlotModel } from './slot-model';

export interface DayModel {
  readonly date: string;
  readonly slots: readonly SlotModel[];
  readonly outside: readonly PostSummary[];
  readonly milestones: readonly Milestone[];
  readonly isToday: boolean;
  readonly isPast: boolean;
}

interface WeekBoardProps {
  days: readonly DayModel[];
  zone: string;
  weekStart: string;
  pillars: readonly PillarView[];
  actions: SlotActions;
  /** Slots that findings point at, outlined. */
  flagged: ReadonlySet<string>;
}

const DAY_PREFIX = 'day:';

function dropDate(id: string | number | undefined): string | undefined {
  return typeof id === 'string' && id.startsWith(DAY_PREFIX) ? id.slice(DAY_PREFIX.length) : undefined;
}

/**
 * The week as seven columns (desktop). Slots drag between days with a mouse or pen
 * (`@dnd-kit/core`); while one moves, every day says whether it can take it: "Taken" (a post
 * already goes out that day: never two in one day), "Passed", or a warning when it would land
 * under 24 hours from another post. Keyboard and touch use each slot's "Move to" menu instead.
 */
export function WeekBoard({ days, zone, weekStart, pillars, actions, flagged }: WeekBoardProps) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const [activeId, setActiveId] = useState<string | null>(null);
  const models = days.flatMap((day) => day.slots);
  const active = activeId ? models.find((model) => model.slot.id === activeId) : undefined;
  const options = active ? actions.options(active) : undefined;
  const optionFor = (date: string | undefined) => options?.find((option) => option.date === date);

  const describe = (option: DayOption | undefined, date: string | undefined): string => {
    if (!date) return 'Not over a day.';
    if (!option) return `${dayLabel(date)}.`;
    if (option.current) return `${dayLabel(date)}, where it is now.`;
    if (option.block) return `${dayLabel(date)}: ${blockMessage(option.block)}.`;
    const warning = option.warnings[0];
    return `${dayLabel(date)}, ${option.time}${warning ? `. ${warning.message}` : ''}.`;
  };

  const announcements: Announcements = {
    onDragStart: ({ active: dragged }) => {
      const model = models.find((candidate) => candidate.slot.id === String(dragged.id));
      return model ? `Picked up the ${model.label} slot: ${model.title}.` : 'Picked up a slot.';
    },
    onDragOver: ({ over }) => {
      const date = dropDate(over?.id);
      return describe(optionFor(date), date);
    },
    onDragEnd: ({ over }) => {
      const option = optionFor(dropDate(over?.id));
      if (!option || option.block || option.current) return 'Dropped. Nothing moved.';
      return `Moving to ${dayLabel(option.date)}, ${option.time}.`;
    },
    onDragCancel: () => 'Cancelled. Nothing moved.',
  };

  const onDragStart = ({ active: dragged }: DragStartEvent) => {
    setActiveId(String(dragged.id));
  };

  const onDragEnd = ({ over }: DragEndEvent) => {
    const option = optionFor(dropDate(over?.id));
    const moving = active;
    setActiveId(null);
    if (!moving || !option || option.block || option.current) return;
    actions.onMove(moving, option);
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => {
        setActiveId(null);
      }}
      accessibility={{
        announcements,
        screenReaderInstructions: {
          draggable: 'To move a slot with the keyboard, open its actions and choose a day under Move to.',
        },
      }}
    >
      {/* Columns never get narrower than a card can be read at; narrow windows scroll sideways. */}
      <div className="overflow-x-auto">
        <ol aria-label="Days of the week" className="grid grid-cols-[repeat(7,minmax(8.5rem,1fr))] border-y border-line">
          {days.map((day) => (
            <DayColumn
            key={day.date}
            day={day}
            zone={zone}
            weekStart={weekStart}
            pillars={pillars}
            actions={actions}
            flagged={flagged}
            dragging={active !== undefined}
            option={optionFor(day.date)}
            activeId={activeId}
            />
          ))}
        </ol>
      </div>
      <DragOverlay dropAnimation={null}>
        {active ? (
          <div className="w-44 rounded-md border border-line-strong bg-raised p-2.5 shadow-overlay">
            <SlotCardBody model={active} pillars={pillars} compact />
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}

interface DayColumnProps {
  day: DayModel;
  zone: string;
  weekStart: string;
  pillars: readonly PillarView[];
  actions: SlotActions;
  flagged: ReadonlySet<string>;
  dragging: boolean;
  option: DayOption | undefined;
  activeId: string | null;
}

function DayColumn({ day, zone, weekStart, pillars, actions, flagged, dragging, option, activeId }: DayColumnProps) {
  const headingId = useId();
  const blocked = option?.block !== undefined;
  const { setNodeRef, isOver } = useDroppable({ id: `${DAY_PREFIX}${day.date}`, disabled: !dragging || blocked });
  const empty = day.slots.length === 0 && day.outside.length === 0;
  return (
    <li
      ref={setNodeRef}
      aria-labelledby={headingId}
      className={cn(
        'relative flex min-h-64 min-w-0 flex-col gap-2 border-l border-line p-2 first:border-l-0',
        day.isPast && 'bg-sunken',
        dragging && !blocked && !option?.current && 'bg-wash',
        dragging && blocked && 'bg-sunken',
        isOver && 'bg-wash-strong outline-2 -outline-offset-2 outline-ink',
      )}
    >
      <h3 id={headingId} className="flex items-baseline justify-between gap-1 px-1 pb-1">
        <span className="text-sm font-semibold text-ink">
          {weekdayShort(day.date)} <span className="font-normal text-ink-muted">{shortDate(day.date)}</span>
        </span>
        {day.isToday ? <span className="text-xs font-medium text-ink">Today</span> : null}
      </h3>

      {dragging && option ? <DropHint option={option} /> : null}

      {day.milestones.map((milestone) => (
        <p key={milestone.id} className="px-1 text-xs text-ink-muted">
          <span className="tabular-nums">{milestone.time}</span> {milestone.label}
        </p>
      ))}

      {day.slots.map((model) => (
        <DraggableSlot
          key={model.slot.id}
          model={model}
          pillars={pillars}
          actions={actions}
          weekStart={weekStart}
          flagged={flagged.has(model.slot.id)}
          hidden={activeId === model.slot.id}
        />
      ))}

      {day.outside.map((post) => (
        <OutsidePostCard key={post.id} post={post} zone={zone} pillars={pillars} />
      ))}

      {empty && !dragging ? <p className="px-1 pt-2 text-xs text-ink-subtle">No post</p> : null}
    </li>
  );
}

function DropHint({ option }: { option: DayOption }) {
  if (option.current) return <p className="px-1 text-xs text-ink-muted">Where it is now</p>;
  if (option.block) {
    return (
      <p className="flex items-start gap-1 px-1 text-xs font-medium text-danger">
        <Ban aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        {option.block.reason === 'past' ? 'Passed' : 'Taken: one post a day'}
      </p>
    );
  }
  const warning = option.warnings[0];
  if (warning) {
    return (
      <p className="flex items-start gap-1 px-1 text-xs font-medium text-warn">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        {`Under 24 h: ${option.time}`}
      </p>
    );
  }
  return <p className="px-1 text-xs font-medium text-ink">{`Drop for ${option.time}`}</p>;
}

interface DraggableSlotProps {
  model: SlotModel;
  pillars: readonly PillarView[];
  actions: SlotActions;
  weekStart: string;
  flagged: boolean;
  hidden: boolean;
}

function DraggableSlot({ model, pillars, actions, weekStart, flagged, hidden }: DraggableSlotProps) {
  const titleId = useId();
  // Pointer only: no `attributes` (role, tab stop), since keyboards move slots with the menu.
  const { setNodeRef, listeners } = useDraggable({ id: model.slot.id, disabled: !model.movable });
  return (
    <article
      ref={setNodeRef}
      aria-labelledby={titleId}
      data-slot-id={model.slot.id}
      className={cn(
        'relative rounded-md border bg-sheet p-2 transition-opacity duration-150',
        flagged ? 'border-warn' : 'border-line',
        // Mouse and pen drag; touch screens scroll as usual and move slots with the menu.
        model.movable && 'pointer-fine:cursor-grab pointer-fine:touch-none pointer-fine:active:cursor-grabbing',
        hidden && 'opacity-40',
      )}
      {...(model.movable ? listeners : {})}
    >
      <SlotCardBody
        model={model}
        pillars={pillars}
        titleId={titleId}
        compact
        leading={
          model.movable ? (
            <GripVertical aria-hidden="true" className="-ml-1 size-3.5 shrink-0 text-ink-subtle pointer-coarse:hidden" />
          ) : null
        }
        trailing={<SlotMenu model={model} actions={actions} weekStart={weekStart} />}
      />
    </article>
  );
}
