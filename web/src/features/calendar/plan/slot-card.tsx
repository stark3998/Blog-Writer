import { CalendarClock, ExternalLink, MessageSquareQuote, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/cn';
import { pillarThreadClass } from '@/lib/pillars';

import { PillarTag } from '../components/pillar';
import { StatusBadge } from '../components/status-badge';
import { POST_STATUS } from '../lib/labels';
import { blockMessage, type DayOption, type PostSummary } from '../lib/plan';
import type { PillarView } from '../lib/settings';
import { dayLabel, utcToLocal } from '../lib/time';

import { postHref, type SlotModel } from './slot-model';

/** What the owner can do with a slot, shared by the desktop menu and the phone sheet. */
export interface SlotActions {
  readonly options: (model: SlotModel) => DayOption[];
  readonly onMove: (model: SlotModel, option: DayOption) => void;
  readonly onEdit: (model: SlotModel) => void;
  readonly onRemove: (model: SlotModel) => void;
  readonly takeHref: (model: SlotModel) => string;
}

/** "Under 24 hours from …" or the reason a day is taken, under a day's name in a move list. */
export function optionNote(option: DayOption): string | undefined {
  if (option.block) return blockMessage(option.block);
  return option.warnings[0]?.message;
}

interface SlotCardBodyProps {
  model: SlotModel;
  pillars: readonly PillarView[];
  /** The id of the element that names the slot (its title), for the menu's label. */
  titleId?: string;
  compact?: boolean;
  /** Before the time (the drag grip). */
  leading?: ReactNode;
  /** After the time (the actions menu), so the words keep the card's whole width. */
  trailing?: ReactNode;
}

/** The slot's words: time, title, lane and format, pillar, and the state of its post. */
export function SlotCardBody({ model, pillars, titleId, compact = false, leading, trailing }: SlotCardBodyProps) {
  return (
    <div className="flex min-w-0 gap-2.5">
      <span aria-hidden="true" className={cn('w-1 shrink-0 self-stretch rounded-full', pillarThreadClass(model.slot.pillar))} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-h-6 items-center gap-1">
          {leading}
          <p className="min-w-0 flex-1 text-xs tabular-nums text-ink-muted">
            <time dateTime={`${model.date}T${model.time}`}>{model.time}</time> {model.zoneName}
          </p>
          {trailing}
        </div>
        <p id={titleId} className={cn('font-medium text-ink', compact ? 'line-clamp-3 text-sm' : 'text-base')}>
          {model.title}
        </p>
        <p className="text-xs text-ink-muted">{model.kindLabel}</p>
        <PillarTag pillarId={model.slot.pillar} pillars={pillars} className="text-xs text-ink-muted" />
        <div className="pt-0.5">
          <StatusBadge status={model.state} busy={model.busy} />
        </div>
      </div>
    </div>
  );
}

interface SlotMenuProps {
  model: SlotModel;
  actions: SlotActions;
  weekStart: string;
}

/** The slot's actions: "Move to" each day (with its block or warning), edit, open, remove. */
export function SlotMenu({ model, actions }: SlotMenuProps) {
  const options = model.movable ? actions.options(model).filter((option) => !option.current) : [];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`Actions for the ${model.label} slot: ${model.title}`}>
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        {options.length > 0 ? (
          <DropdownMenuGroup>
            <DropdownMenuLabel>Move to</DropdownMenuLabel>
            {options.map((option) => {
              const note = optionNote(option);
              return (
                <DropdownMenuItem
                  key={option.date}
                  disabled={option.block !== undefined}
                  onSelect={() => {
                    actions.onMove(model, option);
                  }}
                >
                  <CalendarClock aria-hidden="true" />
                  <span className="grid min-w-0">
                    <span>{`${dayLabel(option.date)}, ${option.time}`}</span>
                    {note ? (
                      <span className={cn('text-xs', option.block ? 'text-ink-subtle' : 'text-warn')}>{note}</span>
                    ) : null}
                  </span>
                </DropdownMenuItem>
              );
            })}
          </DropdownMenuGroup>
        ) : (
          <DropdownMenuLabel>{model.movable ? 'No other day is free' : 'This slot can’t move now'}</DropdownMenuLabel>
        )}
        <DropdownMenuSeparator />
        {model.post ? (
          <DropdownMenuItem asChild>
            <Link to={postHref(model.post)}>
              <ExternalLink aria-hidden="true" />
              Open the post
            </Link>
          </DropdownMenuItem>
        ) : null}
        {model.needsTake ? (
          <DropdownMenuItem asChild>
            <Link to={actions.takeHref(model)}>
              <MessageSquareQuote aria-hidden="true" />
              Write your take
            </Link>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem
          disabled={!model.movable && !model.contentEditable}
          onSelect={() => {
            actions.onEdit(model);
          }}
        >
          <Pencil aria-hidden="true" />
          Edit the slot…
        </DropdownMenuItem>
        <DropdownMenuItem
          variant="danger"
          disabled={!model.removable}
          onSelect={() => {
            actions.onRemove(model);
          }}
        >
          <Trash2 aria-hidden="true" />
          {model.removable ? 'Remove the slot…' : 'Remove (a post fills it)'}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

interface OutsidePostProps {
  post: PostSummary;
  zone: string;
  pillars: readonly PillarView[];
}

/** A post with a slot this week that the plan does not hold (a quick take, a derivative). */
export function OutsidePostCard({ post, zone, pillars }: OutsidePostProps) {
  const time = post.slot ? utcToLocal(Date.parse(post.slot.utc), zone).time : undefined;
  return (
    <div className="rounded-md border border-dashed border-line-strong p-2.5">
      <div className="flex min-w-0 gap-2.5">
        <span aria-hidden="true" className={cn('w-1 shrink-0 self-stretch rounded-full', pillarThreadClass(post.pillar))} />
        <div className="min-w-0 flex-1 space-y-1">
          {time ? <p className="text-xs tabular-nums text-ink-muted">{time}</p> : null}
          <p className="line-clamp-3 text-sm font-medium text-ink">
            <Link to={postHref(post)} className="underline decoration-line-strong underline-offset-4 hover:decoration-ink">
              {post.hook.trim() || 'A post outside the plan'}
            </Link>
          </p>
          <p className="text-xs text-ink-muted">Outside the plan</p>
          <PillarTag pillarId={post.pillar} pillars={pillars} className="text-xs text-ink-muted" />
          <div className="pt-0.5">
            <StatusBadge status={POST_STATUS[post.status]} busy={post.status === 'generating'} />
          </div>
        </div>
      </div>
    </div>
  );
}
