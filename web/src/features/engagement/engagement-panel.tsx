import { MessageSquareReply } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { keptWords } from '@/features/today/lib/time';
import type { EngageRequest } from '@/features/today/lib/use-today-sheets';
import { useNow } from '@/lib/use-now';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/media';

import { useOpenItems } from './lib/queries';
import { PostedRecords } from './posted-records';
import { EMPTY_PREFILL } from './reply-composer';
import { ReplyHelper, type ReplyView } from './reply-helper';
import { WatchlistSession } from './watchlist-session';

type EngageTab = 'reply' | 'session' | 'posted';

function isEngageTab(value: string): value is EngageTab {
  return value === 'reply' || value === 'session' || value === 'posted';
}

function viewFor(request: EngageRequest): ReplyView {
  if (request.kind === 'share') {
    return {
      name: 'compose',
      key: request.serial,
      prefill: { ...EMPTY_PREFILL, itemKind: 'comment', text: request.text, url: request.url, shared: true },
    };
  }
  return {
    name: 'compose',
    key: request.serial,
    prefill: { ...EMPTY_PREFILL, itemKind: 'reply', postId: request.postId },
  };
}

/** "2 waiting for a reply. The oldest is deleted in 5 h." under the section heading. */
function WaitingSummary() {
  const items = useOpenItems();
  const now = useNow();
  const waiting = items.data ?? [];
  if (waiting.length === 0) return null;
  const soonest = [...waiting].sort((a, b) => a.item.expiresAt.localeCompare(b.item.expiresAt))[0];
  return (
    <p className="mt-4 text-sm text-ink">
      <span className="font-semibold">
        {waiting.length === 1 ? '1 waiting for a reply.' : `${String(waiting.length)} waiting for a reply.`}
      </span>{' '}
      <span className="text-ink-muted">
        {soonest ? `The next one is ${keptWords(soonest.item.expiresAt, now).toLowerCase()}.` : null}
      </span>
    </p>
  );
}

export interface EngagementPanelProps {
  /** Controlled open state (Today opens it from reminders and the share target). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** What opened it: a share (check and save other members' text) or a reply reminder (a post). */
  request?: EngageRequest | null;
}

/**
 * Today > Engage (a sheet on Today, phone-first): the reply and comment helper (paste, read a
 * screenshot or take a share, angles, draft, live check, copy, "I posted it"), the daily watchlist
 * session and what the owner posted. Nothing is automated on LinkedIn: the owner posts by hand.
 * Other members' text is kept by the server for 48 hours only; before the owner saves it, it lives
 * in component state. Calls `/api/engagement/items` (+ `angles`, `lint`, `finalize`, `dismiss`),
 * `/api/engagement/extract`, `/api/engagement/watchlist` (+ `engaged`), `/api/engagement/records`.
 */
export function EngagementPanel({ open: openProp, onOpenChange, request = null }: EngagementPanelProps) {
  const isDesktop = useMediaQuery(DESKTOP_QUERY);
  const [openState, setOpenState] = useState(false);
  const [tab, setTab] = useState<EngageTab>('reply');
  const [view, setView] = useState<ReplyView>({ name: 'list' });
  const [drafts, setDrafts] = useState<Readonly<Record<string, string>>>({});
  const [handled, setHandled] = useState<number | null>(null);
  const open = openProp ?? openState;

  if (request !== null && request.serial !== handled) {
    // A new request (share, reminder) starts a fresh reply; drafts in progress stay.
    setHandled(request.serial);
    setTab('reply');
    setView(viewFor(request));
  }

  function setOpen(next: boolean) {
    if (openProp === undefined) setOpenState(next);
    onOpenChange?.(next);
  }

  return (
    <section aria-labelledby="engage" className="mt-16 max-w-measure md:mt-20">
      <h2 id="engage" className="font-serif text-2xl font-medium text-ink">
        Replies and comments
      </h2>
      <p className="mt-2 text-ink-muted">
        Paste a comment or share a screenshot to get angles for a reply only you could write, then spend 15 minutes
        with your watchlist. Nothing is posted for you.
      </p>
      <WaitingSummary />
      <Button
        variant="secondary"
        className="mt-6"
        onClick={() => {
          setOpen(true);
        }}
      >
        <MessageSquareReply aria-hidden="true" />
        Open the reply helper
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side={isDesktop ? 'right' : 'bottom'}
          className="overflow-y-auto md:w-[min(34rem,92vw)]"
        >
          <SheetHeader>
            <SheetTitle>Reply helper</SheetTitle>
            <SheetDescription>
              Other people’s text is deleted after 48 hours. Only your own words and the link are kept.
            </SheetDescription>
          </SheetHeader>
          <Tabs
            value={tab}
            onValueChange={(value) => {
              if (isEngageTab(value)) setTab(value);
            }}
            className="gap-5"
          >
            <TabsList aria-label="Reply helper">
              <TabsTrigger value="reply">Reply</TabsTrigger>
              <TabsTrigger value="session">Daily session</TabsTrigger>
              <TabsTrigger value="posted">Posted</TabsTrigger>
            </TabsList>
            <TabsContent value="reply">
              <ReplyHelper
                view={view}
                onViewChange={setView}
                drafts={drafts}
                onDraftChange={(itemId, text) => {
                  setDrafts((current) =>
                    text === null
                      ? Object.fromEntries(Object.entries(current).filter(([id]) => id !== itemId))
                      : { ...current, [itemId]: text },
                  );
                }}
              />
            </TabsContent>
            <TabsContent value="session">
              <WatchlistSession />
            </TabsContent>
            <TabsContent value="posted">
              <PostedRecords />
            </TabsContent>
          </Tabs>
        </SheetContent>
      </Sheet>
    </section>
  );
}
