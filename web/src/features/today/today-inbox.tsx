import { useQueryClient } from '@tanstack/react-query';
import { AlarmClock, ChevronRight, Clock, MessageSquareReply, Mic, Send, Stamp } from 'lucide-react';
import { useRef } from 'react';
import { Link } from 'react-router';

import { DetailList, type DetailItem } from '@/components/shell/detail-list';
import { EmptyState } from '@/components/shell/empty-state';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ProblemAlert } from '@/features/data/problem-alert';
import { useApiMutation } from '@/lib/query/api-query';
import { queryKeys } from '@/lib/query/keys';
import { cn } from '@/lib/cn';

import {
  groupSummary,
  headline,
  itemTarget,
  kindMeta,
  priorityWord,
  splitInbox,
  type Today,
  type TodayItem,
} from './lib/items';
import { dueLabel, type DueTone } from './lib/time';
import { useNow } from '@/lib/use-now';

/** The four kinds of item, for the empty inbox. */
const KINDS: readonly DetailItem[] = [
  {
    id: 'approve',
    term: 'Approve',
    icon: Stamp,
    detail: 'Next week’s plan on Sunday evening, and each draft and post when it is ready for review.',
  },
  {
    id: 'answer',
    term: 'Answer',
    icon: Mic,
    detail: 'Micro-interviews: two short questions per piece, under three minutes. Dictation works.',
  },
  { id: 'publish', term: 'Publish', icon: Send, detail: 'One tap at each post slot. Nothing reaches LinkedIn until you tap.' },
  {
    id: 'reply',
    term: 'Reply',
    icon: MessageSquareReply,
    detail: 'Reminders 15 minutes, 1 hour and 3 hours after a post goes live.',
  },
];

const DUE_TONE: Readonly<Record<DueTone, string>> = {
  overdue: 'text-danger',
  soon: 'text-warn',
  later: 'text-ink-muted',
};

function Due({ dueAt, now }: { dueAt: string | null | undefined; now: number }) {
  const due = dueLabel(dueAt, now);
  if (!due || !dueAt) return null;
  const Icon = due.tone === 'later' ? Clock : AlarmClock;
  return (
    <p className={cn('flex items-center gap-1.5 text-sm font-medium', DUE_TONE[due.tone])}>
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <time dateTime={dueAt}>{due.text}</time>
    </p>
  );
}

export interface InboxActions {
  /** Opens the Engage panel replying on a post (reply reminders). */
  readonly onReply: (postId: string | null) => void;
  /** Opens the snapshot form for a post. */
  readonly onSnapshot: (postId: string) => void;
}

/**
 * The card's one action. It covers the whole card (a stretched target), so a row is one big tap
 * target; its name repeats the title for screen readers ("Go live: Post at Tue 10:00 …").
 */
function PrimaryAction({
  item,
  actions,
  featured,
}: {
  item: TodayItem;
  actions: InboxActions;
  featured: boolean;
}) {
  const meta = kindMeta(item.kind);
  const target = itemTarget(item);
  const label = (
    <>
      {meta.action}
      <span className="sr-only">: {item.title}</span>
    </>
  );
  const className = cn(
    'after:absolute after:inset-0 after:rounded-[inherit]',
    featured ? 'w-full sm:w-auto' : 'shrink-0',
  );
  const variant = featured ? 'primary' : 'secondary';
  const size = featured ? 'lg' : 'md';
  switch (target.kind) {
    case 'engage':
      return (
        <Button
          variant={variant}
          size={size}
          className={className}
          onClick={() => {
            actions.onReply(target.postId);
          }}
        >
          {label}
        </Button>
      );
    case 'snapshot':
      return (
        <Button
          variant={variant}
          size={size}
          className={className}
          onClick={() => {
            actions.onSnapshot(target.postId);
          }}
        >
          {label}
        </Button>
      );
    case 'page':
      return (
        <Button asChild variant={variant} size={size} className={className}>
          <Link to={target.href}>{label}</Link>
        </Button>
      );
  }
}

function DismissReminder({ item, onDismissed }: { item: TodayItem; onDismissed: () => void }) {
  const client = useQueryClient();
  const reminderId = item.refs?.reminderId;
  const dismiss = useApiMutation('post', '/api/reminders/{reminder_id}/dismiss', {
    successToast: 'Reminder dismissed.',
    errorToast: 'Couldn’t dismiss the reminder.',
    invalidates: [queryKeys.today],
    onSuccess: () => {
      // Take the card away at once; the refetch confirms it.
      client.setQueryData<Today>(queryKeys.today, (today) =>
        today ? { ...today, items: today.items.filter((current) => current.id !== item.id) } : today,
      );
      onDismissed();
    },
  });
  if (!reminderId) return null;
  return (
    <Button
      variant="ghost"
      className="relative z-10 shrink-0"
      disabled={dismiss.isPending}
      onClick={() => {
        dismiss.mutate({ params: { path: { reminder_id: reminderId } } });
      }}
    >
      Dismiss
      <span className="sr-only">: {item.title}</span>
    </Button>
  );
}

function ItemCard({
  item,
  now,
  actions,
  featured,
  onDismissed,
}: {
  item: TodayItem;
  now: number;
  actions: InboxActions;
  featured: boolean;
  onDismissed: () => void;
}) {
  const meta = kindMeta(item.kind);
  const Icon = meta.icon;
  const urgent = priorityWord(item.priority);
  const header = (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-muted">
      <Icon aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
      <span className="font-medium text-ink">{meta.label}</span>
      {urgent ? (
        <Badge variant="danger">
          <AlarmClock aria-hidden="true" />
          {urgent}
        </Badge>
      ) : null}
    </p>
  );

  if (featured) {
    return (
      <li className="relative mb-6 grid gap-4 rounded-xl border border-line-strong bg-sheet p-5 transition-colors duration-150 hover:bg-wash sm:p-6">
        <div className="grid gap-2">
          <p className="text-xs font-semibold text-ink-subtle">Up next</p>
          {header}
          <h3 className="font-serif text-2xl font-medium text-ink">
            {item.title}
          </h3>
          <Due dueAt={item.dueAt} now={now} />
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <PrimaryAction item={item} actions={actions} featured />
          <DismissReminder item={item} onDismissed={onDismissed} />
        </div>
      </li>
    );
  }

  return (
    <li className="relative flex flex-col gap-3 border-b border-line py-4 transition-colors duration-150 hover:bg-wash sm:flex-row sm:items-center sm:gap-6 sm:px-3">
      <div className="grid min-w-0 flex-1 gap-1">
        {header}
        <h3 className="text-base font-semibold text-ink">
          {item.title}
        </h3>
        <Due dueAt={item.dueAt} now={now} />
      </div>
      <div className="flex items-center gap-2">
        <PrimaryAction item={item} actions={actions} featured={false} />
        <DismissReminder item={item} onDismissed={onDismissed} />
      </div>
    </li>
  );
}

function SetupList({ items }: { items: readonly TodayItem[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="today-setup" className="mt-10">
      <h3 id="today-setup" className="text-sm font-semibold text-ink">
        Finish setting up
      </h3>
      <p className="mt-1 text-sm text-ink-muted">Each one unlocks part of the engine. None is urgent.</p>
      <ul className="mt-3 border-t border-line">
        {items.map((item) => {
          const meta = kindMeta(item.kind);
          const Icon = meta.icon;
          const target = itemTarget(item);
          return (
            <li key={item.id} className="border-b border-line">
              <Link
                to={target.kind === 'page' ? target.href : '/today'}
                className="flex min-h-11 items-center gap-3 py-3 text-sm text-ink transition-colors duration-150 hover:bg-wash"
              >
                <Icon aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
                <span className="min-w-0 flex-1">{item.title}</span>
                <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-ink-subtle" />
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function InboxSkeleton() {
  return (
    <section aria-busy="true" aria-labelledby="today-inbox-loading" className="max-w-measure">
      <h2 id="today-inbox-loading" className="sr-only">
        Loading what needs you
      </h2>
      <Skeleton className="h-9 w-72 max-w-full" />
      <Skeleton className="mt-3 h-5 w-96 max-w-full" />
      <Skeleton className="mt-8 h-44 rounded-xl" />
      <Skeleton className="mt-4 h-20" />
      <Skeleton className="mt-2 h-20" />
    </section>
  );
}

interface TodayInboxProps extends InboxActions {
  today: Today | undefined;
  isPending: boolean;
  error: unknown;
  onRetry: () => void;
}

/**
 * Everything that waits on the owner, most urgent first (the server's order): the lead sentence
 * says how much and of what kind, the first item is "Up next" with the page's one primary button,
 * the rest follow as rows, and setup items close the list quietly.
 */
export function TodayInbox({ today, isPending, error, onRetry, onReply, onSnapshot }: TodayInboxProps) {
  const now = useNow();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const actions: InboxActions = { onReply, onSnapshot };

  if (isPending && !today) return <InboxSkeleton />;
  if (!today) {
    return (
      <ProblemAlert
        error={error}
        title="Couldn’t load what needs you."
        onRetry={onRetry}
        retryLabel="Try loading Today again"
        className="max-w-measure"
      />
    );
  }

  const { cards, setup } = splitInbox(today.items);
  const focusHeading = () => {
    headingRef.current?.focus();
  };

  if (cards.length === 0) {
    return (
      <>
        <EmptyState
          size="lg"
          title={headline(0)}
          yourPart="Clear this list when it fills up. Most items take a minute or two, and they work one-handed on your phone."
        >
          <p>When the engine needs a decision from you, it appears here with one clear action. There are four kinds:</p>
          <DetailList items={KINDS} className="mt-6" />
        </EmptyState>
        <div className="max-w-measure">
          <SetupList items={setup} />
        </div>
      </>
    );
  }

  const [first, ...rest] = cards;
  return (
    <section aria-labelledby="today-inbox" className="max-w-measure">
      <h2 id="today-inbox" ref={headingRef} tabIndex={-1} className="font-serif text-3xl font-medium text-ink outline-none">
        {headline(cards.length)}
      </h2>
      <p className="mt-2 text-lg text-ink-muted">{groupSummary(cards)}</p>
      <ol aria-label="What needs you, most urgent first" className="mt-8 grid [&>li:nth-child(2)]:border-t">
        {first ? <ItemCard item={first} now={now} actions={actions} featured onDismissed={focusHeading} /> : null}
        {rest.map((item) => (
          <ItemCard key={item.id} item={item} now={now} actions={actions} featured={false} onDismissed={focusHeading} />
        ))}
      </ol>
      <SetupList items={setup} />
    </section>
  );
}
