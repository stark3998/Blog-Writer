import {
  Archive,
  CalendarClock,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Clock,
  FileText,
  Loader,
  Package,
  PenLine,
  Send,
  TimerOff,
  Trash2,
  type LucideIcon,
} from 'lucide-react';

import { Badge, type BadgeProps } from '@/components/ui/badge';
import { cn } from '@/lib/cn';

import { KIT_STATE_LABELS, POST_STATUS_LABELS } from '../model/labels';
import type { KitState, PostStatus } from '../model/types';

type Tone = NonNullable<BadgeProps['variant']>;

const STATUS_LOOK: Readonly<Record<PostStatus, { tone: Tone; icon: LucideIcon }>> = {
  generating: { tone: 'neutral', icon: Loader },
  draft: { tone: 'neutral', icon: PenLine },
  approved: { tone: 'outline', icon: CircleCheck },
  staged: { tone: 'success', icon: Clock },
  publishing: { tone: 'neutral', icon: Send },
  posted: { tone: 'success', icon: CircleCheck },
  kit: { tone: 'outline', icon: Package },
  needs_attention: { tone: 'warn', icon: CircleAlert },
  missed: { tone: 'warn', icon: TimerOff },
  expired: { tone: 'neutral', icon: TimerOff },
  deleted: { tone: 'neutral', icon: Trash2 },
  archived: { tone: 'neutral', icon: Archive },
};

/** A post's status as a badge: the glossary word with an icon, never colour alone. */
export function PostStatusBadge({ status, className }: { status: PostStatus; className?: string }) {
  const { tone, icon: Icon } = STATUS_LOOK[status];
  return (
    <Badge variant={tone} className={className} data-status={status}>
      <Icon aria-hidden="true" />
      {POST_STATUS_LABELS[status]}
    </Badge>
  );
}

const KIT_LOOK: Readonly<Record<KitState, LucideIcon>> = {
  prepared: Package,
  native_draft_saved: FileText,
  natively_scheduled: CalendarClock,
  posted: CircleCheck,
};

/** The kit's progress as a badge. */
export function KitStateBadge({ state, className }: { state: KitState; className?: string }) {
  const Icon = KIT_LOOK[state];
  return (
    <Badge variant={state === 'posted' ? 'success' : 'outline'} className={cn(className)}>
      <Icon aria-hidden="true" />
      {KIT_STATE_LABELS[state]}
    </Badge>
  );
}

/** A small spinner icon for "working" lines (decorative; the words say it). */
export function WorkingIcon({ className }: { className?: string }) {
  return <CircleDashed aria-hidden="true" className={cn('size-4 shrink-0 text-ink-subtle', className)} />;
}
